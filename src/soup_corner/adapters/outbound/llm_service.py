import logging
import os
from pathlib import Path

from langchain_community.document_loaders import PyPDFLoader
from langchain_community.vectorstores import FAISS
from langchain_core.chat_history import BaseChatMessageHistory, InMemoryChatMessageHistory
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables.history import RunnableWithMessageHistory
from langchain_ollama import ChatOllama, OllamaEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pydantic import ValidationError
from soup_corner.domain.model.address_extraction import AddressExtraction
from soup_corner.domain.model.order_extraction import OrderExtraction

# Quantidade máxima de mensagens mantidas no histórico de cada conversa
MAX_HISTORY_MESSAGES = 10

logger = logging.getLogger(__name__)


class LLMService:
    def __init__(self):
        self.pdf = os.getenv("PDF_PATH")
        self.agent = os.getenv("AGENT_PATH")
        self.llm_model = os.getenv("LLM_MODEL")
        self.embedding = os.getenv("LLM_EMBEDDING")
        self.address_extraction_agent = os.getenv("ADDRESS_EXTRACTION_AGENT_PATH")

        if not self.pdf or not self.llm_model or not self.embedding:
            raise ValueError("Defina PDF_PATH, LLM_MODEL e LLM_EMBEDDING no arquivo .env")

        system_prompt = Path(self.agent).read_text(encoding="utf-8") if self.agent else ""

        # Indexa o cardápio uma única vez, na inicialização
        loader = PyPDFLoader(self.pdf)
        documents = loader.load()
        text_splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=100)
        text_chunks = text_splitter.split_documents(documents)
        embeddings_local = OllamaEmbeddings(model=self.embedding)
        vector_store = FAISS.from_documents(text_chunks, embeddings_local)
        self.retriever = vector_store.as_retriever(search_kwargs={"k": 4})

        # format="json" em vez de with_structured_output: o RunnableWithMessageHistory só aceita
        # mensagens como saída. O JSON (com order/amount) fica no histórico e é convertido depois
        self.llm = ChatOllama(model=self.llm_model, temperature=0.2, format="json")
        template = ChatPromptTemplate.from_messages([
            ("system", system_prompt + "\n\nCardápio:\n{context}"),
            MessagesPlaceholder(variable_name="history"),
            ("human", "{question}"),
        ])
        chain = template | self.llm

        # Extração de endereço com saída estruturada (temperatura 0 para ser determinística).
        # method="json_mode": o formato do JSON já está descrito no prompt; com o padrão (json_schema)
        # o modelo local deixava street/number/neighborhood em branco
        self.address_extraction_prompt = (
            Path(self.address_extraction_agent).read_text(encoding="utf-8")
            if self.address_extraction_agent else ""
        )
        self.address_llm = ChatOllama(model=self.llm_model, temperature=0).with_structured_output(
            AddressExtraction, method="json_mode"
        )

        # Histórico em memória, um por conversa (perdido ao reiniciar o bot)
        self.store: dict[str, InMemoryChatMessageHistory] = {}

        self.chain_with_history = RunnableWithMessageHistory(
            chain,
            self.get_session_history,
            input_messages_key="question",
            history_messages_key="history",
        )

    def talk_message_agent(self, prompt: str, session_id: str) -> OrderExtraction:
        docs = self.retriever.invoke(prompt)
        context = "\n\n".join(doc.page_content for doc in docs)
        response = self.chain_with_history.invoke(
            {"context": context, "question": prompt},
            config={"configurable": {"session_id": session_id}},
        )

        # Limita o tamanho do histórico para não estourar o contexto do modelo
        history = self.store[session_id]
        history.messages = history.messages[-MAX_HISTORY_MESSAGES:]

        try:
            return OrderExtraction.model_validate_json(response.content)
        except ValidationError:
            # JSON inválido: envia o texto bruto para o cliente não ficar sem resposta
            logger.warning("Resposta do LLM fora do formato JSON | sessão=%s", session_id)
            return OrderExtraction(message=response.content)

    def clear_session(self, session_id: str) -> None:
        """Descarta o histórico da conversa (usado após finalizar um pedido)."""
        self.store.pop(session_id, None)

    def get_session_history(self, session_id: str) -> BaseChatMessageHistory:
        if session_id not in self.store:
            self.store[session_id] = InMemoryChatMessageHistory()
        return self.store[session_id]

    def get_address_extraction(self, message: str) -> AddressExtraction:
        # Mensagens montadas diretamente (sem ChatPromptTemplate): o prompt contém "{" e "}" nos exemplos de JSON
        messages = [
            SystemMessage(content=self.address_extraction_prompt),
            HumanMessage(content=message),
        ]
        return self.address_llm.invoke(messages)

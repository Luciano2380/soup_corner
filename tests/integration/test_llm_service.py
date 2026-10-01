"""Testes integrados do LLMService com PDF, FAISS e Ollama reais.

Os testes marcados com `ollama` são pulados se o Ollama não estiver rodando com os modelos
do `.env`. As respostas do LLM não são determinísticas: as asserções verificam estrutura e
comportamento, não o texto exato.
"""
import uuid

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from soup_corner.domain.model.address_extraction import AddressExtraction
from soup_corner.domain.model.order_extraction import OrderExtraction
from soup_corner.adapters.outbound.llm_service import MAX_HISTORY_MESSAGES, LLMService


def new_session() -> str:
    # O LLMService é compartilhado entre os testes: cada teste usa uma sessão própria
    return f"teste-{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# Configuração (não precisa do Ollama)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("variable", ["PDF_PATH", "LLM_MODEL", "LLM_EMBEDDING"])
def test_init_requires_mandatory_variables(monkeypatch, variable):
    monkeypatch.setenv("PDF_PATH", "resources/pdf/soup_corner.pdf")
    monkeypatch.setenv("LLM_MODEL", "modelo")
    monkeypatch.setenv("LLM_EMBEDDING", "embedding")
    monkeypatch.delenv(variable)

    with pytest.raises(ValueError, match="PDF_PATH, LLM_MODEL e LLM_EMBEDDING"):
        LLMService()


# ---------------------------------------------------------------------------
# RAG: PDF → chunks → embeddings → FAISS
# ---------------------------------------------------------------------------

@pytest.mark.ollama
def test_retriever_finds_menu_chunks(llm_service):
    docs = llm_service.retriever.invoke("Quais caldos vocês têm e quanto custam?")

    assert 1 <= len(docs) <= 4
    assert all(doc.page_content.strip() for doc in docs)
    text = " ".join(doc.page_content for doc in docs).lower()
    assert "caldo" in text
    assert "r$" in text


# ---------------------------------------------------------------------------
# Extração de endereço
# ---------------------------------------------------------------------------

@pytest.mark.ollama
def test_extracts_address_from_message(llm_service):
    address = llm_service.get_address_extraction(
        "Pode entregar na Rua das Flores, 100, apto 302, bairro Centro, Belo Horizonte MG"
    )

    assert isinstance(address, AddressExtraction)
    assert address.is_address is True
    assert "flores" in (address.street or "").lower()
    assert address.number == "100"
    assert "centro" in (address.neighborhood or "").lower()
    assert "Rua das Flores" in address.to_address_string() or "R. das Flores" in address.to_address_string()


@pytest.mark.ollama
def test_message_without_address(llm_service):
    address = llm_service.get_address_extraction("Quais caldos vocês têm hoje?")

    assert isinstance(address, AddressExtraction)
    assert address.is_address is False


# ---------------------------------------------------------------------------
# Conversa com histórico
# ---------------------------------------------------------------------------

@pytest.mark.ollama
def test_conversation_returns_order_extraction(llm_service):
    session = new_session()

    response = llm_service.talk_message_agent("Quais caldos vocês têm?", session)

    assert isinstance(response, OrderExtraction)
    assert response.message and response.message.strip()
    assert response.completion_status is False

    history = llm_service.store[session].messages
    assert len(history) == 2
    assert isinstance(history[0], HumanMessage)
    assert history[0].content == "Quais caldos vocês têm?"
    assert isinstance(history[1], AIMessage)


@pytest.mark.ollama
def test_history_limited_to_max_messages(llm_service):
    session = new_session()
    history = llm_service.get_session_history(session)
    for i in range(MAX_HISTORY_MESSAGES):
        history.add_message(HumanMessage(content=f"mensagem antiga {i}"))

    llm_service.talk_message_agent("Qual o horário de funcionamento?", session)

    messages = llm_service.store[session].messages
    assert len(messages) == MAX_HISTORY_MESSAGES
    # As mais antigas são descartadas; a última troca é mantida
    assert messages[-2].content == "Qual o horário de funcionamento?"
    assert "mensagem antiga 0" not in [m.content for m in messages]


@pytest.mark.ollama
def test_sessions_are_isolated_and_clear_session_discards(llm_service):
    session_a, session_b = new_session(), new_session()

    llm_service.talk_message_agent("Oi, meu nome é Joana.", session_a)

    assert session_a in llm_service.store
    assert session_b not in llm_service.store

    llm_service.clear_session(session_a)
    assert session_a not in llm_service.store
    # Descartar sessão inexistente não é erro
    llm_service.clear_session(session_b)


@pytest.mark.ollama
def test_non_json_response_becomes_message(llm_service, monkeypatch):
    session = new_session()
    llm_service.get_session_history(session)

    class FreeTextChain:
        def invoke(self, *args, **kwargs):
            return AIMessage(content="Olá! Temos caldo verde e galinhada.")

    monkeypatch.setattr(llm_service, "chain_with_history", FreeTextChain())

    response = llm_service.talk_message_agent("Oi", session)

    assert response == OrderExtraction(message="Olá! Temos caldo verde e galinhada.")

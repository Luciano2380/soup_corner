# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Visão geral

Bot de Telegram em Python ("Anna") que atende clientes do delivery Soup Corner / Cantinho do Caldo. Responde perguntas sobre o cardápio usando RAG sobre um PDF, com LLM local via Ollama. Código, comentários, mensagens de erro e respostas do bot são em português.

> O `~/CLAUDE.md` (projeto Spring Boot/Java) **não se aplica** a este repositório — ignore-o aqui.

## Comandos

Sempre executar a partir da **raiz do projeto**: os caminhos do `.env` (`PDF_PATH`, `AGENT_PATH`, `ADDRESS_EXTRACTION_AGENT_PATH`, `HTML_TABLE_ORI_PATH`, `HTML_TABLE_DEST_PATH`) são relativos ao diretório de trabalho.

```bash
# Rodar o bot (Ctrl+C encerra)
python3 src/main.py

# Testar só o LLM/RAG, sem Telegram
PYTHONPATH=src python3 -c "from dotenv import load_dotenv; load_dotenv(); from soup_corner.adapters.outbound.llm_service import LLMService; s = LLMService(); print(s.talk_message_agent('Quais caldos vocês têm?', 'teste').model_dump())"

# Checagem rápida de sintaxe
python3 -m py_compile src/main.py $(find src/soup_corner -name "*.py")
```

```bash
# Testes integrados (tests/integration/, config em pytest.ini)
python3 -m pytest -m "not ollama"          # rápidos (~1s), sem Ollama
python3 -m pytest                          # inclui os que usam o Ollama real (~1 min)
RUN_GOOGLE_MAPS_TESTS=1 python3 -m pytest -m google_maps   # Routes API real (cobrada)
```

Nos testes, a Routes API é simulada por um adapter HTTP montado na `requests.Session` do `MapsService`, o Telegram por `Update`/`Context` falsos e o painel é gerado em `tmp_path`. Testes `ollama` são pulados se o Ollama ou os modelos do `.env` não estiverem disponíveis.

Não há linter, `requirements.txt` nem `pyproject.toml`. Os pacotes estão instalados no Python do sistema (`~/.local`, Python 3.10): `python-telegram-bot`, `python-dotenv`, `langchain-community`, `langchain-core`, `langchain-ollama`, `langchain-text-splitters`, `faiss-cpu`, `pypdf`, `requests`, `pytest`, `pytest-asyncio`.

Pré-requisito: Ollama rodando com os modelos de `LLM_MODEL` e `LLM_EMBEDDING` baixados (`ollama pull <modelo>`); caso contrário `LLMService()` falha na inicialização com erro 404.

## Arquitetura

`src/main.py` carrega o `.env` (`load_dotenv`) e inicia `TelegramService`. Os imports usam `soup_corner.…`, que resolve porque `src/` é o diretório do script (e o `pythonpath` do `pytest.ini`).

Camadas em `src/soup_corner/`: `domain/model/` (modelos pydantic, sem dependências externas), `adapters/inbound/` (Telegram) e `adapters/outbound/` (Ollama/LLM e Google Maps). Arquivos estáticos ficam em `resources/` (`prompts/`, `html/`, `pdf/`); os painéis gerados em execução ficam em `data/pages/` (fora do git).

- **`TelegramService`** (`adapters/inbound/telegram_service.py`): cria **um único** `LLMService` no `__init__` e o reutiliza em todas as mensagens. O handler é `async`, mas `talk_message_agent` é síncrono e lento, por isso é chamado via `asyncio.to_thread` — manter assim para não travar o event loop. O `session_id` do histórico é o id do usuário/chat do Telegram.
- **`LLMService`** (`adapters/outbound/llm_service.py`): no `__init__` (uma vez só) carrega o PDF, divide em chunks, gera embeddings e monta um índice FAISS em memória. A cada mensagem: recupera os trechos relevantes → injeta no system prompt como `{context}` → chama o LLM com o histórico da sessão (`RunnableWithMessageHistory`, chaves `question`/`history`).
  - O system prompt é o conteúdo de `AGENT_PATH` (`resources/prompts/principal_agent.md`) + os trechos do cardápio. Chaves `{`/`}` nesse arquivo quebram o `ChatPromptTemplate`.
  - Histórico fica em memória (`self.store`), limitado a `MAX_HISTORY_MESSAGES`, e é perdido ao reiniciar.
  - `RunnableWithMessageHistory`/`InMemoryChatMessageHistory` emitem aviso de depreciação (LangChain recomenda LangGraph); funcionam por enquanto.
- **`MapsService`** (`adapters/outbound/maps_service.py`): `calculate_distance(AddressExtraction)` calcula a distância de carro (Google Routes API, via `requests`) de `SOURCE_ADDRESS`/`STATE_CITY` até o endereço e aplica as faixas de `DELIVERY_FEES`, que **duplicam** a tabela de taxa de entrega do cardápio (PDF): se mudar lá, mude aqui. Retorna `(entrega_atendida, texto)`: se atendida, o texto é anexado à pergunta enviada ao LLM; senão, é enviado direto ao cliente. Distâncias ficam em `lru_cache` por endereço, porque cada chamada à API é cobrada.
- **Resposta em JSON**: o LLM principal roda com `format="json"` e o prompt (`resources/prompts/principal_agent.md`) exige o formato de `OrderExtraction` (`message`, `completion_status`, `client`, `order`, `amount`, `address`). `talk_message_agent` converte o JSON (se vier inválido, o texto bruto vira `message`). Não use `with_structured_output` nessa chain: o `RunnableWithMessageHistory` só aceita mensagens como saída. Chaves literais no prompt devem ser escritas `{{`/`}}`.
- **Painel de pedidos**: ao iniciar, `TelegramService.new_file` copia `HTML_TABLE_ORI_PATH` (`resources/html/index.html`) para `HTML_TABLE_DEST_PATH/index_AAAAMMDD.html`, reaproveitando o do dia se já existir. Quando `completion_status` é `true`, `_register_order` insere um `<tr>` em `<tbody id="ordersTableBody">` e o histórico da sessão é descartado (`clear_session`).
- A data da mensagem é convertida para `America/Sao_Paulo` e enviada ao LLM com o dia da semana, para que ele verifique o horário de funcionamento.

## Configuração

Toda a configuração vem do `.env` (fora do controle de versão via `.gitignore`). Cada serviço lê suas variáveis no `__init__` e lança `ValueError` se faltar alguma obrigatória. Ao adicionar uma variável nova no código, adicione-a também no `.env` e no `.env.example` (versionado, só com valores fictícios).

`LOG_LEVEL` (padrão `INFO`) configura o `logging` em `src/main.py`. Em `INFO`, `message_reply` registra recebimento, duração de cada etapa (extração de endereço, cálculo de distância e resposta), endereço identificado e tempo total; em `DEBUG`, também o conteúdo das mensagens. O logger `httpx` fica em `WARNING` porque em `INFO` ele expõe o token do bot nas URLs.

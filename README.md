# Soup Corner

Bot de Telegram para atendimento do delivery **Soup Corner / Cantinho do Caldo**. A assistente virtual **Anna** conduz o cliente do cardápio até o pedido finalizado: responde sobre pratos, preços, horário e pagamento, monta o pedido, valida o endereço de entrega, calcula a taxa pela distância e registra o pedido concluído em um painel HTML.

As respostas são geradas por um LLM rodando localmente no [Ollama](https://ollama.com), com RAG (*Retrieval-Augmented Generation*): o cardápio em PDF é indexado em um banco vetorial e os trechos relevantes são enviados ao modelo a cada mensagem. A distância de entrega é calculada pela [Google Routes API](https://developers.google.com/maps/documentation/routes).

## Funcionalidades

- Respostas em linguagem natural baseadas **apenas** no cardápio (o modelo é instruído a não inventar pratos, preços ou taxas).
- Histórico de conversa por usuário: o bot entende perguntas de continuação (ex.: "Quanto custa a galinhada grande?" → "E a pequena?").
- Fluxo completo de pedido: itens e total → endereço → taxa de entrega → nome → resumo → pagamento via Pix → finalização (ou cancelamento, se o cliente não confirmar o pagamento).
- Extração automática de endereço: cada mensagem passa por um segundo agente que identifica rua, número, bairro etc. Se essa extração falhar, a mensagem segue normalmente para a atendente.
- Cálculo da distância de carro até o endereço (Google Routes API) e da taxa de entrega por faixa:

  | Distância | Taxa |
  |---|---|
  | até 1 km | grátis |
  | até 2,5 km | R$ 8,00 |
  | até 4 km | R$ 15,00 |
  | acima de 4 km | fora da área de entrega |

- Painel de pedidos do dia (`data/pages/index_AAAAMMDD.html`), recarregado automaticamente no navegador a cada segundo, com cliente, pedido, valor e endereço. Se o bot for reiniciado no mesmo dia, o painel existente é reaproveitado.
- Data e hora da mensagem convertidas para o fuso de São Paulo e enviadas ao LLM (para verificar o horário de funcionamento).
- Indicador "digitando..." enquanto a resposta é gerada e divisão de respostas longas no limite de 4096 caracteres do Telegram.
- Logs com a duração de cada etapa (extração de endereço, distância, resposta); em `DEBUG`, também o conteúdo das mensagens.
- Comando `/start` com mensagem de boas-vindas.

## Tecnologias

- Python 3.10+
- [python-telegram-bot](https://python-telegram-bot.org)
- [LangChain](https://python.langchain.com) (`langchain-core`, `langchain-community`, `langchain-ollama`, `langchain-text-splitters`)
- [FAISS](https://github.com/facebookresearch/faiss) — índice vetorial em memória
- [Ollama](https://ollama.com) — LLM e embeddings locais
- [Google Routes API](https://developers.google.com/maps/documentation/routes) — distância de entrega (via `requests`)
- `pypdf`, `python-dotenv`
- `pytest`, `pytest-asyncio` — testes integrados

## Rodando localmente — passo a passo

Testado com Python 3.10 e Ollama 0.34 no Linux. Todos os comandos são executados **a partir da raiz do projeto**, porque os caminhos do `.env` são relativos a ela.

### 1. Instale o Ollama e baixe os modelos

Instale o Ollama seguindo [ollama.com/download](https://ollama.com/download). No Linux:

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

Com o Ollama em execução, baixe o modelo de chat e o de embeddings:

```bash
ollama pull qwen2.5-coder:7b     # LLM_MODEL
ollama pull nomic-embed-text     # LLM_EMBEDDING
ollama list                      # os dois modelos devem aparecer
```

> O modelo de chat pode ser trocado por outro do Ollama (ex.: `qwen2.5:7b`), desde que o `LLM_MODEL` do `.env` seja atualizado. Ele precisa gerar JSON de forma confiável: tanto a resposta principal quanto a extração de endereço usam saída em JSON.

### 2. Crie o bot no Telegram

1. No Telegram, abra o [@BotFather](https://t.me/BotFather) e envie `/newbot`.
2. Escolha um nome e um usuário para o bot (o usuário precisa terminar em `bot`).
3. Copie o token informado — ele será usado no passo 6.

### 3. Crie a chave do Google Maps

1. No [Google Cloud Console](https://console.cloud.google.com/), crie (ou escolha) um projeto com faturamento ativo.
2. Habilite a **Routes API** em *APIs e serviços → Biblioteca*.
3. Em *APIs e serviços → Credenciais*, crie uma chave de API e, de preferência, restrinja-a à Routes API.

> Cada cálculo de distância é uma chamada cobrada. O bot pede só a distância (SKU mais barato) e guarda em cache os endereços já consultados enquanto está rodando.

### 4. Crie um ambiente virtual (recomendado)

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
```

### 5. Instale as dependências

```bash
pip install python-telegram-bot python-dotenv requests langchain-core langchain-community \
    langchain-ollama langchain-text-splitters faiss-cpu pypdf

# Para rodar os testes
pip install pytest pytest-asyncio
```

### 6. Configure o `.env`

O repositório traz o `.env.example`, com **dados fictícios**. Copie-o para `.env`, que está no `.gitignore` e nunca deve ser versionado, porque vai guardar seus tokens:

```bash
cp .env.example .env
```

Depois, troque no `.env` os valores fictícios pelos reais:

| Variável | Valor fictício (`.env.example`) | Troque por |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | `seu-token-do-botfather` | Token do seu bot (passo 2) |
| `GOOGLE_MAPS_API_KEY` | `sua-chave-google-maps` | Sua chave com a Routes API habilitada (passo 3) |
| `SOURCE_ADDRESS` | `Rua do Restaurante, 123` | Endereço real do restaurante: rua e número (ex.: `Rua das Acácias, 250`) |
| `STATE_CITY` | `Cidade Exemplo, Estado Exemplo` | Cidade e estado reais do restaurante (ex.: `Belo Horizonte, Minas Gerais`) |

As demais variáveis já vêm com valores que funcionam e só precisam mudar se você trocar o modelo do Ollama, mover arquivos ou quiser outro nível de log.

> Com o endereço fictício o bot até inicia, mas o cálculo de entrega não funciona: a Routes API não encontra rota a partir de um endereço que não existe, e a Anna responde "Não consegui localizar o endereço..." para todo cliente. Use sempre o endereço real do restaurante ao rodar o bot ou o teste com a API real.

Além do `.env`, revise também, antes de atender clientes de verdade:

| Arquivo | Dado fictício / de teste | O que fazer |
|---|---|---|
| `resources/pdf/soup_corner.pdf` | Chave Pix `X7m9K2vP4wL1qN8sJ5zR` (fictícia) | Gere o PDF com a chave Pix real; senão os clientes recebem uma chave inválida para pagar. Confira também preços, horário e taxas. |
| `resources/prompts/principal_agent.md` | Linha "Mensagem enviada em: ..." marcada como `[MODO DE TESTE]` | Remova o modo de teste para a Anna respeitar o horário de funcionamento do cardápio. |
| `adapters/outbound/maps_service.py` | `DELIVERY_FEES` | Só se você mudar as faixas de taxa no PDF: as duas tabelas precisam ser iguais. |

Os testes automatizados (`tests/integration/`) usam seus próprios dados fictícios e simulam a Routes API, então **não** precisam ser alterados.

Referência das variáveis:

| Variável | Obrigatória | Descrição |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Sim | Token do bot no Telegram |
| `GOOGLE_MAPS_API_KEY` | Sim | Chave com a Routes API habilitada |
| `SOURCE_ADDRESS` | Sim | Endereço do restaurante (origem das entregas) |
| `STATE_CITY` | Sim | Cidade e estado do restaurante; também completa endereços informados sem cidade |
| `LLM_MODEL` | Sim | Modelo de chat do Ollama |
| `LLM_EMBEDDING` | Sim | Modelo de embeddings do Ollama |
| `PDF_PATH` | Sim | Cardápio em PDF usado como base de conhecimento |
| `HTML_TABLE_ORI_PATH` | Sim | Modelo do painel de pedidos |
| `HTML_TABLE_DEST_PATH` | Sim | Pasta onde o painel do dia (`index_AAAAMMDD.html`) é criado |
| `AGENT_PATH` | Não* | Prompt de sistema da atendente (personalidade, regras e formato JSON) |
| `ADDRESS_EXTRACTION_AGENT_PATH` | Não* | Prompt do agente de extração de endereço |
| `LOG_LEVEL` | Não | Nível de log (`DEBUG`, `INFO`, `WARNING`...). Padrão: `INFO` |

\* O código não exige essas variáveis, mas sem elas os agentes ficam sem instruções e o fluxo de pedido deixa de funcionar corretamente.

### 7. Teste o LLM sem o Telegram (opcional)

Valida Ollama, modelos, PDF e `.env` antes de subir o bot:

```bash
PYTHONPATH=src python3 -c "from dotenv import load_dotenv; load_dotenv(); \
from soup_corner.adapters.outbound.llm_service import LLMService; \
print(LLMService().talk_message_agent('Quais caldos vocês têm?', 'teste').model_dump())"
```

A saída é um dicionário com os campos `message`, `completion_status`, `client`, `order`, `amount` e `address`; o `message` deve citar itens do cardápio (ex.: Caldo de Mandioca, R$ 16,90 / R$ 28,90).

### 8. Rode o bot

```bash
python3 src/main.py
```

Na inicialização o bot indexa o cardápio, o que leva alguns segundos, e cria (ou reaproveita) o painel do dia em `HTML_TABLE_DEST_PATH`. Quando aparecer `Bot iniciado com sucesso...`, abra o bot no Telegram, envie `/start` e converse com a Anna. Para acompanhar os pedidos, abra `data/pages/index_AAAAMMDD.html` no navegador. Para encerrar, use `Ctrl+C`.

### Problemas comuns

| Erro | Causa / solução |
|---|---|
| `model "nomic-embed-text" not found` | Modelo não baixado: `ollama pull nomic-embed-text` (vale o mesmo para o `LLM_MODEL`). |
| `Connection refused` ao chamar o Ollama | Ollama não está rodando: inicie com `ollama serve`. |
| `` `pypdf` package not found `` | `pip install pypdf`. |
| `ValueError: Defina ... no arquivo .env` | Variável obrigatória ausente no `.env`. |
| `FileNotFoundError` do PDF, dos prompts ou do painel | O comando não foi executado na raiz do projeto, ou o caminho no `.env` está errado. |
| `InvalidToken` / `Unauthorized` do Telegram | Token incorreto ou revogado: gere outro no @BotFather. |
| `Erro na Routes API \| status=403` no log | Chave inválida, Routes API não habilitada ou projeto sem faturamento. |
| Bot responde "Não consegui localizar o endereço..." | A Routes API não encontrou rota: o cliente deve conferir rua, número e bairro. |
| Bot responde "Desculpe, não consegui responder agora" | Erro ao gerar a resposta; o detalhe aparece no terminal onde o bot está rodando. |

## Testes

Os testes integrados ficam em `tests/integration/` (configuração em `pytest.ini`), um arquivo por serviço: `test_telegram_service.py`, `test_llm_service.py` e `test_maps_service.py`, com fixtures compartilhadas em `conftest.py`:

```bash
python3 -m pytest -m "not ollama"                          # rápidos (~1s), sem Ollama
python3 -m pytest                                          # suíte completa (~1-2 min)
RUN_GOOGLE_MAPS_TESTS=1 python3 -m pytest -m google_maps   # Routes API real (cobrada)
```

Os testes `ollama` leem `LLM_MODEL` e `LLM_EMBEDDING` do `.env`. O teste `google_maps` usa `GOOGLE_MAPS_API_KEY`, `SOURCE_ADDRESS` e `STATE_CITY` do `.env` e calcula a rota do restaurante até ele mesmo (espera frete grátis). Por isso ele **só passa com a chave e o endereço reais**; com os dados fictícios do `.env.example`, falha.

- **Routes API:** simulada por um adapter HTTP montado na `requests.Session` do `MapsService`, o que exercita cabeçalhos, corpo da requisição, erros HTTP e cache sem custo. O teste com a API real só roda com `RUN_GOOGLE_MAPS_TESTS=1`.
- **Ollama:** os testes marcados com `ollama` usam PDF, FAISS e LLM reais, e são pulados automaticamente se o Ollama ou os modelos do `.env` não estiverem disponíveis.
- **Telegram:** `Update`/`Context` falsos; o painel é gerado em diretório temporário, sem alterar `data/pages/`.

## Prompt para rodar o projeto com um assistente de IA

Para configurar e rodar o projeto com a ajuda de um agente de código (Claude Code, Cursor, Copilot etc.), abra o agente na raiz do projeto e cole o prompt abaixo:

```text
Você está na raiz do projeto Soup Corner, um bot de Telegram em Python que atende pedidos
de delivery usando RAG (LangChain + FAISS) sobre um cardápio em PDF, um LLM local no Ollama
e a Google Routes API para a taxa de entrega. Leia o README.md e o CLAUDE.md antes de começar.

Objetivo: deixar o projeto rodando localmente. Siga estes passos, verificando cada um
antes de avançar, e me informe o resultado ao final:

1. Verifique se Python 3.10+ e o Ollama estão instalados (`python3 --version`,
   `ollama --version`) e se o Ollama está em execução (`ollama list`).
   Se algo faltar, me diga como instalar em vez de instalar por conta própria.
2. Verifique se os modelos definidos em LLM_MODEL e LLM_EMBEDDING (padrão:
   qwen2.5-coder:7b e nomic-embed-text) estão baixados; se não, rode `ollama pull`.
3. Crie um ambiente virtual em .venv, ative-o e instale as dependências listadas
   no README (incluindo pytest e pytest-asyncio).
4. Verifique se existe um arquivo .env na raiz com todas as variáveis obrigatórias
   listadas no README. Se não existir, crie-o com `cp .env.example .env`.
   NUNCA invente, exiba ou copie para outro arquivo os valores de TELEGRAM_BOT_TOKEN
   e GOOGLE_MAPS_API_KEY: se estiverem ausentes, peça que eu os preencha.
   Se SOURCE_ADDRESS e STATE_CITY ainda tiverem os valores fictícios do .env.example
   ("Rua do Restaurante, 123" e "Cidade Exemplo, Estado Exemplo"), peça que eu informe
   o endereço real do restaurante.
5. Rode `python3 -m pytest` e confirme que todos os testes passam (o teste da
   Routes API real deve aparecer como pulado; não o habilite, pois é cobrado).
6. Rode o teste do LLM sem o Telegram (passo 7 do README) e confirme que a resposta
   cita itens reais do cardápio.
7. Rode o bot com `python3 src/main.py` a partir da raiz, em segundo plano, e confirme
   que o terminal mostra "Bot iniciado com sucesso...". Então me peça para enviar
   /start e uma pergunta ao bot no Telegram, e verifique a saída em busca de erros.

Regras:
- Execute todos os comandos a partir da raiz do projeto.
- Não altere o código-fonte para contornar erros de ambiente; se encontrar um bug no
  código, descreva-o e pergunte antes de corrigir.
- Se algum passo falhar, consulte a tabela "Problemas comuns" do README, explique a
  causa e proponha a correção.
```

## Estrutura do projeto

```
soup_corner/
├── .env.example                            # Modelo do .env com dados fictícios (copie para .env)
├── pytest.ini                              # Configuração dos testes
├── data/pages/                             # Painéis gerados por dia (index_AAAAMMDD.html), fora do git
├── resources/
│   ├── prompts/
│   │   ├── principal_agent.md              # Prompt da atendente (fluxo do pedido + formato JSON)
│   │   └── address_extraction_agent.md     # Prompt do agente de extração de endereço
│   ├── html/index.html                     # Modelo do painel de pedidos
│   └── pdf/soup_corner.pdf                 # Cardápio usado pelo RAG
├── src/
│   ├── main.py                             # Ponto de entrada (carrega .env e configura logs)
│   └── soup_corner/
│       ├── domain/model/
│       │   ├── address_extraction.py       # Endereço extraído da mensagem
│       │   └── order_extraction.py         # Resposta do LLM: mensagem + dados do pedido
│       └── adapters/
│           ├── inbound/
│           │   └── telegram_service.py     # Telegram, orquestração e painel de pedidos
│           └── outbound/
│               ├── llm_service.py          # RAG + LLM + histórico + extração de endereço
│               └── maps_service.py         # Distância (Routes API) e taxa de entrega
└── tests/integration/
    ├── conftest.py                         # Fixtures: variáveis de teste, Routes API simulada, LLMService real, Telegram falso
    ├── test_llm_service.py                 # RAG, histórico e extração de endereço (usam o Ollama real)
    ├── test_maps_service.py                # Distância, faixas de taxa, erros e cache
    └── test_telegram_service.py            # Orquestração das mensagens e painel de pedidos
```

O código segue uma divisão em camadas: `domain/` contém os modelos, sem dependência de serviços externos; `adapters/inbound/` recebe as mensagens (Telegram); `adapters/outbound/` fala com serviços externos (Ollama e Google Maps).

## Como funciona

1. `main.py` carrega o `.env`, configura os logs e inicia o `TelegramService`, que cria uma única instância do `LLMService` e do `MapsService` e prepara o painel do dia.
2. O `LLMService` é criado uma única vez: lê o PDF, divide o texto em blocos, gera embeddings com o Ollama e monta um índice FAISS em memória.
3. A cada mensagem, o bot acrescenta a data/hora (fuso de São Paulo) e:
   1. pede ao agente de extração que identifique um endereço na mensagem;
   2. se houver endereço, calcula a distância com o `MapsService`. Fora da área ou não localizado, responde direto ao cliente, sem chamar o LLM principal; dentro da área, anexa distância e taxa à mensagem;
   3. busca no índice os trechos relevantes do cardápio e chama o LLM com o prompt da atendente e o histórico daquele usuário. O LLM responde em JSON (`OrderExtraction`).
4. O campo `message` é enviado ao cliente. Quando `completion_status` é `true` (pagamento confirmado), o pedido é inserido no painel do dia (com os valores escapados em HTML) e o histórico do usuário é descartado, para que a próxima mensagem comece um pedido novo. Se o JSON do LLM vier inválido, o texto bruto é enviado como resposta.

Para mudar o comportamento da atendente, edite `resources/prompts/principal_agent.md` (chaves literais devem ser escritas `{{`/`}}`). Para atualizar o cardápio, substitua o PDF e reinicie o bot; se as taxas de entrega mudarem, atualize também `DELIVERY_FEES` em `adapters/outbound/maps_service.py`.

## Limitações conhecidas

- O histórico de conversa fica em memória e é perdido ao reiniciar o bot (limitado às últimas 10 mensagens por usuário).
- O índice do cardápio é refeito a cada inicialização.
- As faixas de taxa de entrega estão duplicadas: no cardápio (PDF) e em `DELIVERY_FEES` (`adapters/outbound/maps_service.py`).
- O prompt da atendente está em **modo de teste**: ignora o horário de funcionamento e aceita pedidos em qualquer dia e horário.
- O pagamento não é verificado: o pedido é finalizado quando o cliente diz que pagou.
- Pedido cancelado por falta de pagamento não limpa o histórico: só `completion_status = true` descarta a conversa.
- O painel do dia é escolhido na inicialização: se o bot ficar rodando após a meia-noite, os pedidos continuam sendo gravados no painel do dia anterior até ele ser reiniciado.
- O cache de distâncias vale só enquanto o bot está rodando.
- `RunnableWithMessageHistory` e `InMemoryChatMessageHistory` estão depreciados no LangChain (recomenda-se LangGraph); funcionam por enquanto.

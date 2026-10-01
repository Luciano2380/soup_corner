"""Testes integrados do TelegramService.

O fluxo `message_reply` é exercitado com objetos falsos de Update/Context (sem rede do Telegram),
o painel HTML real (resources/html/index.html copiado para um diretório temporário) e o
MapsService real com a Routes API falsa. Para o LLM há dois cenários:

- `FakeAgent`: respostas controladas, para testar a orquestração de forma determinística;
- LLMService real (marcador `ollama`): fluxo ponta a ponta Telegram → LLM → Maps → painel.
"""
import asyncio
from datetime import datetime, timezone

import pytest

from soup_corner.domain.model.address_extraction import AddressExtraction
from soup_corner.domain.model.order_extraction import OrderExtraction
from soup_corner.adapters.inbound import telegram_service as telegram_module
from soup_corner.adapters.outbound.maps_service import ROUTES_API_URL
from soup_corner.adapters.inbound.telegram_service import (
    ERROR_MESSAGE,
    ORDERS_TBODY_TAG,
    TELEGRAM_MAX_MESSAGE_LENGTH,
    TelegramService,
)

CUSTOMER_ADDRESS = "Rua das Flores, 100, Centro, Cidade Exemplo, Estado Exemplo"


class FakeAgent:
    """Substitui o LLMService com respostas configuráveis e registro das chamadas."""

    def __init__(self):
        self.address = AddressExtraction(is_address=False)
        self.reply = OrderExtraction(message="Olá! Como posso ajudar?")
        self.address_error: Exception | None = None
        self.reply_error: Exception | None = None
        self.address_calls: list[str] = []
        self.talk_calls: list[tuple[str, str]] = []
        self.cleared: list[str] = []

    def get_address_extraction(self, message):
        self.address_calls.append(message)
        if self.address_error:
            raise self.address_error
        return self.address

    def talk_message_agent(self, prompt, session_id):
        self.talk_calls.append((prompt, session_id))
        if self.reply_error:
            raise self.reply_error
        return self.reply

    def clear_session(self, session_id):
        self.cleared.append(session_id)


def replies(update) -> list[str]:
    return [call.args[0] for call in update.message.reply_text.await_args_list]


def panel_rows(service: TelegramService) -> str:
    content = service.table_edit.read_text(encoding="utf-8")
    start = content.index(ORDERS_TBODY_TAG)
    return content[start:content.index("</tbody>", start)]


@pytest.fixture
def fake_agent():
    return FakeAgent()


@pytest.fixture
def service(telegram_env, routes_api, fake_agent, monkeypatch):
    monkeypatch.setattr(telegram_module, "LLMService", lambda: fake_agent)
    svc = TelegramService()
    svc.maps.session.mount(ROUTES_API_URL, routes_api)
    svc.new_file()
    return svc


# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("variable", ["TELEGRAM_BOT_TOKEN", "HTML_TABLE_ORI_PATH", "HTML_TABLE_DEST_PATH"])
def test_init_requires_mandatory_variables(telegram_env, monkeypatch, variable):
    monkeypatch.setattr(telegram_module, "LLMService", FakeAgent)
    monkeypatch.delenv(variable)

    with pytest.raises(ValueError, match=variable):
        TelegramService()


def test_init_creates_single_agent_and_maps(telegram_env, monkeypatch):
    created = []
    monkeypatch.setattr(telegram_module, "LLMService", lambda: created.append(FakeAgent()) or created[-1])

    svc = TelegramService()

    assert len(created) == 1
    assert svc.agent is created[0]
    assert svc.maps.origin == "Rua do Restaurante, 123, Cidade Exemplo, Estado Exemplo"
    assert svc.table_edit is None


# ---------------------------------------------------------------------------
# Painel de pedidos (new_file / _register_order)
# ---------------------------------------------------------------------------

def test_new_file_creates_daily_panel(service, telegram_env):
    expected = telegram_env.resolve() / f"index_{datetime.now().strftime('%Y%m%d')}.html"

    assert service.table_edit == expected
    assert expected.read_bytes() == open("resources/html/index.html", "rb").read()


def test_new_file_reuses_existing_panel(service):
    service.table_edit.write_text("pedidos já registrados", encoding="utf-8")

    service.new_file()

    assert service.table_edit.read_text(encoding="utf-8") == "pedidos já registrados"


def test_new_file_without_template_raises_error(service, monkeypatch):
    service.table_ori = "resources/html/nao_existe.html"

    with pytest.raises(FileNotFoundError):
        service.new_file()


def test_register_order_inserts_escaped_row(service):
    order = OrderExtraction(
        completion_status=True,
        client="Ana <script>",
        order="2x Galinhada P-500",
        amount="R$ 35,80",
        address=None,
    )

    service._register_order(order, "4242")

    rows = panel_rows(service)
    assert '<tr data-order-id="4242-' in rows
    assert '<td class="customer">Ana &lt;script&gt;</td>' in rows
    assert '<td class="order">2x Galinhada P-500</td>' in rows
    assert '<td class="price">R$ 35,80</td>' in rows
    assert '<td class="address">-</td>' in rows


def test_register_order_concurrent_does_not_lose_orders(service):
    orders = [OrderExtraction(completion_status=True, client=f"Cliente {i}") for i in range(20)]

    async def register_all():
        await asyncio.gather(*(asyncio.to_thread(service._register_order, p, str(i)) for i, p in enumerate(orders)))

    asyncio.run(register_all())

    rows = panel_rows(service)
    assert rows.count("<tr data-order-id=") == 20
    assert all(f">Cliente {i}<" in rows for i in range(20))


def test_register_order_without_initialized_panel(service):
    service.table_edit = None

    with pytest.raises(RuntimeError):
        service._register_order(OrderExtraction(completion_status=True), "1")


def test_register_order_without_table_in_html(service):
    service.table_edit.write_text("<html><body></body></html>", encoding="utf-8")

    with pytest.raises(ValueError, match="Tabela de pedidos não encontrada"):
        service._register_order(OrderExtraction(completion_status=True), "1")


# ---------------------------------------------------------------------------
# /start
# ---------------------------------------------------------------------------

async def test_start_greeting_with_name(service, make_update):
    update, context = make_update("/start", first_name="Carlos")

    await service.start(update, context)

    [text] = replies(update)
    assert text.startswith("Olá, Carlos! 👋")
    assert "Anna" in text


# ---------------------------------------------------------------------------
# message_reply com agente falso
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", ["", "   "])
async def test_empty_message_is_ignored(service, fake_agent, make_update, text):
    update, context = make_update(text)

    await service.message_reply(update, context)

    assert fake_agent.address_calls == []
    update.message.reply_text.assert_not_awaited()


async def test_regular_message_without_address(service, fake_agent, routes_api, make_update):
    update, context = make_update("  Quais caldos vocês têm?  ", user_id=777)

    await service.message_reply(update, context)

    # 30/09/2026 15:00 UTC → quarta-feira 12:00 em São Paulo
    expected = "Quais caldos vocês têm?\n\nMensagem enviada em: quarta-feira, 30/09/2026 12:00"
    assert fake_agent.address_calls == [expected]
    assert fake_agent.talk_calls == [(expected, "777")]
    assert routes_api.requests == []
    assert replies(update) == ["Olá! Como posso ajudar?"]
    assert fake_agent.cleared == []
    context.bot.send_chat_action.assert_awaited()


async def test_served_address_is_forwarded_to_llm(service, fake_agent, routes_api, make_update):
    fake_agent.address = AddressExtraction(is_address=True, street="Rua das Flores", number="100", neighborhood="Centro")
    routes_api.distances[CUSTOMER_ADDRESS] = 2.0
    fake_agent.reply = OrderExtraction(message="Taxa de entrega R$ 8,00.")
    update, context = make_update("Rua das Flores, 100, Centro")

    await service.message_reply(update, context)

    [(prompt, _)] = fake_agent.talk_calls
    assert prompt.startswith("Rua das Flores, 100, Centro\n\nMensagem enviada em:")
    assert prompt.endswith("fica a 2,0 km; taxa de entrega: R$ 8,00. Informe isso ao cliente.]")
    assert replies(update) == ["Taxa de entrega R$ 8,00."]


async def test_address_outside_area_replies_without_llm(service, fake_agent, routes_api, make_update):
    fake_agent.address = AddressExtraction(is_address=True, street="Rua das Flores", number="100", neighborhood="Centro")
    routes_api.distances[CUSTOMER_ADDRESS] = 12.0
    update, context = make_update("Rua das Flores, 100, Centro")

    await service.message_reply(update, context)

    assert fake_agent.talk_calls == []
    [text] = replies(update)
    assert text.startswith("Infelizmente não entregamos")
    assert "fica a 12,0 km" in text


async def test_address_not_found_replies_without_llm(service, fake_agent, routes_api, make_update):
    fake_agent.address = AddressExtraction(is_address=True, street="Rua Inexistente", number="1")
    update, context = make_update("Rua Inexistente, 1")

    await service.message_reply(update, context)

    assert fake_agent.talk_calls == []
    assert replies(update)[0].startswith("Não consegui localizar o endereço")


async def test_address_extraction_failure_continues_conversation(service, fake_agent, make_update):
    fake_agent.address_error = RuntimeError("LLM de endereço fora do ar")
    update, context = make_update("Oi")

    await service.message_reply(update, context)

    assert len(fake_agent.talk_calls) == 1
    assert replies(update) == ["Olá! Como posso ajudar?"]


async def test_routes_api_failure_sends_error_message(service, fake_agent, routes_api, make_update):
    fake_agent.address = AddressExtraction(is_address=True, street="Rua das Flores", number="100")
    routes_api.status_code = 500
    update, context = make_update("Rua das Flores, 100")

    await service.message_reply(update, context)

    assert fake_agent.talk_calls == []
    assert replies(update) == [ERROR_MESSAGE]


async def test_llm_failure_sends_error_message(service, fake_agent, make_update):
    fake_agent.reply_error = ConnectionError("Ollama fora do ar")
    update, context = make_update("Oi")

    await service.message_reply(update, context)

    assert replies(update) == [ERROR_MESSAGE]
    assert fake_agent.cleared == []


@pytest.mark.parametrize("message", [None, "", "   "])
async def test_empty_llm_reply_becomes_error_message(service, fake_agent, make_update, message):
    fake_agent.reply = OrderExtraction(message=message)
    update, context = make_update("Oi")

    await service.message_reply(update, context)

    assert replies(update) == [ERROR_MESSAGE]


async def test_long_reply_split_at_telegram_limit(service, fake_agent, make_update):
    long_text = "a" * (TELEGRAM_MAX_MESSAGE_LENGTH * 2 + 10)
    fake_agent.reply = OrderExtraction(message=long_text)
    update, context = make_update("Me mande o cardápio completo")

    await service.message_reply(update, context)

    parts = replies(update)
    assert [len(p) for p in parts] == [TELEGRAM_MAX_MESSAGE_LENGTH, TELEGRAM_MAX_MESSAGE_LENGTH, 10]
    assert "".join(parts) == long_text


async def test_completed_order_registers_in_panel_and_clears_session(service, fake_agent, make_update):
    fake_agent.reply = OrderExtraction(
        message="Pedido finalizado! 😊 Obrigado por escolher o Soup Corner. ❤️",
        completion_status=True,
        client="Maria",
        order="1x Caldo Verde G-1000",
        amount="R$ 36,90",
        address="Rua das Flores, 100, Centro",
    )
    update, context = make_update("Pix feito", user_id=555)

    await service.message_reply(update, context)

    rows = panel_rows(service)
    assert '<tr data-order-id="555-' in rows
    assert '<td class="customer">Maria</td>' in rows
    assert '<td class="order">1x Caldo Verde G-1000</td>' in rows
    assert '<td class="price">R$ 36,90</td>' in rows
    assert '<td class="address">Rua das Flores, 100, Centro</td>' in rows
    assert fake_agent.cleared == ["555"]
    assert replies(update) == ["Pedido finalizado! 😊 Obrigado por escolher o Soup Corner. ❤️"]


async def test_order_registration_failure_still_replies_and_clears_session(service, fake_agent, make_update):
    fake_agent.reply = OrderExtraction(message="Pedido finalizado!", completion_status=True, client="Maria")
    service.table_edit.write_text("<html></html>", encoding="utf-8")
    update, context = make_update("Pix feito", user_id=556)

    await service.message_reply(update, context)

    assert fake_agent.cleared == ["556"]
    assert replies(update) == ["Pedido finalizado!"]


async def test_failing_typing_indicator_does_not_affect_reply(service, make_update):
    update, context = make_update("Oi")
    context.bot.send_chat_action.side_effect = RuntimeError("Telegram indisponível")

    await service.message_reply(update, context)

    assert replies(update) == ["Olá! Como posso ajudar?"]


async def test_date_converted_to_sao_paulo_timezone(service, fake_agent, make_update):
    # Domingo 01:30 UTC ainda é sábado 22:30 em São Paulo
    update, context = make_update("Oi", date=datetime(2026, 10, 4, 1, 30, tzinfo=timezone.utc))

    await service.message_reply(update, context)

    assert fake_agent.talk_calls[0][0].endswith("Mensagem enviada em: sábado, 03/10/2026 22:30")


# ---------------------------------------------------------------------------
# Ponta a ponta com o LLMService real (Ollama)
# ---------------------------------------------------------------------------

@pytest.fixture
def service_real(telegram_env, routes_api, llm_service, monkeypatch):
    monkeypatch.setattr(telegram_module, "LLMService", lambda: llm_service)
    svc = TelegramService()
    svc.maps.session.mount(ROUTES_API_URL, routes_api)
    svc.new_file()
    return svc


@pytest.mark.ollama
async def test_end_to_end_menu_question(service_real, llm_service, routes_api, make_update):
    user_id = 900001
    llm_service.clear_session(str(user_id))
    update, context = make_update("Quais caldos vocês têm?", user_id=user_id)

    try:
        await service_real.message_reply(update, context)

        [text] = replies(update)
        assert text.strip()
        assert text != ERROR_MESSAGE
        assert routes_api.requests == []
        assert len(llm_service.store[str(user_id)].messages) == 2
    finally:
        llm_service.clear_session(str(user_id))


@pytest.mark.ollama
async def test_end_to_end_address_with_delivery_fee(service_real, llm_service, routes_api, make_update):
    user_id = 900002
    llm_service.clear_session(str(user_id))
    # Qualquer destino que o LLM extrair fica a 2 km (faixa de R$ 8,00)
    routes_api.distances = _AlwaysTwoKm()
    update, context = make_update(
        "Quero fechar o pedido. Meu endereço é Rua das Flores, 100, bairro Centro", user_id=user_id
    )

    try:
        await service_real.message_reply(update, context)

        assert len(routes_api.requests) == 1
        destination = routes_api.bodies[0]["destination"]["address"]
        assert "Flores" in destination and "100" in destination

        history = llm_service.store[str(user_id)].messages
        assert "taxa de entrega: R$ 8,00" in history[0].content
        [text] = replies(update)
        assert text.strip() and text != ERROR_MESSAGE
    finally:
        llm_service.clear_session(str(user_id))


class _AlwaysTwoKm(dict):
    def get(self, key, default=None):
        return 2.0

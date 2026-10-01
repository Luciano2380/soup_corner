import os
import asyncio
import html
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, MessageHandler, filters, ContextTypes, CommandHandler
from soup_corner.adapters.outbound.llm_service import LLMService
from soup_corner.adapters.outbound.maps_service import MapsService
from soup_corner.domain.model.order_extraction import OrderExtraction

logger = logging.getLogger(__name__)

# Limite de caracteres por mensagem imposto pelo Telegram
TELEGRAM_MAX_MESSAGE_LENGTH = 4096

# O Telegram envia a data em UTC; o horário de funcionamento segue o fuso de São Paulo
TIMEZONE = ZoneInfo("America/Sao_Paulo")
WEEKDAYS = ("segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo")

ERROR_MESSAGE = "Desculpe, não consegui responder agora. Tente novamente em instantes."

# Onde as linhas de pedido são inseridas no painel (resources/html/index.html)
ORDERS_TBODY_TAG = '<tbody id="ordersTableBody">'


class TelegramService:
    def __init__(self):
        self.telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
        if not self.telegram_token:
            raise ValueError("Defina TELEGRAM_BOT_TOKEN no arquivo .env")
        self.table_ori = os.getenv("HTML_TABLE_ORI_PATH")
        self.table_dest = os.getenv("HTML_TABLE_DEST_PATH")
        if not self.table_ori or not self.table_dest:
            raise ValueError("Defina HTML_TABLE_ORI_PATH e HTML_TABLE_DEST_PATH no arquivo .env")
        self.agent = LLMService()
        self.maps = MapsService()
        self.table_edit: Path | None = None
        # Pedidos de clientes diferentes podem ser finalizados ao mesmo tempo
        self.table_lock = threading.Lock()

    def execute(self):
        application = Application.builder().token(self.telegram_token).build()
        application.add_handler(CommandHandler("start", self.start))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.message_reply))
        self.new_file()
        print("Bot iniciado com sucesso... Aperte Ctrl+C para encerrar.")
        application.run_polling(allowed_updates=Update.ALL_TYPES)

    async def message_reply(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.message.text.strip() if update.message and update.message.text else ""
        if not message:
            return
        date = update.message.date.astimezone(TIMEZONE)
        formatted_datetime = f"{WEEKDAYS[date.weekday()]}, {date.strftime('%d/%m/%Y %H:%M')}"
        session_id = str(update.effective_user.id)
        logger.info("Mensagem recebida | sessão=%s | %d caracteres", session_id, len(message))
        logger.debug("Conteúdo recebido | sessão=%s | %r", session_id, message)

        start_time = time.perf_counter()
        # Mantém o "digitando..." visível enquanto os LLMs processam (o Telegram o apaga após ~5s)
        typing_task = asyncio.create_task(self._keep_typing(context, update.effective_chat.id))
        try:
            message += f"\n\nMensagem enviada em: {formatted_datetime}"
            reply = await self._build_reply(message, session_id)
        except Exception:
            logger.exception("Erro ao gerar resposta | sessão=%s", session_id)
            reply = OrderExtraction(message=ERROR_MESSAGE)
        finally:
            typing_task.cancel()

        if reply.completion_status:
            try:
                await asyncio.to_thread(self._register_order, reply, session_id)
            except Exception:
                logger.exception("Erro ao registrar pedido no painel | sessão=%s", session_id)
            # Pedido encerrado: a próxima mensagem começa um pedido novo
            self.agent.clear_session(session_id)

        text = (reply.message or "").strip() or ERROR_MESSAGE
        # O Telegram rejeita mensagens acima de 4096 caracteres
        for i in range(0, len(text), TELEGRAM_MAX_MESSAGE_LENGTH):
            await update.message.reply_text(text[i:i + TELEGRAM_MAX_MESSAGE_LENGTH])

        logger.info(
            "Resposta enviada | sessão=%s | %d caracteres | pedido_finalizado=%s | %.2fs",
            session_id, len(text), reply.completion_status, time.perf_counter() - start_time,
        )
        logger.debug("Conteúdo enviado | sessão=%s | %r", session_id, text)

    async def _build_reply(self, message: str, session_id: str) -> OrderExtraction:
        # Todas as chamadas são bloqueantes (LLM/HTTP): rodam em thread para não travar o event loop
        try:
            address = await self._run_timed("extração de endereço", session_id, self.agent.get_address_extraction, message)
        except Exception:
            # Falha na extração não impede a resposta ao cliente: segue como mensagem comum
            logger.exception("Erro ao extrair endereço | sessão=%s", session_id)
            address = None

        if address is not None and address.is_address:
            end = address.to_address_string()
            logger.info("Endereço identificado | sessão=%s | %s", session_id, end)
            is_add, resp = await self._run_timed("cálculo de distância", session_id, self.maps.calculate_distance, address)
            logger.info("Distância calculada | sessão=%s | entrega_atendida=%s", session_id, is_add)
            if not is_add:
                return OrderExtraction(message=resp)
            message += resp

        return await self._run_timed("resposta", session_id, self.agent.talk_message_agent, message, session_id)

    def _register_order(self, reply: OrderExtraction, session_id: str) -> None:
        """Insere o pedido finalizado como linha da tabela do painel do dia."""
        if self.table_edit is None:
            raise RuntimeError("Painel de pedidos não inicializado")

        order_id = html.escape(f"{session_id}-{int(time.time())}")
        # Colunas do <thead> do painel: Cliente, Pedido, Valor, Endereço (classes do CSS do painel)
        cells = "".join(
            f'<td class="{css}">{html.escape(value or "-")}</td>'
            for css, value in (
                ("customer", reply.client),
                ("order", reply.order),
                ("price", reply.amount),
                ("address", reply.address),
            )
        )
        row = f'<tr data-order-id="{order_id}">{cells}</tr>\n'

        with self.table_lock:
            content = self.table_edit.read_text(encoding="utf-8")
            tbody = content.find(ORDERS_TBODY_TAG)
            end = content.find("</tbody>", tbody)
            if tbody == -1 or end == -1:
                raise ValueError(f"Tabela de pedidos não encontrada em {self.table_edit}")
            self.table_edit.write_text(content[:end] + row + content[end:], encoding="utf-8")

        logger.info("Pedido registrado no painel | sessão=%s | id=%s | %s", session_id, order_id, self.table_edit)

    @staticmethod
    async def _run_timed(step: str, session_id: str, func, *args):
        start_time = time.perf_counter()
        try:
            return await asyncio.to_thread(func, *args)
        finally:
            logger.info("Etapa '%s' concluída | sessão=%s | %.2fs", step, session_id, time.perf_counter() - start_time)

    @staticmethod
    async def _keep_typing(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
        try:
            while True:
                await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
                await asyncio.sleep(4)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Indicador de digitação é cosmético; falha não deve afetar a resposta
            logger.debug("Falha ao enviar ação 'digitando'", exc_info=True)

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_name = update.effective_user.first_name
        await update.message.reply_text(
            f"Olá, {user_name}! 👋\n\nEu sou Anna, Inteligência Artificial do Soup Corner. "
            "Pode conversar comigo sobre nosso cardápio e pedidos"
        )

    def new_file(self) -> None:
        """Cria o painel de pedidos do dia (index_AAAAMMDD.html) a partir do modelo, se ainda não existir."""
        source_file = Path(self.table_ori).resolve()
        dest_dir = Path(self.table_dest).resolve()

        if not source_file.exists():
            raise FileNotFoundError(f"Arquivo de origem do painel não encontrado: {source_file}")

        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_file = dest_dir / f"index_{datetime.now().strftime('%Y%m%d')}.html"
        self.table_edit = dest_file

        # Não sobrescreve: o bot pode ser reiniciado no mesmo dia com pedidos já registrados
        if dest_file.exists():
            logger.info("Painel do dia já existe e será reaproveitado | %s", dest_file)
            return

        dest_file.write_bytes(source_file.read_bytes())
        logger.info("Painel do dia criado | %s", dest_file)

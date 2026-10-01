import logging
import os

from dotenv import load_dotenv

from soup_corner.adapters.inbound.telegram_service import TelegramService


load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
# O httpx loga cada requisição em INFO, incluindo a URL com o token do bot
logging.getLogger("httpx").setLevel(logging.WARNING)

def main():

    print("Iniciando Projeto")
    telegram_service = TelegramService()
    telegram_service.execute()   

if __name__=="__main__":
    main()    
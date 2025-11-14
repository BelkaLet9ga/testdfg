import asyncio
import os

from aiosmtpd.controller import Controller

from config import load_env
from smtp_server import MailHandler
from storage import init_db
from telegram_bot import TelegramBot

SMTP_HOST = "0.0.0.0"
SMTP_PORT = 25


def get_token() -> str:
    load_env()
    token = os.getenv("TELEGRAM_TOKEN")
    if not token:
        raise RuntimeError(
            "Переменная TELEGRAM_TOKEN не задана. Добавьте её в .env или окружение."
        )
    return token


async def main():
    init_db()
    bot = TelegramBot(get_token())
    await bot.start()

    handler = MailHandler(notifier=bot)
    controller = Controller(handler, hostname=SMTP_HOST, port=SMTP_PORT)
    controller.start()
    print(f"SMTP: {SMTP_HOST}:{SMTP_PORT}")
    print("Telegram-бот запущен. Нажмите Ctrl+C для остановки.")

    try:
        await asyncio.Future()
    except asyncio.CancelledError:
        pass
    finally:
        controller.stop()
        await bot.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

import asyncio
import os

from aiosmtpd.controller import Controller

from smtp_server import MailHandler
from storage import init_db
from telegram_bot import TelegramBot

SMTP_HOST = "0.0.0.0"
SMTP_PORT = 25
TELEGRAM_TOKEN = os.getenv(
    "TELEGRAM_TOKEN",
    "8476649791:AAGSNbatatUasGP2wct88Rw4IVN0_J2sAMU",
)


async def main():
    init_db()
    bot = TelegramBot(TELEGRAM_TOKEN)
    await bot.start()

    handler = MailHandler(notifier=bot)
    controller = Controller(handler, hostname=SMTP_HOST, port=SMTP_PORT)
    controller.start()
    print(f"SMTP: {SMTP_HOST}:{SMTP_PORT}")
    print("Telegram-бот запущен. Нажмите Ctrl+C для остановки.")

    try:
        await bot.idle()
    finally:
        controller.stop()
        await bot.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

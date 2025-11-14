import asyncio

import uvicorn
from aiosmtpd.controller import Controller

from app import app, init_db
from smtp_server import MailHandler

API_HOST = "0.0.0.0"
API_PORT = 8000
SMTP_HOST = "0.0.0.0"
SMTP_PORT = 25


async def main():
    """Запускает SMTP и HTTP сервисы в одном процессе."""
    init_db()
    handler = MailHandler()
    controller = Controller(handler, hostname=SMTP_HOST, port=SMTP_PORT)
    controller.start()
    print(f"SMTP: {SMTP_HOST}:{SMTP_PORT}")
    print(f"HTTP: http://{API_HOST}:{API_PORT}")

    config = uvicorn.Config(app, host=API_HOST, port=API_PORT, log_level="info")
    server = uvicorn.Server(config)

    try:
        await server.serve()
    finally:
        controller.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

from __future__ import annotations

import asyncio
import contextlib
from typing import Optional

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from storage import ensure_mailbox, get_mailbox, get_user_for_address, list_messages


def _format_message(subject: Optional[str], sender: Optional[str], body: str) -> str:
    subject = subject or "(без темы)"
    sender = sender or "неизвестно"
    text = body.strip()
    if len(text) > 700:
        text = text[:700] + "..."
    return f"Тема: {subject}\nОт: {sender}\n{text or '[пустое тело]'}"


class TelegramBot:
    def __init__(self, token: str):
        self.application = Application.builder().token(token).build()
        self.application.add_handler(CommandHandler("start", self.cmd_start))
        self.application.add_handler(CommandHandler("inbox", self.cmd_inbox))
        self.application.add_handler(CommandHandler("help", self.cmd_help))
        self._polling_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        await self.application.initialize()
        await self.application.start()
        self._polling_task = asyncio.create_task(
            self.application.updater.start_polling()
        )

    async def idle(self) -> None:
        if self._polling_task:
            try:
                await self._polling_task
            except asyncio.CancelledError:
                pass

    async def stop(self) -> None:
        if self._polling_task:
            await self.application.updater.stop()
            with contextlib.suppress(asyncio.CancelledError):
                await self._polling_task
            self._polling_task = None
        await self.application.stop()
        await self.application.shutdown()

    async def notify_new_email(self, recipient: str, sender: str, subject: str, body: str) -> None:
        user_id = get_user_for_address(recipient)
        if not user_id:
            return
        text = (
            f"Новое письмо для {recipient}\n"
            f"{_format_message(subject, sender, body)}"
        )
        await self.application.bot.send_message(chat_id=int(user_id), text=text)

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message:
            return
        address = ensure_mailbox(update.effective_user.id)
        await update.message.reply_text(
            "Привет! Вот твой временный адрес:\n"
            f"{address}\n\n"
            "Команда /inbox покажет последние письма."
        )

    async def cmd_inbox(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message:
            return
        address = get_mailbox(update.effective_user.id)
        if not address:
            address = ensure_mailbox(update.effective_user.id)

        messages = list_messages(address, limit=5)
        if not messages:
            await update.message.reply_text(
                f"Входящие для {address} пока пустые."
            )
            return

        parts = [
            _format_message(m["subject"], m["sender"], m.get("body") or "")
            for m in messages
        ]
        body = "\n\n---\n\n".join(parts)
        await update.message.reply_text(
            f"Почта для {address}:\n\n{body}"
        )

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message:
            return
        address = get_mailbox(update.effective_user.id) or ensure_mailbox(update.effective_user.id)
        await update.message.reply_text(
            "Доступные команды:\n"
            "/start - получить/напомнить адрес\n"
            "/inbox - показать последние письма\n\n"
            f"Текущий адрес: {address}"
        )

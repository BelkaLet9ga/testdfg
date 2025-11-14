from __future__ import annotations

import asyncio
import contextlib
from typing import Optional

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from storage import ensure_mailbox, get_mailbox, get_user_for_address, list_messages


def _format_message(subject: Optional[str], sender: Optional[str], body: str) -> str:
    subject = subject or "(bez temy)"
    sender = sender or "neizvestno"
    text = body.strip()
    if len(text) > 700:
        text = text[:700] + "..."
    return f"Tema: {subject}\nOt: {sender}\n{text or '[pustoe telo]'}"


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
        if self.application.updater:
            await self.application.updater.stop()
        if self._polling_task:
            self._polling_task.cancel()
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
            f"Novoe pis'mo dlya {recipient}\n"
            f"{_format_message(subject, sender, body)}"
        )
        await self.application.bot.send_message(chat_id=int(user_id), text=text)

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message:
            return
        address = ensure_mailbox(update.effective_user.id)
        await update.message.reply_text(
            "Privet! Vot tvoy vremennii adres:\n"
            f"{address}\n\n"
            "Komanda /inbox pokazhet poslednie pisma."
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
                f"Vkhodyashchie dlya {address} poka pustye."
            )
            return

        parts = [
            _format_message(m["subject"], m["sender"], m.get("body") or "")
            for m in messages
        ]
        body = "\n\n---\n\n".join(parts)
        await update.message.reply_text(
            f"Pochta dlya {address}:\n\n{body}"
        )

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message:
            return
        address = get_mailbox(update.effective_user.id) or ensure_mailbox(update.effective_user.id)
        await update.message.reply_text(
            "Komandy:\n"
            "/start - poluchit' ili napomnit' adres\n"
            "/inbox - pokazat' poslednie pisma\n\n"
            f"Tekushchii adres: {address}"
        )

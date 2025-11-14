from __future__ import annotations

import asyncio
from datetime import datetime
from html import escape
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)
from telegram.error import BadRequest

from storage import (
    change_mailbox,
    count_messages,
    ensure_mailbox_record,
    get_message,
    get_user_for_address,
    list_messages,
)

MESSAGE_LIMIT = 5
MAX_MESSAGE_LENGTH = 3500
TRUNCATION_NOTICE = "\n...\n[Текст обрезан]"


def _format_datetime(value: str) -> str:
    try:
        dt = datetime.fromisoformat(value)
        return dt.strftime("%d.%m.%Y %H:%M")
    except ValueError:
        return value


def _short(text: Optional[str], limit: int = 40) -> str:
    if not text:
        return ""
    value = text.strip()
    return value if len(value) <= limit else value[: limit - 3] + "..."


class TelegramBot:
    def __init__(self, token: str):
        self.application = Application.builder().token(token).build()
        self.application.add_handler(CommandHandler("start", self.cmd_start))
        self.application.add_handler(CommandHandler("inbox", self.cmd_inbox))
        self.application.add_handler(CommandHandler("help", self.cmd_help))
        self.application.add_handler(CallbackQueryHandler(self.on_callback))
        self._polling_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        await self.application.initialize()
        await self.application.start()
        self._polling_task = asyncio.create_task(
            self.application.updater.start_polling()
        )

    async def stop(self) -> None:
        if self.application.updater:
            await self.application.updater.stop()
        if self._polling_task:
            self._polling_task.cancel()
            try:
                await self._polling_task
            except asyncio.CancelledError:
                pass
            self._polling_task = None
        await self.application.stop()
        await self.application.shutdown()

    async def notify_new_email(
        self, recipient: str, sender: str, subject: str, body: str
    ) -> None:
        user_id = get_user_for_address(recipient)
        if not user_id:
            return
        preview = _short(body, 300)
        text = (
            f"<b>Новое письмо для {escape(recipient)}</b>\n"
            f"<b>От:</b> {escape(sender or 'Неизвестно')}\n"
            f"<b>Тема:</b> {escape(subject or '(без темы)')}\n\n"
            f"{escape(preview or '[Пустое тело]')}\n\n"
            "Открой /inbox, чтобы увидеть детали."
        )
        await self.application.bot.send_message(
            chat_id=int(user_id), text=text, parse_mode="HTML"
        )

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message:
            return
        await self._send_dashboard(update.effective_chat.id, update.effective_user.id)

    async def cmd_inbox(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message:
            return
        await self._send_dashboard(update.effective_chat.id, update.effective_user.id)

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message:
            return
        await update.message.reply_text(
            "Команды:\n"
            "/start — открыть главное меню\n"
            "/inbox — обновить список писем\n"
            "/help — краткая справка"
        )

    async def on_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not query or not query.from_user or not query.message:
            return
        data = query.data or ""
        user_id = query.from_user.id
        chat_id = query.message.chat.id
        message_id = query.message.message_id

        if data == "noop":
            await query.answer("Писем пока нет")
            return

        if data == "refresh":
            await self._send_dashboard(chat_id, user_id, message_id)
            await query.answer("Список обновлён")
            return

        if data == "change":
            info = change_mailbox(user_id)
            await self._send_dashboard(chat_id, user_id, message_id)
            await query.answer(f"Новый ящик: {info['address']}")
            return

        if data.startswith("msg:"):
            try:
                message_id_db = int(data.split(":", 1)[1])
            except (ValueError, IndexError):
                await query.answer("Некорректный запрос", show_alert=True)
                return
            email = get_message(message_id_db)
            if not email:
                await query.answer("Письмо не найдено", show_alert=True)
                return
            body = (email.get("body") or "").strip()
            if len(body) > MAX_MESSAGE_LENGTH:
                body = body[: MAX_MESSAGE_LENGTH - len(TRUNCATION_NOTICE)] + TRUNCATION_NOTICE
            text = (
                f"<b>От:</b> {escape(email.get('sender') or 'Неизвестно')}\n"
                f"<b>Тема:</b> {escape(email.get('subject') or '(без темы)')}\n"
                f"<b>Получено:</b> {escape(email.get('received_at') or '')}\n\n"
                f"<pre>{escape(body or '[Пустое тело]')}</pre>"
            )
            await self.application.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="HTML",
            )
            await query.answer()
            return

    async def _send_dashboard(
        self, chat_id: int, user_id: int, message_id: Optional[int] = None
    ) -> None:
        info = ensure_mailbox_record(user_id)
        address = info["address"]
        created_at = _format_datetime(info["created_at"])
        total = count_messages(address)
        letters = list_messages(address, limit=MESSAGE_LIMIT)

        text = (
            "<b>Главное меню</b>\n\n"
            f"<b>📧 {escape(address)}</b>\n"
            f"<b>Количество писем: {total}</b>\n"
            f"<b>Дата создания: {escape(created_at)}</b>"
        )

        keyboard: list[list[InlineKeyboardButton]] = []
        if not letters:
            keyboard.append([InlineKeyboardButton("📄 Ящик пустой", callback_data="noop")])
        else:
            for mail in letters:
                sender = mail.get("sender") or "Без имени"
                subject = mail.get("subject") or "(без темы)"
                title = _short(f"{sender} - {subject}")
                keyboard.append(
                    [InlineKeyboardButton(title, callback_data=f"msg:{mail['id']}")]
                )
        keyboard.append([InlineKeyboardButton("↻ Обновить", callback_data="refresh")])
        keyboard.append([InlineKeyboardButton("✏️ Изменить почту", callback_data="change")])
        markup = InlineKeyboardMarkup(keyboard)

        if message_id:
            try:
                await self.application.bot.edit_message_text(
                    text=text,
                    chat_id=chat_id,
                    message_id=message_id,
                    parse_mode="HTML",
                    reply_markup=markup,
                )
            except BadRequest as exc:
                if "Message is not modified" not in str(exc):
                    raise
        else:
            await self.application.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="HTML",
                reply_markup=markup,
            )

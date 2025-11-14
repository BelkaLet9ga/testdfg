from __future__ import annotations

import asyncio
from datetime import datetime
import re
from html import escape
from html.parser import HTMLParser
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
    ensure_user,
    get_message,
    get_user_for_address,
    list_messages,
)

MESSAGE_LIMIT = 5
MAX_MESSAGE_LENGTH = 3500
TRUNCATION_NOTICE = "\n...\n[Текст обрезан]"
CODE_RE = re.compile(r"\b(?:\d{4,8}|[A-Z0-9]{4,10})\b")


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


def _split_sender(raw: str) -> tuple[str, str]:
    raw = raw.strip() or "Неизвестно"
    if "<" in raw and ">" in raw:
        name, email = raw.split("<", 1)
        return name.strip().strip('"'), email.strip(" >")
    return raw, raw


class _LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._current_href: Optional[str] = None
        self._buffer: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "a":
            return
        attrs_dict = dict(attrs)
        href = attrs_dict.get("href")
        if not href:
            return
        self._current_href = href
        self._buffer = []

    def handle_data(self, data):
        if self._current_href is not None:
            self._buffer.append(data)

    def handle_endtag(self, tag):
        if tag.lower() != "a" or self._current_href is None:
            return
        text = "".join(self._buffer).strip() or self._current_href
        self.links.append((text, self._current_href))
        self._current_href = None
        self._buffer = []


def _extract_links(html: str) -> list[tuple[str, str]]:
    if not html:
        return []
    parser = _LinkParser()
    parser.feed(html)
    return parser.links


class _TextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.chunks: list[str] = []

    def handle_data(self, data):
        self.chunks.append(data)

    def get_text(self) -> str:
        return "".join(self.chunks)


def _html_to_text(html_content: str) -> str:
    parser = _TextParser()
    parser.feed(html_content or "")
    return parser.get_text()


def _normalize_body(body_plain: str, body_html: str) -> str:
    text = body_plain or ""
    if not text.strip() and body_html:
        return _html_to_text(body_html)
    if "<" in text and ">" in text and body_html:
        return _html_to_text(body_html)
    return text


def _extract_codes(text: str) -> list[str]:
    if not text:
        return []
    result = []
    seen = set()
    for match in CODE_RE.findall(text):
        code = match.strip()
        if code.upper() in seen:
            continue
        seen.add(code.upper())
        result.append(code)
    return result[:5]


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
        self,
        recipient: str,
        sender: str,
        subject: str,
        body_plain: str,
        body_html: str,
    ) -> None:
        owner = get_user_for_address(recipient)
        if not owner or not owner.get("telegram_id"):
            return
        normalized_text = _normalize_body(body_plain or "", body_html or "")
        preview = ""
        buttons = []
        links = _extract_links(body_html or "")
        codes = _extract_codes(normalized_text)

        for title, href in links[:3]:
            buttons.append(
                [
                    InlineKeyboardButton(
                        title[:32] or href, url=href
                    )
                ]
            )
        name, email = _split_sender(sender or "")
        text = (
            "<b>🔔 Новое письмо</b>\n"
            f"├ {escape(name)} &lt;{escape(email)}&gt;\n"
            f"└ <b>{escape(subject or '(без темы)')}</b>\n\n"
        )
        if codes:
            text += (
                "<b>📧 Ваш код:</b> "
                + " / ".join(f"<code>{escape(code)}</code>" for code in codes[:3])
                + "\n\n"
            )
        else:
            text += f"{escape(_short(normalized_text, 120) or '[Пустое тело]')}\n\n"
        if links and not codes:
            text += "Нажмите кнопку, чтобы открыть ссылку.\n"

        buttons.append([InlineKeyboardButton("🔍 Открыть письмо", callback_data="refresh")])
        keyboard = InlineKeyboardMarkup(buttons)
        await self.application.bot.send_message(
            chat_id=int(owner["telegram_id"]),
            text=text,
            parse_mode="HTML",
            reply_markup=keyboard,
        )

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message:
            return
        await self._send_dashboard(update.effective_chat.id, update.effective_user)

    async def cmd_inbox(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message:
            return
        await self._send_dashboard(update.effective_chat.id, update.effective_user)

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
        chat_id = query.message.chat.id
        message_id = query.message.message_id

        if data == "noop":
            await query.answer("Писем пока нет")
            return

        if data == "refresh":
            await self._send_dashboard(chat_id, query.from_user, message_id)
            await query.answer("Список обновлён")
            return

        if data == "change":
            user_record = ensure_user(query.from_user.id, query.from_user.full_name)
            info = change_mailbox(user_record["id"])
            await self._send_dashboard(chat_id, query.from_user, message_id)
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
            normalized_text = _normalize_body(
                email.get("body_plain") or email.get("body") or "",
                email.get("body_html") or "",
            )
            body = normalized_text
            if len(body) > MAX_MESSAGE_LENGTH:
                body = body[: MAX_MESSAGE_LENGTH - len(TRUNCATION_NOTICE)] + TRUNCATION_NOTICE
            links = _extract_links(email.get("body_html") or "")
            codes = _extract_codes(normalized_text)
            links_block = ""
            if links:
                rendered = []
                for idx, (title, href) in enumerate(links, start=1):
                    rendered.append(
                        f"{idx}. <a href=\"{escape(href)}\">{escape(title)}</a>"
                    )
                links_block = "\n\n<b>Ссылки из письма:</b>\n" + "\n".join(rendered)
            codes_block = ""
            if codes:
                codes_block = "\n\n<b>Коды:</b>\n" + "\n".join(
                    f"{idx}. <code>{escape(code)}</code>"
                    for idx, code in enumerate(codes, start=1)
                )
            text = (
                f"<b>От:</b> {escape(email.get('sender') or 'Неизвестно')}\n"
                f"<b>Тема:</b> {escape(email.get('subject') or '(без темы)')}\n"
                f"<b>Получено:</b> {escape(email.get('received_at') or '')}\n\n"
                f"<pre>{escape(body or '[Пустое тело]')}</pre>"
                f"{links_block}"
                f"{codes_block}"
            )
            await self.application.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="HTML",
            )
            await query.answer()
            return

    async def _send_dashboard(
        self, chat_id: int, telegram_user, message_id: Optional[int] = None
    ) -> None:
        user_record = ensure_user(telegram_user.id, telegram_user.full_name)
        mailbox = ensure_mailbox_record(user_record["id"])
        address = mailbox["address"]
        created_at = _format_datetime(mailbox["created_at"])
        total = count_messages(mailbox["id"])
        letters = list_messages(mailbox["id"], limit=MESSAGE_LIMIT)

        text = (
            "<b>Главное меню</b>\n\n"
            f"<b>📧 {escape(address)}</b>\n"
            f"<b>Пароль:</b> {escape(mailbox['password'])}\n"
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

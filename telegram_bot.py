from __future__ import annotations

import asyncio
import os
import re
import time
from datetime import datetime
from html import escape
from html.parser import HTMLParser
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.error import BadRequest, Forbidden, RetryAfter, TimedOut

from storage import (
    attach_mailbox,
    change_mailbox,
    count_messages,
    ensure_mailbox_record,
    ensure_user,
    get_domain,
    get_message,
    get_user_for_address,
    get_total_users,
    get_total_emails,
    list_telegram_ids,
    set_domain,
    list_messages,
)
from user_store import get_known_user_ids, upsert_user

MESSAGE_PAGE_SIZE = 5
MESSAGE_FETCH_LIMIT = 50
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


def _build_notification_text(state: dict) -> str:
    text = (
        "<b>📨 Новое письмо</b>\n"
        f"├ {escape(state['sender_line'])}\n"
        f"└ <b><code>{escape(state['subject'])}</code></b>\n\n"
    )
    if state.get("code"):
        code_display = state["code"] if state["code_visible"] else "✱✱✱✱"
        text += f"<b>🔏 Возможный код:</b> <code>{escape(code_display)}</code>"
    return text


def _build_notification_keyboard(
    state: dict,
    include_open_button: bool = True,
    include_code_button: bool = True,
) -> list[list[InlineKeyboardButton]]:
    buttons: list[list[InlineKeyboardButton]] = []
    if include_code_button and state.get("code"):
        label = "👁 Скрыть код" if state["code_visible"] else "👁 Показать код"
        buttons.append([InlineKeyboardButton(label, callback_data="notif_code")])
    if state.get("links"):
        icon = "▼" if state["links_open"] else "⌵"
        buttons.append(
            [InlineKeyboardButton(f"🔗 Показать ссылки {icon}", callback_data="notif_links")]
        )
        if state["links_open"]:
            for title, href in state["links"]:
                buttons.append([InlineKeyboardButton(title[:32] or href, url=href)])
    if include_open_button:
        buttons.append([InlineKeyboardButton("🔍 Показать письмо", callback_data="notif_open")])
    return buttons


def _build_full_email_text(state: dict) -> str:
    text = (
        f"От: {escape(state['sender_line'])}\n"
        f"Тема: {escape(state['subject'])}\n"
        f"Получено: {escape(state.get('received_at') or '')}\n\n"
    )
    if state.get("code"):
        text += f"🔏 Возможный код: <code>{escape(state['code'])}</code>\n\n"
    body = state.get("body_text") or "[Пустое тело]"
    text += f"<pre>{escape(body)}</pre>"
    return text


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


def _short_user(user) -> str:
    if not user:
        return "unknown"
    if user.username:
        return f"@{user.username}"
    return f"{user.full_name} ({user.id})"


def _parse_telegram_ids(raw: str) -> set[int]:
    ids: set[int] = set()
    normalized = raw.replace("\u00A0", " ").replace("\u202F", " ")
    lines = normalized.splitlines()
    if not lines:
        lines = [normalized]
    for line in lines:
        cleaned = re.sub(r"[^\d]", "", line)
        if not cleaned:
            continue
        try:
            ids.add(int(cleaned))
        except ValueError:
            continue
    return ids


def _admin_panel_text() -> str:
    domain = get_domain()
    total_users = get_total_users()
    return (
        "🛠 Админ-панель\n"
        f"Активный домен: <code>{escape(domain)}</code>\n"
        f"Пользователей: <b>{total_users}</b>"
    )


def _admin_panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Сменить домен", callback_data="admin_change_domain")],
            [InlineKeyboardButton("Отправить сообщение", callback_data="admin_broadcast")],
            [InlineKeyboardButton("Добавить юзеров", callback_data="admin_add_users")],
        ]
    )


def _broadcast_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Отправить", callback_data="admin_broadcast_confirm"),
                InlineKeyboardButton("✖️ Отмена", callback_data="admin_broadcast_cancel"),
            ]
        ]
    )


class TelegramBot:
    def __init__(self, token: str):
        self.application = Application.builder().token(token).build()
        self.application.add_handler(CommandHandler("start", self.cmd_start))
        self.application.add_handler(CommandHandler("inbox", self.cmd_inbox))
        self.application.add_handler(CommandHandler("help", self.cmd_help))
        self.application.add_handler(CommandHandler("stats", self.cmd_stats))
        self.application.add_handler(CommandHandler("admin", self.cmd_admin))
        self.application.add_handler(CallbackQueryHandler(self.on_callback))
        self.application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text)
        )
        self._polling_task: Optional[asyncio.Task] = None
        self._tools_state: dict[int, bool] = {}
        self._password_visible: dict[int, bool] = {}
        self._inbox_state: dict[int, dict[str, int | bool]] = {}
        self._auth_state: dict[int, dict[str, str]] = {}
        self._notif_state: dict[tuple[int, int], dict] = {}
        self._admin_state: dict[int, dict[str, str]] = {}
        self._known_users: set[int] = get_known_user_ids()
        self._action_times: dict[int, float] = {}
        self._flood_interval = 0.7
        self.log_chat_id = int(os.environ.get("LOG_CHAT_ID", "-1003225324834"))
        self._log_lock = asyncio.Lock()
        self._last_log_time = 0.0
        self._log_delay = float(os.environ.get("LOG_THROTTLE", "1.0"))
        self.admin_id = int(os.environ.get("ADMIN_ID", "7942744213"))

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

    async def _log_event(self, text: str) -> None:
        if not self.log_chat_id:
            return
        async with self._log_lock:
            now = time.monotonic()
            wait_for = self._log_delay - (now - self._last_log_time)
            if wait_for > 0:
                await asyncio.sleep(wait_for)
            try:
                await self.application.bot.send_message(
                    chat_id=self.log_chat_id, text=text[:4000]
                )
            except Exception:
                pass
            else:
                self._last_log_time = time.monotonic()

    def _allow_action(self, user_id: int) -> bool:
        now = time.monotonic()
        last = self._action_times.get(user_id, 0)
        if now - last < self._flood_interval:
            return False
        self._action_times[user_id] = now
        return True

    def _is_admin(self, user) -> bool:
        return bool(user and user.id == self.admin_id)

    async def _register_user(self, telegram_user) -> None:
        entry, is_new, total = upsert_user(
            telegram_user.id,
            telegram_user.full_name,
            telegram_user.username,
        )
        if telegram_user.id not in self._known_users:
            self._known_users.add(telegram_user.id)
            await self._log_event(
                f"👤 Новый пользователь: {_short_user(telegram_user)} (всего: {total})"
            )

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
        owner_name = owner.get("name") or owner.get("telegram_id")
        owner_username = owner.get("username")
        user_label = (
            f"@{owner_username}" if owner_username else owner_name or owner.get("telegram_id")
        )
        normalized_text = _normalize_body(body_plain or "", body_html or "")
        links = _extract_links(body_html or "")[:3]
        codes = _extract_codes(normalized_text)

        name, email = _split_sender(sender or "")
        state = {
            "sender_line": f"{name} <{email}>",
            "subject": subject or "(без темы)",
            "code": codes[0] if codes else None,
            "code_visible": False,
            "links": links,
            "links_open": False,
            "body_text": normalized_text.strip(),
            "received_at": datetime.utcnow().isoformat(),
        }
        keyboard = InlineKeyboardMarkup(_build_notification_keyboard(state))
        message = await self.application.bot.send_message(
            chat_id=int(owner["telegram_id"]),
            text=_build_notification_text(state),
            parse_mode="HTML",
            reply_markup=keyboard,
        )
        self._notif_state[(message.chat_id, message.message_id)] = state
        await self._log_event(
            f"📨 Новое письмо для {recipient} (user: {user_label}) от {state['sender_line']} ({state['subject']})"
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

    async def cmd_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message or not update.effective_chat:
            return
        if (
            update.effective_user.id != self.admin_id
            or update.effective_chat.id != self.log_chat_id
        ):
            await update.message.reply_text("Эта команда недоступна.")
            return
        users = get_total_users()
        emails = get_total_emails()
        await update.message.reply_text(
            f"📊 Статистика:\n"
            f"• Пользователей: {users}\n"
            f"• Получено писем: {emails}"
        )

    async def cmd_admin(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message:
            return
        if not self._is_admin(update.effective_user):
            await update.message.reply_text("Эта команда недоступна.")
            return
        await update.message.reply_text(
            _admin_panel_text(),
            parse_mode="HTML",
            reply_markup=_admin_panel_keyboard(),
        )

    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message or not update.effective_chat:
            return
        chat_id = update.effective_chat.id
        admin_state = self._admin_state.get(chat_id)
        state = self._auth_state.get(chat_id)
        if not admin_state and not state:
            return
        await self._register_user(update.effective_user)
        if not self._allow_action(update.effective_user.id):
            await update.message.reply_text("Помедленнее…")
            await self._log_event(
                f"🚫 Flood control: {_short_user(update.effective_user)}"
            )
            return
        text = update.message.text.strip()
        if text.lower() in {"/cancel", "отмена"}:
            if admin_state:
                self._admin_state.pop(chat_id, None)
            if state:
                self._auth_state.pop(chat_id, None)
            await update.message.reply_text("Действие отменено.")
            return

        if admin_state:
            if not self._is_admin(update.effective_user):
                self._admin_state.pop(chat_id, None)
                await update.message.reply_text("Эта команда недоступна.")
                return
            await self._handle_admin_text(update, admin_state, text)
            return

        if not state:
            return

        if state.get("step") == "login":
            state["login"] = text
            state["step"] = "password"
            await update.message.reply_text("Введите пароль:")
            return

        if state.get("step") == "password":
            login = state.get("login")
            password = text
            user_record = ensure_user(
                update.effective_user.id,
                update.effective_user.full_name,
                update.effective_user.username,
            )
            mailbox = attach_mailbox(user_record["id"], login, password)
            if mailbox:
                self._auth_state.pop(chat_id, None)
                await update.message.reply_text(f"Вы вошли в {mailbox['address']}")
                await self._send_dashboard(chat_id, update.effective_user)
                await self._log_event(
                    f"🔐 Пользователь {_short_user(update.effective_user)} вошёл в {mailbox['address']}"
                )
            else:
                state["step"] = "login"
                state.pop("login", None)
                await update.message.reply_text(
                    "Неверный логин или пароль. Попробуйте снова или напишите /cancel."
                )
                await self._log_event(
                    f"⚠️ Неудачный вход для {_short_user(update.effective_user)} с логином {login}"
                )
            return

    async def _handle_admin_text(self, update: Update, admin_state: dict, text: str) -> None:
        chat_id = update.effective_chat.id
        mode = admin_state.get("mode")

        if mode == "domain":
            new_domain = text.strip().lower()
            if (
                not new_domain
                or " " in new_domain
                or "@" in new_domain
                or "." not in new_domain
                or not re.match(r"^[a-zA-Z0-9.-]+$", new_domain)
            ):
                await update.message.reply_text(
                    "Введите домен без @ и пробелов, например example.com"
                )
                return
            normalized = set_domain(new_domain)
            self._admin_state.pop(chat_id, None)
            await update.message.reply_text(
                f"✅ Домен обновлён: <code>{escape(normalized)}</code>\n\n{_admin_panel_text()}",
                parse_mode="HTML",
                reply_markup=_admin_panel_keyboard(),
            )
            await self._log_event(
                f"🛠 Домен изменён на {normalized} админом {_short_user(update.effective_user)}"
            )
            return

        if mode == "broadcast":
            admin_state["message"] = text
            admin_state["step"] = "confirm"
            await update.message.reply_text(
                f"Рассылка предпросмотр:\n\n{text}",
                reply_markup=_broadcast_confirm_keyboard(),
            )
            return

        if mode == "add_users":
            ids = _parse_telegram_ids(text)
            if not ids:
                await update.message.reply_text("Не нашёл ни одного ID. Введите ID, каждый с новой строки.")
                return
            existing = set(list_telegram_ids())
            new_count = 0
            for tid in ids:
                if tid not in existing:
                    new_count += 1
                ensure_user(tid, name=None, username=None)
                upsert_user(tid, name=None, username=None)
            self._admin_state.pop(chat_id, None)
            total_users = get_total_users()
            await update.message.reply_text(
                f"Готово. Новых: {new_count}, всего обработано: {len(ids)}, сейчас пользователей: {total_users}",
                reply_markup=_admin_panel_keyboard(),
            )
            await self._log_event(
                f"👥 Админ {_short_user(update.effective_user)} добавил пользователей: новых {new_count}, всего {len(ids)}"
            )
            return

        await update.message.reply_text("Неизвестное действие.")

    async def _send_full_email(
        self, chat_id: int, source_message_id: int, bot: Optional[object] = None
    ) -> None:
        state = self._notif_state.get((chat_id, source_message_id))
        if not state:
            await self.application.bot.send_message(
                chat_id=chat_id, text="Письмо недоступно."
            )
            return
        keyboard = InlineKeyboardMarkup(
            _build_notification_keyboard(
                state, include_open_button=False, include_code_button=False
            )
        )
        target_bot = bot or self.application.bot
        message = await target_bot.send_message(
            chat_id=chat_id,
            text=_build_full_email_text(state),
            parse_mode="HTML",
            reply_markup=keyboard,
        )
        self._notif_state[(message.chat_id, message.message_id)] = dict(state)

    async def _broadcast_message(self, text: str) -> tuple[int, int, int]:
        user_ids = set(list_telegram_ids())
        if self.admin_id:
            user_ids.add(self.admin_id)
        sent = 0
        failed = 0
        for uid in user_ids:
            try:
                await self.application.bot.send_message(chat_id=uid, text=text)
                sent += 1
            except RetryAfter as exc:
                await asyncio.sleep(exc.retry_after + 0.5)
                try:
                    await self.application.bot.send_message(chat_id=uid, text=text)
                    sent += 1
                except Exception:
                    failed += 1
            except Forbidden:
                failed += 1
            except (TimedOut, BadRequest):
                failed += 1
            except Exception:
                failed += 1
            await asyncio.sleep(0.05)
        return sent, len(user_ids), failed

    async def on_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not query or not query.from_user or not query.message:
            return
        await self._register_user(query.from_user)
        if not self._allow_action(query.from_user.id):
            await query.answer("Помедленнее…")
            await self._log_event(f"🚫 Flood control: {_short_user(query.from_user)}")
            return
        data = query.data or ""
        chat_id = query.message.chat.id
        message_id = query.message.message_id
        is_admin = self._is_admin(query.from_user)

        if data == "noop":
            await query.answer("Писем пока нет")
            return

        if data == "admin_change_domain":
            if not is_admin:
                await query.answer("Недоступно", show_alert=True)
                return
            self._admin_state[chat_id] = {"mode": "domain", "step": "domain"}
            await query.answer()
            await self.application.bot.send_message(
                chat_id=chat_id,
                text="Введите новый домен (без @):",
            )
            return

        if data == "admin_broadcast":
            if not is_admin:
                await query.answer("Недоступно", show_alert=True)
                return
            self._admin_state[chat_id] = {"mode": "broadcast", "step": "text"}
            await query.answer()
            await self.application.bot.send_message(
                chat_id=chat_id,
                text="Введите текст рассылки:",
            )
            return

        if data == "admin_add_users":
            if not is_admin:
                await query.answer("Недоступно", show_alert=True)
                return
            self._admin_state[chat_id] = {"mode": "add_users", "step": "text"}
            await query.answer()
            await self.application.bot.send_message(
                chat_id=chat_id,
                text="Вставьте список Telegram ID (каждый с новой строки или через пробел):",
            )
            return

        if data == "admin_broadcast_cancel":
            if not is_admin:
                await query.answer("Недоступно", show_alert=True)
                return
            self._admin_state.pop(chat_id, None)
            await query.answer("Отменено")
            return

        if data == "admin_broadcast_confirm":
            if not is_admin:
                await query.answer("Недоступно", show_alert=True)
                return
            state = self._admin_state.get(chat_id)
            message_text = state.get("message") if state else None
            if not message_text:
                await query.answer("Сообщение отсутствует", show_alert=True)
                return
            await query.answer("Отправляю…")
            sent, total, failed = await self._broadcast_message(message_text)
            self._admin_state.pop(chat_id, None)
            note = f"Рассылка отправлена: {sent}/{total}"
            if failed:
                note += f" (ошибок: {failed})"
            await self.application.bot.send_message(
                chat_id=chat_id,
                text=note,
            )
            await self._log_event(
                f"📢 Рассылка от {_short_user(query.from_user)}: {sent}/{total}, errors: {failed}"
            )
            return

        if data == "toggle_inbox":
            await self._send_dashboard(
                chat_id, query.from_user, message_id, toggle_inbox=True
            )
            await query.answer()
            return

        if data == "inbox_prev":
            await self._send_dashboard(
                chat_id, query.from_user, message_id, page_shift=-1
            )
            await query.answer()
            return

        if data == "inbox_next":
            await self._send_dashboard(
                chat_id, query.from_user, message_id, page_shift=1
            )
            await query.answer()
            return

        if data == "toggle_tools":
            await self._send_dashboard(
                chat_id, query.from_user, message_id, toggle_tools=True
            )
            await query.answer()
            return

        if data == "refresh":
            await self._send_dashboard(chat_id, query.from_user, message_id)
            await query.answer("Обновлено")
            return

        if data == "toggle_pwd":
            await self._send_dashboard(
                chat_id, query.from_user, message_id, toggle_password=True
            )
            await query.answer()
            return

        if data == "auth_start":
            self._auth_state[chat_id] = {"step": "login"}
            await query.answer()
            await self.application.bot.send_message(
                chat_id=chat_id,
                text="Введите логин (email) почты, в которую хотите войти:",
            )
            return

        if data == "notif_code":
            state = self._notif_state.get((chat_id, message_id))
            if not state or not state.get("code"):
                await query.answer("Код не найден")
                return
            state["code_visible"] = not state["code_visible"]
            await query.edit_message_text(
                text=_build_notification_text(state),
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(_build_notification_keyboard(state)),
            )
            await query.answer()
            return

        if data == "notif_links":
            state = self._notif_state.get((chat_id, message_id))
            if not state or not state.get("links"):
                await query.answer("Ссылок нет")
                return
            state["links_open"] = not state["links_open"]
            await query.edit_message_text(
                text=_build_notification_text(state),
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(_build_notification_keyboard(state)),
            )
            await query.answer()
            return

        if data == "notif_open":
            await self._send_full_email(chat_id, message_id, context.bot)
            await query.answer()
            return

        if data == "change":
            user_record = ensure_user(
                query.from_user.id, query.from_user.full_name, query.from_user.username
            )
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
        self,
        chat_id: int,
        telegram_user,
        message_id: Optional[int] = None,
        toggle_tools: bool = False,
        toggle_password: bool = False,
        toggle_inbox: bool = False,
        page_shift: int = 0,
    ) -> None:
        await self._register_user(telegram_user)
        user_record = ensure_user(
            telegram_user.id, telegram_user.full_name, telegram_user.username
        )
        mailbox = ensure_mailbox_record(user_record["id"])
        address = mailbox["address"]
        created_at = _format_datetime(mailbox["created_at"])
        total = count_messages(mailbox["id"])
        letters = list_messages(mailbox["id"], limit=MESSAGE_FETCH_LIMIT)

        password_visible = self._password_visible.get(chat_id, False)
        if toggle_password:
            password_visible = not password_visible
        self._password_visible[chat_id] = password_visible
        password_display = mailbox["password"] if password_visible else "✱✱✱✱"

        text = (
            f"📫 {escape(address)}\n"
            f"  └ <b>Пароль:</b> <code>{escape(password_display)}</code>\n\n"
            f"<b>┌ Количество писем:</b> <code>{total}</code>\n"
            f"<b>└ Дата создания:</b> <code>{escape(created_at)}</code>"
        )

        inbox_state = self._inbox_state.get(
            chat_id, {"open": False, "page": 0}
        )
        if toggle_inbox:
            inbox_state["open"] = not inbox_state["open"]
        if inbox_state["open"] and page_shift:
            inbox_state["page"] += page_shift
        if not inbox_state["open"]:
            inbox_state["page"] = 0
        total_pages = max(
            1, (len(letters) + MESSAGE_PAGE_SIZE - 1) // MESSAGE_PAGE_SIZE
        )
        inbox_state["page"] = max(
            0, min(inbox_state["page"], total_pages - 1)
        )
        self._inbox_state[chat_id] = inbox_state
        inbox_open = inbox_state["open"]
        current_page = inbox_state["page"]

        keyboard: list[list[InlineKeyboardButton]] = []
        inbox_icon = "⌵" if inbox_open else "⌵"
        keyboard.append(
            [InlineKeyboardButton(f"📧 Входящие {inbox_icon}", callback_data="toggle_inbox")]
        )
        if inbox_open:
            if not letters:
                keyboard.append(
                    [InlineKeyboardButton("Писем нет", callback_data="noop")]
                )
            else:
                start = current_page * MESSAGE_PAGE_SIZE
                page_letters = letters[start : start + MESSAGE_PAGE_SIZE]
                if not page_letters:
                    keyboard.append(
                        [InlineKeyboardButton("Писем нет", callback_data="noop")]
                    )
                else:
                    for mail in page_letters:
                        sender = mail.get("sender") or "Без имени"
                        subject = mail.get("subject") or "(без темы)"
                        title = _short(f"{sender} - {subject}")
                        keyboard.append(
                            [
                                InlineKeyboardButton(
                                    title, callback_data=f"msg:{mail['id']}"
                                )
                            ]
                        )
                    if total_pages > 1:
                        row: list[InlineKeyboardButton] = []
                        if current_page > 0:
                            row.append(
                                InlineKeyboardButton(
                                    "◀️", callback_data="inbox_prev"
                                )
                            )
                        row.append(
                            InlineKeyboardButton(
                                f"{current_page + 1}/{total_pages}",
                                callback_data="noop",
                            )
                        )
                        if current_page < total_pages - 1:
                            row.append(
                                InlineKeyboardButton(
                                    "▶️", callback_data="inbox_next"
                                )
                            )
                        keyboard.append(row)
        tools_open = self._tools_state.get(chat_id, False)
        if toggle_tools:
            tools_open = not tools_open
        self._tools_state[chat_id] = tools_open

        icon = "⌵" if tools_open else "⌵"
        keyboard.append([InlineKeyboardButton(f"🧰 Инструменты {icon}", callback_data="toggle_tools")])
        if tools_open:
            label = "Пароль: видно" if password_visible else "Пароль: не видно"
            keyboard.append([InlineKeyboardButton(label, callback_data="toggle_pwd")])
            keyboard.append([InlineKeyboardButton("👤 Войти в почту", callback_data="auth_start")])
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

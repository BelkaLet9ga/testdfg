from __future__ import annotations

from email.header import decode_header
from email.message import Message
from email.parser import BytesParser
from email.utils import parseaddr

from storage import get_mailbox_by_address, save_email


def _extract_parts(msg: Message) -> tuple[str, str]:
    def decode(part: Message) -> str:
        payload = part.get_payload(decode=True)
        if payload is None:
            return ""
        charset = part.get_content_charset() or "utf-8"
        return payload.decode(charset, errors="replace")

    plain = ""
    html = ""

    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_maintype() != "text":
                continue
            text = decode(part)
            subtype = part.get_content_subtype()
            if subtype == "plain" and not plain:
                plain = text
            elif subtype == "html" and not html:
                html = text
        if not plain and html:
            plain = html
    else:
        text = decode(msg)
        if msg.get_content_subtype() == "html":
            html = text
        else:
            plain = text

    return plain, html


def _decode_header(value: str) -> str:
    if not value:
        return ""
    parts = []
    for text, charset in decode_header(value):
        if isinstance(text, bytes):
            parts.append(text.decode(charset or "utf-8", errors="replace"))
        else:
            parts.append(text)
    return "".join(parts)


class MailHandler:
    def __init__(self, notifier=None):
        self.notifier = notifier

    async def handle_DATA(self, server, session, envelope):
        msg = BytesParser().parsebytes(envelope.content)
        sender_header = msg.get("From", "")
        raw_name, sender_email = parseaddr(sender_header)
        sender_name = _decode_header(raw_name)
        subject = _decode_header(msg.get("Subject", ""))
        body_plain, body_html = _extract_parts(msg)
        raw_headers = "\n".join(f"{k}: {v}" for k, v in msg.items())
        formatted_sender = (
            f"{sender_name} <{sender_email}>"
            if sender_name and sender_email
            else sender_email or sender_header
        )

        for rcpt in envelope.rcpt_tos:
            address = rcpt.lower()
            mailbox = get_mailbox_by_address(address)
            if not mailbox:
                continue
            save_email(
                mailbox["id"],
                sender_name,
                sender_email,
                subject,
                body_plain,
                body_html,
                raw_headers,
            )
            if self.notifier:
                await self.notifier.notify_new_email(
                    address, formatted_sender, subject, body_plain, body_html
                )

        return "250 Message accepted"

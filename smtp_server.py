from __future__ import annotations

from email.message import Message
from email.parser import BytesParser

from storage import save_email


def _extract_body(msg: Message) -> str:
    def decode(part: Message) -> str:
        payload = part.get_payload(decode=True)
        if payload is None:
            return ""
        charset = part.get_content_charset() or "utf-8"
        return payload.decode(charset, errors="replace")

    if msg.is_multipart():
        fallback = ""
        for part in msg.walk():
            if part.get_content_maintype() != "text":
                continue
            text = decode(part)
            if part.get_content_subtype() == "plain":
                return text
            if not fallback and text:
                fallback = text
        return fallback

    return decode(msg)


class MailHandler:
    def __init__(self, notifier=None):
        self.notifier = notifier

    async def handle_DATA(self, server, session, envelope):
        msg = BytesParser().parsebytes(envelope.content)
        sender = msg.get("From", "")
        subject = msg.get("Subject", "")
        body = _extract_body(msg)

        for rcpt in envelope.rcpt_tos:
            address = rcpt.lower()
            save_email(address, sender, subject, body)
            if self.notifier:
                await self.notifier.notify_new_email(address, sender, subject, body)

        return "250 Message accepted"

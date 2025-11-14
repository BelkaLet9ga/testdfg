from __future__ import annotations

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


class MailHandler:
    def __init__(self, notifier=None):
        self.notifier = notifier

    async def handle_DATA(self, server, session, envelope):
        msg = BytesParser().parsebytes(envelope.content)
        sender_header = msg.get("From", "")
        sender_name, sender_email = parseaddr(sender_header)
        subject = msg.get("Subject", "")
        body_plain, body_html = _extract_parts(msg)
        raw_headers = "\n".join(f"{k}: {v}" for k, v in msg.items())

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
                    address, sender_header, subject, body_plain, body_html
                )

        return "250 Message accepted"

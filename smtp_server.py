import asyncio
from datetime import datetime
from email.parser import BytesParser
from aiosmtpd.controller import Controller
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "tempmail.db"


def save_email(recipient, sender, subject, body):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT INTO emails (recipient, sender, subject, body, received_at) VALUES (?, ?, ?, ?, ?)",
        (recipient, sender, subject, body, datetime.utcnow().isoformat())
    )
    conn.commit()
    conn.close()


class MailHandler:
    async def handle_DATA(self, server, session, envelope):
        msg = BytesParser().parsebytes(envelope.content)
        sender = msg.get("From", "")
        subject = msg.get("Subject", "")
        body = msg.get_payload()

        for rcpt in envelope.rcpt_tos:
            save_email(rcpt, sender, subject, body)

        return "250 Message accepted"


if __name__ == "__main__":
    handler = MailHandler()
    controller = Controller(handler, hostname="0.0.0.0", port=25)
    controller.start()

    print("SMTP server running on :25 ...")
    try:
        asyncio.get_event_loop().run_forever()
    except KeyboardInterrupt:
        pass

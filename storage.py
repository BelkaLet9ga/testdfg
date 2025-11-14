import random
import sqlite3
import string
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "tempmail.db"
DOMAIN = "1398hnjfkdskd.de"


def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS emails (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipient TEXT NOT NULL,
            sender TEXT,
            subject TEXT,
            body TEXT,
            received_at TEXT NOT NULL
        )
        """
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS mailboxes (
            user_id TEXT PRIMARY KEY,
            address TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _generate_local_part(length: int = 10) -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "".join(random.choice(alphabet) for _ in range(length))


def ensure_mailbox(user_id: int) -> str:
    """Возвращает существующий ящик пользователя или создаёт новый."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT address FROM mailboxes WHERE user_id=?", (str(user_id),))
    row = cur.fetchone()
    if row:
        conn.close()
        return row["address"]

    while True:
        local = _generate_local_part()
        address = f"{local}@{DOMAIN}"
        try:
            cur.execute(
                "INSERT INTO mailboxes (user_id, address, created_at) VALUES (?, ?, ?)",
                (str(user_id), address.lower(), datetime.utcnow().isoformat()),
            )
            conn.commit()
            conn.close()
            return address
        except sqlite3.IntegrityError:
            # Возможен редкий конфликт адресов -> пробуем снова.
            continue


def get_mailbox(user_id: int) -> str | None:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT address FROM mailboxes WHERE user_id=?", (str(user_id),))
    row = cur.fetchone()
    conn.close()
    return row["address"] if row else None


def get_user_for_address(address: str) -> str | None:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM mailboxes WHERE address=?", (address.lower(),))
    row = cur.fetchone()
    conn.close()
    return row["user_id"] if row else None


def save_email(recipient: str, sender: str, subject: str, body: str) -> None:
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO emails (recipient, sender, subject, body, received_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (recipient.lower(), sender, subject, body, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()


def list_messages(recipient: str, limit: int = 20) -> list[dict]:
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM emails WHERE recipient=? ORDER BY id DESC LIMIT ?",
        (recipient.lower(), limit),
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows

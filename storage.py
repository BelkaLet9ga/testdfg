import random
import sqlite3
import string
from datetime import datetime
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent / "tempmail.db"
DOMAIN = "1398hnjfkdskd.de"

FIRST_NAMES = [
    "alex", "max", "oliver", "daniel", "chris", "peter", "john", "michael", "david", "nick",
    "andrew", "mark", "paul", "luke", "jake", "tom", "harry", "leo", "jack", "ryan",
    "adam", "owen", "eric", "kevin", "bruce", "ethan", "cole", "blake", "grant", "henry",
    "felix", "brian", "isaac", "sam", "liam", "noah", "aaron", "tyler", "vince", "roman",
    "shane", "nate", "caleb", "joel", "ellis", "gavin", "logan", "zack", "quinn", "seth",
]

LAST_NAMES = [
    "smith", "johnson", "williams", "brown", "jones", "miller", "davis", "wilson", "taylor", "anderson",
    "thomas", "jackson", "white", "harris", "martin", "thompson", "garcia", "martinez", "robinson", "clark",
    "rodriguez", "lewis", "lee", "walker", "hall", "allen", "young", "hernandez", "king", "wright",
    "lopez", "hill", "scott", "green", "adams", "baker", "gonzalez", "nelson", "carter", "mitchell",
    "perez", "roberts", "turner", "phillips", "campbell", "parker", "evans", "edwards", "collins", "stewart",
]


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    conn = get_db()
    cur = conn.cursor()
    _maybe_reset_legacy(cur)

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id TEXT NOT NULL UNIQUE,
            name TEXT,
            username TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS mailboxes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            address TEXT NOT NULL UNIQUE,
            password TEXT NOT NULL,
            created_at TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS emails (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mailbox_id INTEGER NOT NULL,
            sender_name TEXT,
            sender_email TEXT,
            subject TEXT,
            body_plain TEXT,
            body_html TEXT,
            raw_headers TEXT,
            received_at TEXT NOT NULL,
            read INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(mailbox_id) REFERENCES mailboxes(id) ON DELETE CASCADE
        )
        """
    )
    _ensure_user_columns(cur)
    conn.commit()
    conn.close()


def _maybe_reset_legacy(cur: sqlite3.Cursor) -> None:
    """Если в БД старая схема, дропаем таблицы, чтобы создать новую структуру."""
    try:
        cur.execute("PRAGMA table_info(mailboxes)")
        columns = [row[1] for row in cur.fetchall()]
    except sqlite3.OperationalError:
        columns = []

    if columns and "password" not in columns:
        cur.execute("DROP TABLE IF EXISTS emails")
        cur.execute("DROP TABLE IF EXISTS mailboxes")
    try:
        cur.execute("PRAGMA table_info(users)")
    except sqlite3.OperationalError:
        cur.execute("DROP TABLE IF EXISTS users")


def _ensure_user_columns(cur: sqlite3.Cursor) -> None:
    try:
        cur.execute("PRAGMA table_info(users)")
    except sqlite3.OperationalError:
        return
    columns = {row[1] for row in cur.fetchall()}
    if "username" not in columns:
        cur.execute("ALTER TABLE users ADD COLUMN username TEXT")


def ensure_user(
    telegram_id: int, name: Optional[str] = None, username: Optional[str] = None
) -> dict:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE telegram_id=?", (str(telegram_id),))
    row = cur.fetchone()
    if row:
        updates = []
        params = []
        if name and row["name"] != name:
            updates.append("name=?")
            params.append(name)
        if username and row["username"] != username:
            updates.append("username=?")
            params.append(username)
        if updates:
            cur.execute(
                f"UPDATE users SET {', '.join(updates)} WHERE id=?",
                (*params, row["id"]),
            )
            conn.commit()
        conn.close()
        return dict(row)

    now = datetime.utcnow().isoformat()
    cur.execute(
        "INSERT INTO users (telegram_id, name, username, created_at) VALUES (?, ?, ?, ?)",
        (str(telegram_id), name, username, now),
    )
    conn.commit()
    cur.execute("SELECT * FROM users WHERE telegram_id=?", (str(telegram_id),))
    created = cur.fetchone()
    conn.close()
    return dict(created)


def _generate_password(length: int = 24) -> str:
    alphabet = string.ascii_letters + string.digits + string.punctuation
    rng = random.SystemRandom()
    while True:
        pwd = "".join(rng.choice(alphabet) for _ in range(length))
        if (
            any(c.islower() for c in pwd)
            and any(c.isupper() for c in pwd)
            and any(c.isdigit() for c in pwd)
            and any(c in string.punctuation for c in pwd)
        ):
            return pwd


def _generate_address() -> str:
    first = random.choice(FIRST_NAMES)
    last = random.choice(LAST_NAMES)
    digits = "".join(random.choice(string.digits) for _ in range(random.randint(2, 4)))
    local = f"{first}.{last}{digits}"
    return f"{local}@{DOMAIN}".lower()


def ensure_mailbox_record(user_id: int) -> dict:
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
         "SELECT * FROM mailboxes WHERE user_id=? AND active=1 ORDER BY id DESC LIMIT 1",
        (user_id,),
    )
    row = cur.fetchone()
    if row:
        conn.close()
        return dict(row)

    info = _create_mailbox(cur, user_id)
    conn.commit()
    conn.close()
    return info


def _create_mailbox(cur: sqlite3.Cursor, user_id: int) -> dict:
    while True:
        address = _generate_address()
        password = _generate_password()
        created_at = datetime.utcnow().isoformat()
        try:
            cur.execute(
                """
                INSERT INTO mailboxes (user_id, address, password, created_at, active)
                VALUES (?, ?, ?, ?, 1)
                """,
                (user_id, address, password, created_at),
            )
            cur.connection.commit()
            cur.execute(
                "SELECT * FROM mailboxes WHERE address=?", (address,)
            )
            return dict(cur.fetchone())
        except sqlite3.IntegrityError:
            continue


def change_mailbox(user_id: int) -> dict:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM mailboxes WHERE user_id=?", (user_id,))
    conn.commit()
    info = _create_mailbox(cur, user_id)
    conn.commit()
    conn.close()
    return info


def get_mailbox_record(user_id: int) -> Optional[dict]:
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM mailboxes WHERE user_id=? AND active=1 ORDER BY id DESC LIMIT 1",
        (user_id,),
    )
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def get_mailbox_by_address(address: str) -> Optional[dict]:
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM mailboxes WHERE address=? AND active=1",
        (address.lower(),),
    )
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def attach_mailbox(user_id: int, address: str, password: str) -> Optional[dict]:
    """Переназначает существующий ящик пользователю, если пароль совпадает."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM mailboxes WHERE address=? AND password=? AND active=1",
        (address.lower(), password),
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        return None
    # деактивируем прежние ящики пользователя
    cur.execute("UPDATE mailboxes SET active=0 WHERE user_id=?", (user_id,))
    cur.execute(
        "UPDATE mailboxes SET user_id=?, active=1 WHERE id=?",
        (user_id, row["id"]),
    )
    conn.commit()
    cur.execute("SELECT * FROM mailboxes WHERE id=?", (row["id"],))
    updated = cur.fetchone()
    conn.close()
    return dict(updated)


def get_user_for_address(address: str) -> Optional[dict]:
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT users.id as user_id, users.telegram_id, users.name, users.username, mailboxes.id as mailbox_id
        FROM mailboxes
        JOIN users ON users.id = mailboxes.user_id
        WHERE mailboxes.address=? AND mailboxes.active=1
        """,
        (address.lower(),),
    )
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def save_email(
    mailbox_id: int,
    sender_name: Optional[str],
    sender_email: Optional[str],
    subject: Optional[str],
    body_plain: Optional[str],
    body_html: Optional[str],
    raw_headers: Optional[str],
) -> None:
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO emails (
            mailbox_id, sender_name, sender_email, subject,
            body_plain, body_html, raw_headers, received_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            mailbox_id,
            sender_name,
            sender_email,
            subject,
            body_plain,
            body_html,
            raw_headers,
            datetime.utcnow().isoformat(),
        ),
    )
    conn.commit()
    conn.close()


def count_messages(mailbox_id: int) -> int:
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(1) as total FROM emails WHERE mailbox_id=?",
        (mailbox_id,),
    )
    row = cur.fetchone()
    conn.close()
    return int(row["total"]) if row else 0


def list_messages(mailbox_id: int, limit: int = 20) -> list[dict]:
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, sender_name, sender_email, subject, body_plain, received_at
        FROM emails
        WHERE mailbox_id=?
        ORDER BY id DESC
        LIMIT ?
        """,
        (mailbox_id, limit),
    )
    rows = []
    for row in cur.fetchall():
        rows.append(
            {
                "id": row["id"],
                "subject": row["subject"],
                "sender_name": row["sender_name"],
                "sender_email": row["sender_email"],
                "sender": row["sender_name"] or row["sender_email"],
                "body": row["body_plain"],
                "received_at": row["received_at"],
            }
        )
    conn.close()
    return rows


def get_message(message_id: int) -> Optional[dict]:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM emails WHERE id=?", (message_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    data = dict(row)
    data["sender"] = data.get("sender_name") or data.get("sender_email")
    data["body"] = data.get("body_plain")
    return data

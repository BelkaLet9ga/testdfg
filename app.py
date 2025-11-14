import sqlite3
from datetime import datetime
from pathlib import Path
import random
import string

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from contextlib import asynccontextmanager

DB_PATH = Path(__file__).parent / "tempmail.db"
DOMAIN = "1398hnjfkdskd.de"


def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS emails (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipient TEXT NOT NULL,
            sender TEXT,
            subject TEXT,
            body TEXT,
            received_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()  # ← заменяет старый startup event
    yield


app = FastAPI(title="TempMail", lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
async def index():
    local = "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(10))
    email = f"{local}@{DOMAIN}"

    html = f"""
    <html>
    <head>
        <title>TempMail</title>
        <style>
            body {{
                font-family: Arial, sans-serif;
                margin: 0;
                background: #0f172a;
                color: white;
            }}
            .wrap {{
                max-width: 900px;
                margin: 40px auto;
                background: #1e293b;
                padding: 30px;
                border-radius: 16px;
                box-shadow: 0 0 25px rgba(0,0,0,0.45);
            }}
            input {{
                width: 100%;
                padding: 14px;
                border-radius: 10px;
                border: none;
                margin-bottom: 20px;
                font-size: 20px;
                background: #334155;
                color: white;
            }}
            h1 {{
                margin-bottom: 10px;
            }}
            .msg {{
                background: #334155;
                padding: 18px;
                margin-top: 15px;
                border-radius: 12px;
            }}
            .from {{
                color: #93c5fd;
                font-size: 15px;
                margin-bottom: 8px;
            }}
            .subject {{
                font-size: 20px;
                margin-bottom: 12px;
                font-weight: bold;
            }}
        </style>
    </head>
    <body>
        <div class="wrap">
            <h1>TempMail</h1>
            <p>Ваш временный адрес:</p>
            <input value="{email}" readonly>

            <h2>Inbox</h2>
            <div id="inbox">Загрузка...</div>
        </div>

        <script>
        async function loadMail() {{
            let r = await fetch("/messages?email={local}");
            let data = await r.json();
            let box = document.getElementById("inbox");
            box.innerHTML = "";

            if (data.length === 0) {{
                box.innerHTML = "<i>Нет писем</i>";
                return;
            }}

            data.forEach(msg => {{
                box.innerHTML += `
                    <div class='msg'>
                        <div class='subject'>${{msg.subject}}</div>
                        <div class='from'>From: ${{msg.sender}}</div>
                        <div>${{msg.body}}</div>
                    </div>`;
            }});
        }}

        loadMail();
        setInterval(loadMail, 3000);
        </script>
    </body>
    </html>
    """

    return HTMLResponse(html)


@app.get("/messages")
async def get_messages(email: str):
    local = email.split("@")[0]
    full = f"{local}@{DOMAIN}"

    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM emails WHERE recipient=? ORDER BY id DESC", (full,))
    data = [dict(r) for r in c.fetchall()]
    conn.close()
    return data

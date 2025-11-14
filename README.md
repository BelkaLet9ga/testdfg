# Temp Mailer MVP

Простой учебный сервис одноразовой почты:

- HTTP/UI часть на FastAPI (`app.py`) генерирует временный адрес и показывает поступившие письма.
- SMTP‑приёмник (`smtp_server.py`) принимает письма через `aiosmtpd` и пишет их в SQLite.
- Скрипт `run.py` запускает оба процесса одной командой.

## Подготовка окружения

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install fastapi uvicorn aiosmtpd
```

## Запуск

```powershell
python run.py
```

- HTTP часть доступна по умолчанию на `http://127.0.0.1:8000`.
- SMTP слушает порт `25` на всех интерфейсах.

Остановить сервисы можно `Ctrl+C`.

## Публикация в Git

Репозиторий уже инициализирован локально (`master`). Чтобы отправить код в новый Git‑репозиторий:

```powershell
git remote add origin <ssh-или-https-URL>
git branch -M main
git push -u origin main
```

# Temp Mailer Bot

MVP принимает письма через SMTP и пересылает их в Telegram-бота. Для каждого пользователя создаётся свой временный ящик, новые письма сразу отправляются в чат.

## Стек
- SMTP сервер: `aiosmtpd`
- Telegram-бот: `python-telegram-bot`
- Хранилище: SQLite (`tempmail.db`)

## Подготовка
```powershell
python -m venv .venv
.venv\Scripts\activate
pip install aiosmtpd python-telegram-bot
```

Укажите токен бота через переменную среды, либо оставьте значение по умолчанию из `run.py`:
```powershell
$env:TELEGRAM_TOKEN = "8476649791:xxxxxxxxxxxx"
```

## Запуск
```powershell
python run.py
```

- SMTP по умолчанию слушает `0.0.0.0:25`.
- Бот начинает polling и отвечает на команды `/start` и `/inbox`.

Остановить можно `Ctrl+C`. При запуске на сервере оберните команду в systemd/tmux/supervisor.

## Работа с Git
Репозиторий уже инициализирован. Чтобы отправить код на удалённый Git:
```powershell
git remote add origin <ssh-или-https-URL>
git branch -M main
git push -u origin main
```

На сервере:
```bash
git clone <ssh-или-https-URL> tempmail
cd tempmail
git pull   # для обновлений
```

# Запуск на Mac (для тестирования)

## Требования

- Python 3.11+
- Telegram бот (создать через @BotFather)

## Шаги

### 1. Установить зависимости

```bash
cd /путь/к/diwo-watchdog
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Создать Fernet-ключ (один раз)

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Скопируйте вывод — это ваш `SECRETS_ENCRYPTION_KEY`.

### 3. Создать файл .env

```bash
cp .env.example .env
```

Откройте `.env` и заполните:
```
BOT_TOKEN=токен_от_BotFather
SECRETS_ENCRYPTION_KEY=ключ_из_шага_2
LOG_LEVEL=INFO
```

### 4. Запустить

```bash
python3 main.py
```

Бот начнёт работать. Откройте бота в Telegram и нажмите /start.

### 5. Остановить

`Ctrl+C`

---

## Проверка работы

1. Добавьте источник через кнопку «📡 Добавить источник»
2. Дождитесь первого опроса (согласно выбранному интервалу)
3. Проверьте снапшоты:
```bash
sqlite3 watchdog.db "SELECT * FROM radar_snapshots ORDER BY ts DESC LIMIT 5;"
```
4. Проверьте виджеты:
```bash
sqlite3 watchdog.db "SELECT * FROM user_widgets;"
```

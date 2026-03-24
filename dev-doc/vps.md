# Запуск на VPS (Linux)

## Требования

- Ubuntu 22.04+ / Debian 12+
- Python 3.11+

## Установка

### 1. Установить Python 3.11+

```bash
sudo apt update && sudo apt install -y python3.11 python3.11-venv python3-pip
```

### 2. Скопировать файлы на сервер

```bash
scp -r diwo-tg-watchdog/ user@your-server:/opt/diwo-tg-watchdog/
```

Или через git:
```bash
git clone <repo> /opt/diwo-tg-watchdog
```

### 3. Установить зависимости

```bash
cd /opt/diwo-tg-watchdog
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 4. Создать Fernet-ключ (один раз)

```bash
source venv/bin/activate
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### 5. Создать .env

```bash
cp .env.example .env
nano .env
```

Заполните:
```
BOT_TOKEN=токен_от_BotFather
SECRETS_ENCRYPTION_KEY=ключ_из_шага_4
LOG_LEVEL=INFO
DB_PATH=/opt/diwo-tg-watchdog/watchdog.db
```

### 6. Создать systemd-сервис

```bash
sudo nano /etc/systemd/system/diwo-watchdog.service
```

Содержимое:
```ini
[Unit]
Description=Diwo TG Watchdog
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/opt/diwo-tg-watchdog
EnvironmentFile=/opt/diwo-tg-watchdog/.env
ExecStart=/opt/diwo-tg-watchdog/venv/bin/python main.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

### 7. Запустить сервис

```bash
sudo systemctl daemon-reload
sudo systemctl enable diwo-watchdog
sudo systemctl start diwo-watchdog
```

### 8. Проверить статус

```bash
sudo systemctl status diwo-watchdog
sudo journalctl -u diwo-watchdog -f
```

---

## Обновление

```bash
cd /opt/diwo-tg-watchdog
git pull  # или скопируйте новые файлы
sudo systemctl restart diwo-watchdog
```

---

## Резервная копия БД

```bash
cp /opt/diwo-tg-watchdog/watchdog.db /backup/watchdog-$(date +%Y%m%d).db
```

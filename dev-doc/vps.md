# Запуск на VPS (Linux)

## Требования

- Ubuntu 22.04+ / Debian 12+
- Python 3.11+

---

## Установка

### 1. Установить Python 3.11+

```bash
sudo apt install -y python3 python3-venv python3-pip
```

---

### 2. Скопировать файлы на сервер

Выберите один из способов:

#### Способ А — через SCP (с вашего Mac)

Выполните на **вашем Mac**, не на сервере:

```bash
scp -r /путь/к/diwo-tg-watchdog user@IP_СЕРВЕРА:/opt/diwo-tg-watchdog
```

Например:
```bash
scp -r ~/Documents/diwo-tg-watchdog user@123.45.67.89:/opt/diwo-tg-watchdog
```

Если подключаетесь по ключу:
```bash
scp -i ~/.ssh/id_rsa -r ~/Documents/diwo-tg-watchdog user@123.45.67.89:/opt/diwo-tg-watchdog
```

#### Способ Б — через GitHub (на сервере)

```bash
git clone https://github.com/Streamdoge/diwo-watchdog.git /opt/diwo-tg-watchdog
```

Для приватного репозитория используйте Personal Access Token:
```bash
git clone https://<TOKEN>@github.com/Streamdoge/diwo-watchdog.git /opt/diwo-tg-watchdog
```

Токен создаётся на GitHub: Settings → Developer settings → Personal access tokens → scope: `repo`.

---

### 3. Установить зависимости

```bash 
cd /opt/diwo-watchdog
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

---

### 4. Создать Fernet-ключ (один раз)

```bash
source venv/bin/activate
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Сохраните вывод — это ваш `SECRETS_ENCRYPTION_KEY`.

---

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
DB_PATH=/opt/diwo-watchdog/watchdog.db
```

---

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
WorkingDirectory=/opt/diwo-watchdog
EnvironmentFile=/opt/diwo-watchdog/.env
ExecStart=/opt/diwo-watchdog/venv/bin/python main.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

---

### 7. Выдать права на папку

Сервис запускается от пользователя `www-data` — он должен иметь доступ к папке проекта:

```bash
chown -R www-data:www-data /opt/diwo-watchdog/
```

---

### 8. Запустить сервис

```bash
sudo systemctl daemon-reload
sudo systemctl enable diwo-watchdog
sudo systemctl start diwo-watchdog
```

---

### 9. Проверить статус

```bash
sudo systemctl status diwo-watchdog
sudo journalctl -u diwo-watchdog -f
```

---

## Обновление

#### Если деплоили через SCP

Скопируйте новые файлы с Mac:
```bash
scp -r ~/Documents/diwo-tg-watchdog user@IP_СЕРВЕРА:/opt/diwo-tg-watchdog
```

Затем на сервере:
```bash
sudo systemctl restart diwo-watchdog
```

#### Если деплоили через GitHub

```bash
cd /opt/diwo-tg-watchdog
git pull
sudo systemctl restart diwo-watchdog
```

---

## Резервная копия БД

```bash
cp /opt/diwo-tg-watchdog/watchdog.db /backup/watchdog-$(date +%Y%m%d).db
```

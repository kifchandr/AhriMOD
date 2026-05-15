# Установка Ahrimod на Ubuntu сервере

Инструкция для **Ubuntu 22.04 LTS / 24.04 LTS**. На других дистрибутивах принципы те же, могут отличаться имена пакетов.

Бот будет работать:
- под **отдельным системным пользователем** (безопасность)
- через **systemd** (автозапуск при загрузке, автоматический перезапуск при сбоях)
- в **изолированном venv** (без конфликтов с системным Python)
- логи — в **journald** (`journalctl -u ahrimod`)

---

## 0. Настройка в Telegram (до того как ставить на сервер)

Эти шаги нужны независимо от того, где будет крутиться бот.

### 0.1. Создать бота

У `@BotFather`:
- `/newbot` → задать имя и username → получить **BOT_TOKEN** (он пойдёт в `.env`)
- `/setprivacy` → выбрать своего бота → **Disable**. Без этого бот не увидит обычные сообщения в группах.

### 0.2. Создать админский чат

Понадобится приватный чат куда бот будет слать сообщения на ручную модерацию. Создай отдельную **группу** (можно из 2-3 человек — модераторы), добавь туда бота. Узнай её ID (например, переслав сообщение из неё в `@userinfobot`). Это **ADMIN_CHAT_ID**.

Туда же удобно слать audit-лог — тогда `LOG_CHAT_ID` ставится тем же значением.

### 0.3. Добавить бота в модерируемые чаты

Бота нужно добавить **админом** в каждый чат, который он будет модерировать. Минимально нужные права:

- **Delete Messages** — удалять спам
- **Ban Users** — банить нарушителей
- **Restrict Users** — мутить (если `NEW_USER_PUNISHMENT=mute` в конфиге)

**Сколько чатов добавлять — зависит от структуры:**

| Что у тебя | Куда добавлять бота | Что писать в `PROTECTED_CHAT_IDS` |
|---|---|---|
| Одна **супергруппа с темами** (форум, разделы внутри) | Один раз в саму группу, как админа | Один ID этой группы |
| **Канал + группа обсуждений** (комментарии) | Только в **группу обсуждений** как админа | ID группы обсуждений |
| **Несколько отдельных групп** | В каждую как админа | Все ID через запятую |

ID супергруппы выглядит как `-100xxxxxxxxxx` (10–13 цифр после `-100`). Узнать его проще всего, переслав сообщение из чата в `@userinfobot` или `@getidsbot`.

### 0.4. Узнать свой Telegram ID

Чтобы быть админом бота, нужно знать свой `user_id` — пиши `@userinfobot`, он скажет. Это число пойдёт в `ADMIN_USER_IDS`.

Все ID и токен сложи в блокнот — пригодятся при заполнении `.env` на шаге 5.

---

## 1. Подготовка сервера

Подключаемся по SSH под учёткой с `sudo`:

```bash
ssh user@your-server-ip
```

Обновляем систему и ставим зависимости:

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-venv python3-pip git nano
```

Проверяем версию Python — нужна **3.10+** (в Ubuntu 22.04 идёт 3.10, в 24.04 — 3.12):

```bash
python3 --version
```

---

## 2. Создаём отдельного пользователя

Не запускаем бота от root. Создадим системного пользователя без shell-доступа:

```bash
sudo useradd --system --create-home --home-dir /opt/ahrimod --shell /usr/sbin/nologin ahrimod
```

Проверяем, что папка создалась:

```bash
ls -ld /opt/ahrimod
# drwxr-x--- 2 ahrimod ahrimod 4096 ... /opt/ahrimod
```

---

## 3. Заливаем код проекта

Вариант **A** (через scp с твоей машины):

```bash
# на ЛОКАЛЬНОЙ машине, в папке где лежит ahrimod.zip:
scp ahrimod.zip user@your-server-ip:/tmp/

# на СЕРВЕРЕ:
sudo apt install -y unzip
sudo unzip /tmp/ahrimod.zip -d /tmp/
sudo cp -r /tmp/ahrimod/. /opt/ahrimod/
sudo rm -rf /tmp/ahrimod /tmp/ahrimod.zip
```

Вариант **B** (через git, если выложен в репозиторий):

```bash
sudo -u ahrimod git clone https://github.com/yourname/ahrimod.git /opt/ahrimod
```

Выставляем владельца (даже если копировали через cp):

```bash
sudo chown -R ahrimod:ahrimod /opt/ahrimod
```

---

## 4. Создаём venv и ставим зависимости

```bash
sudo -u ahrimod python3 -m venv /opt/ahrimod/venv
sudo -u ahrimod /opt/ahrimod/venv/bin/pip install --upgrade pip
sudo -u ahrimod /opt/ahrimod/venv/bin/pip install -r /opt/ahrimod/requirements.txt
```

Проверим, что бот импортируется без ошибок:

```bash
sudo -u ahrimod /opt/ahrimod/venv/bin/python -c "from bot.config import Settings; print('OK')"
```

(Эта команда упадёт с ошибкой про отсутствие BOT_TOKEN — это нормально, мы ещё не создали `.env`. Главное чтобы не было `ImportError`.)

---

## 5. Настраиваем `.env`

```bash
sudo -u ahrimod cp /opt/ahrimod/.env.example /opt/ahrimod/.env
sudo -u ahrimod nano /opt/ahrimod/.env
```

Заполняем все нужные поля (`BOT_TOKEN`, `ADMIN_CHAT_ID`, `PROTECTED_CHAT_IDS`, `ADMIN_USER_IDS` и т.д.).

После сохранения ограничиваем права на файл — там токен бота:

```bash
sudo chmod 600 /opt/ahrimod/.env
sudo chown ahrimod:ahrimod /opt/ahrimod/.env
```

---

## 6. Создаём папку для БД

```bash
sudo -u ahrimod mkdir -p /opt/ahrimod/data
```

(При первом запуске SQLite сам создаст файл `data/bot.db`.)

---

## 7. Тестовый запуск (вручную)

Прежде чем ставить в автозапуск, проверим что бот стартует. **Важно**: перед запуском от руки нужно сменить cwd на папку проекта, иначе `.env` будет искаться в твоём домашнем каталоге. Через systemd этого делать не нужно — у unit-файла прописан `WorkingDirectory`.

```bash
sudo -u ahrimod sh -c 'cd /opt/ahrimod && venv/bin/python main.py'
```

В логах должны появиться строки про подключение к БД и `Бот запущен`. Останавливаем по `Ctrl+C`.

Если есть ошибки — правим `.env` или код, прежде чем идти дальше.

---

## 8. Устанавливаем systemd unit

В архиве лежит готовый файл `ahrimod.service`. Копируем его в systemd:

```bash
sudo cp /opt/ahrimod/ahrimod.service /etc/systemd/system/ahrimod.service
sudo systemctl daemon-reload
```

Включаем автозапуск при перезагрузке сервера и стартуем:

```bash
sudo systemctl enable ahrimod
sudo systemctl start ahrimod
```

Проверяем что бот живой:

```bash
sudo systemctl status ahrimod
```

Должно быть `Active: active (running)`.

---

## 9. Просмотр логов

Все `print` и `logging` бота уходят в journald:

```bash
# последние логи
sudo journalctl -u ahrimod -n 100

# в реальном времени (как tail -f)
sudo journalctl -u ahrimod -f

# только за последний час
sudo journalctl -u ahrimod --since "1 hour ago"

# только ошибки
sudo journalctl -u ahrimod -p err
```

---

## 10. Управление сервисом

| Команда | Что делает |
|---|---|
| `sudo systemctl start ahrimod` | Запустить |
| `sudo systemctl stop ahrimod` | Остановить |
| `sudo systemctl restart ahrimod` | Перезапустить |
| `sudo systemctl status ahrimod` | Проверить статус |
| `sudo systemctl enable ahrimod` | Включить автозапуск при загрузке |
| `sudo systemctl disable ahrimod` | Выключить автозапуск |

---

## 11. Обновление бота

После изменения кода:

```bash
# заливаем новые файлы (через scp или git pull)
sudo -u ahrimod git -C /opt/ahrimod pull       # если через git

# если поменялись зависимости
sudo -u ahrimod /opt/ahrimod/venv/bin/pip install -r /opt/ahrimod/requirements.txt

# рестартуем
sudo systemctl restart ahrimod
sudo systemctl status ahrimod
```

---

## 12. Бэкап БД

База — обычный SQLite-файл в `/opt/ahrimod/data/bot.db`. Простейший бэкап через cron:

```bash
sudo crontab -e
```

Добавляем строку (бэкап каждый день в 4:00 в `/var/backups/ahrimod/`):

```cron
0 4 * * * mkdir -p /var/backups/ahrimod && sqlite3 /opt/ahrimod/data/bot.db ".backup '/var/backups/ahrimod/bot-$(date +\%F).db'" && find /var/backups/ahrimod -name 'bot-*.db' -mtime +30 -delete
```

(Хранит бэкапы 30 дней, потом удаляет старые.) Если `sqlite3` не установлен:

```bash
sudo apt install -y sqlite3
```

---

## Решение типичных проблем

**`status` показывает `failed`, в логах `BOT_TOKEN` не задан.**
Не отредактирован `.env` или у systemd нет прав его прочитать. Проверь `chown ahrimod:ahrimod /opt/ahrimod/.env` и `chmod 600`.

**Бот стартует, но не видит сообщения в группе.**
В BotFather вызови `/setprivacy` для своего бота и выбери `Disable`. Затем перезапусти бота.

**Бот не может удалять сообщения / банить.**
Сделай бота админом нужного чата с правами «Удалять сообщения» и «Банить пользователей».

**`ProtectSystem=strict`: ошибки записи.**
В unit-файле раздел `ReadWritePaths=` указывает только `/opt/ahrimod/data`. Если бот пишет куда-то ещё — добавь путь туда же или поменяй настройку. Логи всегда идут в journald, файлам логов отдельные пути не нужны.

**Хочу видеть, что бот сделал последние сутки.**
```bash
sudo journalctl -u ahrimod --since "24 hours ago" | less
```

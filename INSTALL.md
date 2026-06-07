# Установка Ahrimod на Ubuntu сервере

Инструкция для **Ubuntu 22.04 LTS / 24.04 LTS**. На других дистрибутивах принципы те же, могут отличаться имена пакетов.

Бот разворачивается в `/root/AhriMOD`, работает:
- от пользователя **root** (проект лежит в `/root`)
- через **systemd** (автозапуск при загрузке, автоматический перезапуск при сбоях)
- в **изолированном venv** (`/root/AhriMOD/venv`, без конфликтов с системным Python)
- логи — в **journald** (`journalctl -u ahrimod`)

Репозиторий: `https://github.com/kifchandr/AhriMOD`

---

## ⚡ Быстрое обновление с GitHub

Если бот уже установлен и `/root/AhriMOD` — это git-клон, обновление сводится к трём командам:

```bash
cd /root/AhriMOD
systemctl stop ahrimod
git pull

# только если менялись зависимости (requirements.txt):
venv/bin/pip install -r requirements.txt

systemctl start ahrimod
journalctl -u ahrimod -f       # убедиться, что поднялся без ошибок
```

Миграции БД (новые колонки) применяются автоматически при старте — руками базу трогать не нужно, данные не теряются.

> **Важно:** `.env`, `data/` и `venv/` должны быть в `.gitignore`, иначе `git pull` может их затронуть. Проверь, что в репозитории есть `.gitignore` со строками:
> ```
> .env
> venv/
> data/
> __pycache__/
> *.pyc
> *.db
> *.db-wal
> *.db-shm
> ```
> Если `.env` или `data/` уже попали в репозиторий, перестань их отслеживать (на машине с доступом на push):
> ```bash
> git rm --cached .env
> git rm -r --cached data venv
> git add .gitignore && git commit -m "Stop tracking secrets and data" && git push
> ```
> Если `.env` лежал в публичном репозитории — токен скомпрометирован, отзови его в `@BotFather` → `/revoke` и впиши новый в `.env`.

Если `/root/AhriMOD` ещё **не** git-клон — см. раздел [«Заливаем код проекта»](#2-заливаем-код-проекта) ниже.

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

> Если у админ-чата включены **Темы** (форум) и ты хочешь постить в конкретную тему — укажи её ID в `ADMIN_CHAT_THREAD_ID`. Если темы не используешь, оставь `0`, иначе все отправки в админ-чат будут падать с `message thread not found`.

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

Все ID и токен сложи в блокнот — пригодятся при заполнении `.env` на шаге 4.

---

## 1. Подготовка сервера

Подключаемся по SSH под root:

```bash
ssh root@your-server-ip
```

Обновляем систему и ставим зависимости:

```bash
apt update && apt upgrade -y
apt install -y python3 python3-venv python3-pip git nano
```

Проверяем версию Python — нужна **3.10+** (в Ubuntu 22.04 идёт 3.10, в 24.04 — 3.12):

```bash
python3 --version
```

---

## 2. Заливаем код проекта

Вариант **A** (через git — рекомендуется, так потом обновляться одной командой):

```bash
git clone https://github.com/kifchandr/AhriMOD.git /root/AhriMOD
```

Вариант **B** (через scp с твоей машины):

```bash
# на ЛОКАЛЬНОЙ машине, в папке где лежит ahrimod.zip:
scp ahrimod.zip root@your-server-ip:/tmp/

# на СЕРВЕРЕ:
apt install -y unzip
unzip /tmp/ahrimod.zip -d /tmp/
cp -r /tmp/ahrimod/. /root/AhriMOD/
rm -rf /tmp/ahrimod /tmp/ahrimod.zip
```

---

## 3. Создаём venv и ставим зависимости

```bash
python3 -m venv /root/AhriMOD/venv
/root/AhriMOD/venv/bin/pip install --upgrade pip
/root/AhriMOD/venv/bin/pip install -r /root/AhriMOD/requirements.txt
```

Проверим, что бот импортируется без ошибок:

```bash
cd /root/AhriMOD && venv/bin/python -c "from bot.config import Settings; print('OK')"
```

(Эта команда упадёт с ошибкой про отсутствие BOT_TOKEN — это нормально, мы ещё не создали `.env`. Главное чтобы не было `ImportError`.)

---

## 4. Настраиваем `.env`

```bash
cp /root/AhriMOD/.env.example /root/AhriMOD/.env
nano /root/AhriMOD/.env
```

Заполняем все нужные поля (`BOT_TOKEN`, `ADMIN_CHAT_ID`, `PROTECTED_CHAT_IDS`, `ADMIN_USER_IDS` и т.д.).

После сохранения ограничиваем права на файл — там токен бота:

```bash
chmod 600 /root/AhriMOD/.env
```

---

## 5. Создаём папку для БД

```bash
mkdir -p /root/AhriMOD/data
```

(При первом запуске SQLite сам создаст файл `data/bot.db`.)

---

## 6. Тестовый запуск (вручную)

Прежде чем ставить в автозапуск, проверим что бот стартует. **Важно**: перед запуском от руки нужно сменить cwd на папку проекта, иначе `.env` будет искаться в твоём домашнем каталоге. Через systemd этого делать не нужно — у unit-файла прописан `WorkingDirectory`.

```bash
cd /root/AhriMOD && venv/bin/python main.py
```

В логах должны появиться строки про подключение к БД и `Бот запущен`. Останавливаем по `Ctrl+C`.

Если есть ошибки — правим `.env` или код, прежде чем идти дальше.

---

## 7. Устанавливаем systemd unit

В репозитории лежит готовый файл `ahrimod.service` (уже настроен на `/root/AhriMOD`, root и venv). Копируем его в systemd:

```bash
cp /root/AhriMOD/ahrimod.service /etc/systemd/system/ahrimod.service
systemctl daemon-reload
```

Включаем автозапуск при перезагрузке сервера и стартуем:

```bash
systemctl enable ahrimod
systemctl start ahrimod
```

Проверяем что бот живой:

```bash
systemctl status ahrimod
```

Должно быть `Active: active (running)`.

---

## 8. Просмотр логов

Все `print` и `logging` бота уходят в journald:

```bash
# последние логи
journalctl -u ahrimod -n 100

# в реальном времени (как tail -f)
journalctl -u ahrimod -f

# только за последний час
journalctl -u ahrimod --since "1 hour ago"

# только ошибки
journalctl -u ahrimod -p err
```

---

## 9. Управление сервисом

| Команда | Что делает |
|---|---|
| `systemctl start ahrimod` | Запустить |
| `systemctl stop ahrimod` | Остановить |
| `systemctl restart ahrimod` | Перезапустить |
| `systemctl status ahrimod` | Проверить статус |
| `systemctl enable ahrimod` | Включить автозапуск при загрузке |
| `systemctl disable ahrimod` | Выключить автозапуск |

---

## 10. Обновление бота

См. блок [«⚡ Быстрое обновление с GitHub»](#-быстрое-обновление-с-github) в начале файла. Если коротко:

```bash
cd /root/AhriMOD
systemctl stop ahrimod
git pull
# при изменении зависимостей:
venv/bin/pip install -r requirements.txt
systemctl start ahrimod
systemctl status ahrimod
```

---

## 11. Бэкап БД

Бот сам шлёт ежедневный бэкап БД в Telegram (см. `BACKUP_*` в `.env`). Дополнительно можно держать локальные копии через cron.

База — обычный SQLite-файл в `/root/AhriMOD/data/bot.db`. Простейший бэкап через cron:

```bash
crontab -e
```

Добавляем строку (бэкап каждый день в 4:00 в `/var/backups/ahrimod/`):

```cron
0 4 * * * mkdir -p /var/backups/ahrimod && sqlite3 /root/AhriMOD/data/bot.db ".backup '/var/backups/ahrimod/bot-$(date +\%F).db'" && find /var/backups/ahrimod -name 'bot-*.db' -mtime +30 -delete
```

(Хранит бэкапы 30 дней, потом удаляет старые.) Если `sqlite3` не установлен:

```bash
apt install -y sqlite3
```

---

## Решение типичных проблем

**`status` показывает `failed`, в логах `BOT_TOKEN` не задан.**
Не отредактирован `.env` или его не видно. Проверь, что файл лежит в `/root/AhriMOD/.env` и `chmod 600 /root/AhriMOD/.env`.

**Бот стартует, но не видит сообщения в группе.**
В BotFather вызови `/setprivacy` для своего бота и выбери `Disable`. Затем убери и заново добавь бота в чат и перезапусти сервис.

**Бот не может удалять сообщения / банить.**
Сделай бота админом нужного чата с правами «Удалять сообщения» и «Банить пользователей».

**В логах `message thread not found`, модерация не доходит до админ-чата.**
В `.env` `ADMIN_CHAT_THREAD_ID` (или `LOG_CHAT_THREAD_ID`) указывает на несуществующую тему форума. Поставь `0`, если не используешь темы, либо впиши ID реально существующей открытой темы. После правки — `systemctl restart ahrimod`.

**Сервис не стартует после смены `ExecStart`.**
Проверь, что путь к интерпретатору верный: при venv это `/root/AhriMOD/venv/bin/python`. Сверь с `systemctl cat ahrimod`.

**Хочу видеть, что бот сделал последние сутки.**
```bash
journalctl -u ahrimod --since "24 hours ago" | less
```
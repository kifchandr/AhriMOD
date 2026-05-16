# Установка и настройка Ahrimod

Бот ставится одной командой. Дальнейшие обновления — через `git pull` (или скрипт `ahrimod-update`). Поведенческие настройки меняются на лету прямо в Telegram через `/menu`.

Поддерживается **Ubuntu 22.04 / 24.04 LTS** (на других дистрибутивах — те же шаги, могут отличаться имена пакетов).

---

## 0. Настройка в Telegram (до установки)

### 0.1. Создать бота

Через `@BotFather`:

1. `/newbot` → задать имя и username → получить **BOT_TOKEN**
2. `/setprivacy` → выбрать бота → **Disable**
   Без этого бот не будет видеть обычные сообщения в группах.

### 0.2. Создать админский чат

Понадобится отдельная **группа для модераторов** (2–3 человека, можно одну тебя). Туда бот будет слать уведомления о модерации, реакциях, новых юзерах и т.п.

- Создай группу, добавь в неё бота
- Узнай её ID: переслать любое сообщение из неё в `@userinfobot`
- Это будет `ADMIN_CHAT_ID`

Если группа — **форум с темами**, тебе ещё нужен `ADMIN_CHAT_THREAD_ID`. URL темы в Telegram-вебе: `t.me/c/<chat_id>/<thread_id>` — это последнее число.

### 0.3. Добавить бота в модерируемые чаты

Бот должен быть **админом** в каждом чате который модерирует. Минимально нужные права:

- ✅ **Delete Messages** — удалять спам
- ✅ **Ban Users** — банить нарушителей
- ✅ **Restrict Users** — мутить (для эскалации по предупреждениям)

Куда добавлять зависит от того что у тебя:

| Структура | Куда добавлять бота | Что в `PROTECTED_CHAT_IDS` |
|---|---|---|
| **Супергруппа с темами** (форум) | Один раз как админа | Один ID этой группы |
| **Канал + группа обсуждений** | В группу обсуждений как админа | ID группы обсуждений |
| **Несколько групп** | В каждую как админа | Все ID через запятую |

### 0.4. Узнать свой Telegram ID

Чтобы быть админом бота — пиши `@userinfobot`, он скажет твой `user_id`. Это число пойдёт в `ADMIN_USER_IDS`.

Все ID и токен сложи в блокнот.

---

## 1. Установка одной командой

На сервере под `root` (или с `sudo`):

```bash
curl -fsSL https://raw.githubusercontent.com/kifchandr/AhriMOD/main/install.sh -o /tmp/install.sh
sudo bash /tmp/install.sh
```

Скрипт сделает за тебя:

- Поставит `python3`, `python3-venv`, `git`, `rsync`, `sqlite3`
- Создаст системного пользователя `ahrimod` (без shell)
- Клонирует репозиторий в `/opt/ahrimod`
- Создаст venv в `/opt/ahrimod/venv` и установит зависимости
- Создаст `.env` из шаблона
- Установит systemd unit `ahrimod.service`
- Установит команду `/usr/local/bin/ahrimod-update`

В конце выведет дальнейшие шаги.

## 2. Заполнить `.env`

```bash
sudo nano /opt/ahrimod/.env
```

В `.env` только **базовые** параметры (Telegram-токен, IDs):

```ini
BOT_TOKEN=8123:AAxx...
ADMIN_CHAT_ID=-1003707823690
ADMIN_CHAT_THREAD_ID=5030
LOG_CHAT_ID=-1003707823690
LOG_CHAT_THREAD_ID=5030
PROTECTED_CHAT_IDS=-1003375282506
ADMIN_USER_IDS=2076994518
DB_PATH=./data/bot.db
```

Все остальные настройки (пороги предупреждений, бэкап, FAQ-кулдаун и т.д.) **по умолчанию имеют разумные значения** и меняются через бот.

## 3. Запуск

```bash
sudo systemctl enable --now ahrimod
sudo journalctl -u ahrimod -n 30 --no-pager -o cat
```

В логе должно быть:

```
✓ ADMIN_CHAT_ID = -1003707823690 (...)
✓ PROTECTED_CHAT_IDS[-1003375282506] = -1003375282506 (...)
Бот запущен
Run polling for bot @YourBot_bot ...
```

Если красные `✗` — бот не видит этот чат: либо ID неверный, либо бот туда не добавлен. Поправь и `sudo systemctl restart ahrimod`.

---

## 4. Первичная настройка через бот

В любом чате где бот видит твои сообщения (например в админ-чате) пиши:

### Меню

```
/menu
```

Откроется inline-меню с группами настроек:

- 🛡 **Доверие** — `trust_min_hours`, `trust_min_messages`, и т.д.
- 🔍 **Фильтры** — CAS, simhash, блокировка медиа
- ⚠️ **Предупреждения** — пороги эскалации, TTL, уведомления
- 🔨 **Наказания** — режим для новичков
- 📦 **Бэкап** — расписание, хранение
- 🔧 **Прочее** — FAQ, recent_messages

Жми на bool-настройку чтобы переключить, на не-bool — чтобы ввести новое значение.

### Текстовые команды (альтернатива меню)

```
/config                    — все настройки сразу с пометками
/setcfg warn_ban_at 10     — изменить значение
/resetcfg warn_ban_at      — вернуть к .env-дефолту
```

Значения переопределённые через бот помечаются 📝 в `/config`.

### Заполнить базовые списки

Минимум — разрешить домен Telegram'а и забанить пару конкурирующих сервисов:

```
/addgooddomain t.me
/addgooddomain ahrivpn.com
/addbandomain nordvpn.com
/addbandomain expressvpn.com
/addbanword nordvpn
/addbanword surfshark
```

### Добавить FAQ-автоответы

```
/addfaq как настроить, настройка vpn :: 1. Скачай конфиг → 2. Импортируй в WireGuard → 3. Готово
/addfaq цена, стоимость, сколько стоит :: Базовый план бесплатно, премиум — 100₽/мес
```

### Назначить тему для бэкапов

Создай в админ-чате отдельную тему «Бэкапы», узнай её thread_id. Потом:

```
/menu → 📦 Бэкап → backup_thread_id → введи ID
```

Или быстрее:
```
/setcfg backup_thread_id 7890
```

Проверь работу:
```
/backup
```

Должен прилететь свежий `.db.gz`.

---

## 5. Обновление в будущем

Всё, что нужно для обновления — одна команда:

```bash
sudo ahrimod-update
```

Скрипт сам:
1. Остановит бота
2. Сделает `git pull`
3. Обновит зависимости (`pip install -r requirements.txt`)
4. Обновит systemd unit если поменялся
5. Запустит бота
6. Покажет первые 30 строк лога

При обновлении **не трогаются**:
- `.env` — твои настройки
- `data/` — БД с пользователями, доменами, FAQ, бэкапами
- `venv/` — окружение

То есть переопределения настроек из `/menu` сохраняются между обновлениями, потому что они в БД.

---

## 6. Откат если что-то сломалось

```bash
# Посмотреть последние коммиты
sudo -u ahrimod git -C /opt/ahrimod log --oneline -10

# Откатиться к предыдущему
sudo systemctl stop ahrimod
sudo -u ahrimod git -C /opt/ahrimod reset --hard HEAD~1
sudo systemctl start ahrimod

# Или к конкретному коммиту
sudo -u ahrimod git -C /opt/ahrimod reset --hard <commit-hash>
```

База данных при этом не пострадает — она вне git.

---

## 7. Резервное копирование

Бот сам делает ежедневный бэкап БД и шлёт его в админ-чат (тема `BACKUP_THREAD_ID`). Локальные копии хранятся в `/opt/ahrimod/data/backups/` за последние `BACKUP_KEEP_DAYS` дней.

Чтобы вручную скачать БД с сервера:

```bash
sudo cp /opt/ahrimod/data/bot.db /tmp/bot.db.copy
sudo chmod a+r /tmp/bot.db.copy
# потом scp /tmp/bot.db.copy на свой комп
```

Восстановить — заменить файл, перезапустить:

```bash
sudo systemctl stop ahrimod
sudo cp /tmp/bot-good.db /opt/ahrimod/data/bot.db
sudo chown ahrimod:ahrimod /opt/ahrimod/data/bot.db
sudo systemctl start ahrimod
```

---

## 8. Удаление

```bash
sudo systemctl disable --now ahrimod
sudo rm /etc/systemd/system/ahrimod.service
sudo rm /usr/local/bin/ahrimod-update
sudo rm -rf /opt/ahrimod
sudo userdel ahrimod
sudo rm -rf /var/lib/ahrimod
sudo systemctl daemon-reload
```

---

## 9. Решение проблем

### `Permission denied: '.env'`

Запусти из правильной cwd: `cd /opt/ahrimod && sudo -u ahrimod venv/bin/python main.py`. При запуске через systemd этой проблемы не будет — там `WorkingDirectory=/opt/ahrimod`.

### `✗ ADMIN_CHAT_ID = ...: chat not found`

Бота нет в этом чате. Добавь его. Проверь напрямую через API:
```bash
TOKEN=$(sudo grep ^BOT_TOKEN= /opt/ahrimod/.env | cut -d= -f2)
curl -s "https://api.telegram.org/bot${TOKEN}/getChat?chat_id=-100xxx" | python3 -m json.tool
```

### Команды бота не работают в защищаемом чате

Скорее всего privacy mode «залип». Удали бота из чата и добавь заново сразу как админа — это решает проблему.

### Бот падает при старте

```bash
sudo journalctl -u ahrimod -n 100 --no-pager -o cat
```

Найди `ERROR` или `Traceback`. Если что-то непонятное — открой issue в GitHub-репозитории.

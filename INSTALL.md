# Установка и настройка Ahrimod

Бот устанавливается в **`/root/AhriMOD`** одной командой. Дальнейшие обновления — через `sudo ahrimod-update`. Поведенческие настройки меняются на лету прямо в Telegram через `/menu`.

> **Почему `/root/AhriMOD` и запуск от root?**
> `/root/` — это домашняя директория суперпользователя, доступна только root (chmod 700). Это упрощает права — не нужен отдельный системный пользователь, не нужно настраивать SELinux/sandbox. Бот делает только исходящие запросы к Telegram API и не открывает сетевых портов, так что запуск от root не открывает дополнительных векторов атаки (если кто-то получит токен — он и без root-прав сможет писать в чат). Если нужна более жёсткая изоляция — лучший вариант ставить в `/opt/ahrimod` под отдельным пользователем, см. в комментариях `install.sh`.

Поддерживается **Ubuntu 22.04 / 24.04 LTS**.

---

## 0. Настройка в Telegram (до установки)

### 0.1. Создать бота через `@BotFather`

1. `/newbot` → задать имя и username → получить **`BOT_TOKEN`**
2. `/setprivacy` → выбрать бота → **`Disable`**
   Без этого бот не будет видеть обычные сообщения в группах.

### 0.2. Создать админский чат

- Создай отдельную **группу для модераторов**, добавь туда бота
- Узнай её ID: переслать любое сообщение в `@userinfobot`
- Это будет **`ADMIN_CHAT_ID`**

Если группа — **форум с темами**, нужен ещё `ADMIN_CHAT_THREAD_ID`. URL темы в Telegram-вебе: `t.me/c/<chat_id>/<thread_id>` — последнее число.

### 0.3. Добавить бота в модерируемые чаты

Бот должен быть **админом** в каждом чате который модерирует, с правами:

- ✅ Delete Messages
- ✅ Ban Users
- ✅ Restrict Users

| Структура чата | Что писать в `PROTECTED_CHAT_IDS` |
|---|---|
| Супергруппа с темами (форум) | Один ID группы |
| Канал + группа обсуждений | ID **группы обсуждений** |
| Несколько отдельных групп | Все ID через запятую |

### 0.4. Узнать свой `user_id`

Пиши `@userinfobot` — он скажет. Это число пойдёт в `ADMIN_USER_IDS`.

---

## 1. Установка одной командой

```bash
curl -fsSL https://raw.githubusercontent.com/kifchandr/AhriMOD/master/install.sh -o /tmp/install.sh
sudo bash /tmp/install.sh
```

Скрипт за тебя:

- Поставит `python3`, `python3-venv`, `git`, `rsync`, `sqlite3`
- Клонирует репо в **`/root/AhriMOD`**
- Создаст `venv` и установит зависимости
- Создаст `.env` из шаблона (если ещё нет)
- Установит systemd unit `ahrimod.service`
- Установит команду `/usr/local/bin/ahrimod-update`

В конце выведет цветной чек-лист дальнейших шагов.

## 2. Заполнить `.env`

```bash
sudo nano /root/AhriMOD/.env
```

В `.env` только **базовые** параметры — то, что нужно для соединения:

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

Все остальные параметры (пороги предупреждений, бэкап, FAQ-кулдаун, эскалация и т.д.) имеют разумные дефолты и **меняются прямо в Telegram** через `/menu`.

## 3. Запуск

```bash
sudo systemctl enable --now ahrimod
sudo journalctl -u ahrimod -n 30 --no-pager -o cat
```

В логе должно быть:

```
✓ ADMIN_CHAT_ID = -1003707823690 (...)
✓ PROTECTED_CHAT_IDS[-1003375282506] = ...
Бот запущен
Run polling for bot @YourBot_bot ...
```

Логи теперь цветные — благодаря `FORCE_COLOR=1` в systemd unit и rich-handler в Python. Если красные `✗` — бот не видит этот чат: либо ID неверный, либо бот туда не добавлен.

---

## 4. Настройка через бот

В любом чате где бот видит твои сообщения:

### Меню

```
/menu
```

Открывается inline-меню с шестью группами:

```
⚙️ Настройки бота
[🛡 Доверие]
[🔍 Фильтры]
[⚠️ Предупреждения]
[🔨 Наказания новичков]
[📦 Бэкап]
[🔧 Прочее]
```

Внутри группы:
- **Bool-настройка** (например `notify_on_warn`) — клик переключает Вкл/Выкл сразу
- **Числовая/текстовая** (например `warn_ban_at`) — клик запрашивает новое значение, ты пишешь его сообщением

Маркер `📝` рядом с настройкой = переопределена через бота (хранится в БД). Без маркера = из `.env` или код-дефолт.

### Текстовые команды

```
/config                    — все настройки с пометками
/setcfg warn_ban_at 10     — изменить значение
/resetcfg warn_ban_at      — сбросить к .env-дефолту
```

### Базовые списки

Минимум — разрешить `t.me` и забанить пару конкурирующих сервисов:

```
/addgooddomain t.me
/addgooddomain ahrivpn.com
/addbandomain nordvpn.com
/addbanword nordvpn
/addbanword surfshark
```

### FAQ-автоответы

```
/addfaq как настроить, настройка vpn :: 1. Скачай конфиг → 2. Импорт в WireGuard → 3. Готово
/addfaq цена, стоимость, сколько стоит :: Базовый план бесплатно, премиум — 100₽/мес
```

### Бэкап

В админ-чате создай отдельную тему «Бэкапы», узнай её `thread_id`. Потом:

```
/menu → 📦 Бэкап → backup_thread_id → введи ID
```

Или быстрее:
```
/setcfg backup_thread_id 7890
```

Проверь:
```
/backup
```

Должен прилететь свежий `.db.gz`.

---

## 5. Обновление

Одной командой:

```bash
sudo ahrimod-update
```

Скрипт делает:
1. `systemctl stop ahrimod`
2. `git pull` в `/root/AhriMOD`
3. `pip install -r requirements.txt` (если есть новые зависимости)
4. Обновляет systemd unit при необходимости
5. `systemctl start ahrimod`
6. Показывает первые 30 строк лога

**Не трогает** при обновлении:
- `.env` — твои настройки
- `data/` — БД, бэкапы, FAQ, runtime-настройки из меню
- `venv/` — окружение

То есть переопределения настроек из `/menu` **сохраняются между обновлениями**, потому что они в БД.

---

## 6. Откат

```bash
# Посмотреть коммиты
git -C /root/AhriMOD log --oneline -10

# Откатиться на предыдущий
sudo systemctl stop ahrimod
git -C /root/AhriMOD reset --hard HEAD~1
sudo systemctl start ahrimod

# Или на конкретный коммит
git -C /root/AhriMOD reset --hard <commit-hash>
```

БД при этом не пострадает — она вне git.

---

## 7. Бэкап и восстановление

Бот ежедневно делает бэкап БД и шлёт в админ-чат (тема `BACKUP_THREAD_ID`). Локальные копии — в `/root/AhriMOD/data/backups/` за последние `BACKUP_KEEP_DAYS` дней.

Скачать с сервера руками:
```bash
scp root@SERVER:/root/AhriMOD/data/bot.db ./bot.db.backup
```

Восстановить:
```bash
sudo systemctl stop ahrimod
sudo cp ./bot-good.db /root/AhriMOD/data/bot.db
sudo systemctl start ahrimod
```

---

## 8. Удаление

```bash
sudo systemctl disable --now ahrimod
sudo rm /etc/systemd/system/ahrimod.service
sudo rm /usr/local/bin/ahrimod-update
sudo rm -rf /root/AhriMOD
sudo systemctl daemon-reload
```

---

## 9. Решение проблем

### `Permission denied: '.env'`

При ручном запуске вне systemd сделай `cd /root/AhriMOD` перед `python main.py`. Через systemd этой проблемы нет.

### `✗ ADMIN_CHAT_ID = ...: chat not found`

Бота нет в этом чате — добавь. Проверка напрямую:
```bash
TOKEN=$(grep ^BOT_TOKEN= /root/AhriMOD/.env | cut -d= -f2)
curl -s "https://api.telegram.org/bot${TOKEN}/getChat?chat_id=-100xxx" | python3 -m json.tool
```

### Команды бота не работают в модерируемом чате

Скорее всего privacy mode не применился. Удали бота из чата и добавь заново сразу как админа.

### Лог белый, цветов нет

Проверь что в `/etc/systemd/system/ahrimod.service` есть строки:
```ini
Environment=FORCE_COLOR=1
Environment=TERM=xterm-256color
```
Если нет — `sudo cp /root/AhriMOD/ahrimod.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl restart ahrimod`.

### Бот падает при старте

```bash
sudo journalctl -u ahrimod -n 100 --no-pager -o cat
```

Найди `ERROR` или `Traceback`. Если непонятно — создай issue в репозитории.

---

## Команды на каждый день

| Что | Команда |
|---|---|
| Обновить из GitHub | `sudo ahrimod-update` |
| Статус | `sudo systemctl status ahrimod` |
| Логи в реальном времени | `sudo journalctl -u ahrimod -f` |
| Перезапустить | `sudo systemctl restart ahrimod` |
| Поменять `.env` | `sudo nano /root/AhriMOD/.env && sudo systemctl restart ahrimod` |
| Поменять настройку в боте | `/menu` или `/setcfg key value` |

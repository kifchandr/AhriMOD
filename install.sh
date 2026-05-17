#!/bin/bash
# -----------------------------------------------------------------------------
# Ahrimod — автоустановка на Ubuntu 22.04 / 24.04
# Устанавливается в /root/AhriMOD, работает от root.
# Использование:
#   curl -fsSL https://raw.githubusercontent.com/kifchandr/AhriMOD/master/install.sh -o install.sh
#   sudo bash install.sh
# -----------------------------------------------------------------------------
set -euo pipefail

REPO_URL="${AHRIMOD_REPO_URL:-https://github.com/kifchandr/AhriMOD.git}"
INSTALL_DIR="${AHRIMOD_INSTALL_DIR:-/root/AhriMOD}"

# ANSI цвета
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

log()   { echo -e "${CYAN}▸${NC} $*"; }
ok()    { echo -e "${GREEN}✓${NC} $*"; }
warn()  { echo -e "${YELLOW}!${NC} $*"; }
err()   { echo -e "${RED}✗${NC} $*"; exit 1; }
hdr()   { echo -e "\n${BOLD}${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n${BOLD}${BLUE}$*${NC}\n${BOLD}${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"; }

# Проверка root
[ "$EUID" -eq 0 ] || err "Запусти через sudo: sudo bash install.sh"

hdr "🛠  Установка Ahrimod в $INSTALL_DIR"

# 1. Системные зависимости
log "Установка системных зависимостей..."
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git rsync sqlite3 >/dev/null
ok "Зависимости установлены"

# 2. Клонирование / обновление
if [ -d "$INSTALL_DIR/.git" ]; then
    log "Репозиторий уже в $INSTALL_DIR — обновляю (git pull)..."
    systemctl stop ahrimod 2>/dev/null || true
    git -C "$INSTALL_DIR" pull --ff-only
    ok "Обновлено"
else
    BACKUP_DIR=""
    if [ -d "$INSTALL_DIR" ]; then
        warn "$INSTALL_DIR существует, но без .git — пересоздаю"
        BACKUP_DIR=$(mktemp -d)
        [ -f "$INSTALL_DIR/.env" ] && cp "$INSTALL_DIR/.env" "$BACKUP_DIR/.env" && warn "Сохранён .env"
        [ -d "$INSTALL_DIR/data" ] && cp -r "$INSTALL_DIR/data" "$BACKUP_DIR/data" && warn "Сохранён data/"
        systemctl stop ahrimod 2>/dev/null || true
        rm -rf "$INSTALL_DIR"
    fi

    log "Клонирование ${MAGENTA}$REPO_URL${NC} → ${MAGENTA}$INSTALL_DIR${NC}..."
    git clone "$REPO_URL" "$INSTALL_DIR"
    ok "Клонировано"

    if [ -n "$BACKUP_DIR" ]; then
        [ -f "$BACKUP_DIR/.env" ] && cp "$BACKUP_DIR/.env" "$INSTALL_DIR/.env" && ok "Восстановлен .env"
        [ -d "$BACKUP_DIR/data" ] && cp -r "$BACKUP_DIR/data" "$INSTALL_DIR/data" && ok "Восстановлен data/"
        rm -rf "$BACKUP_DIR"
    fi
fi

# 3. venv + зависимости
log "Создание venv и установка Python-зависимостей..."
if [ ! -d "$INSTALL_DIR/venv" ]; then
    python3 -m venv "$INSTALL_DIR/venv"
fi
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip --quiet
"$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt" --quiet
ok "venv готов"

# 4. .env
NEEDS_ENV_FILL=0
if [ ! -f "$INSTALL_DIR/.env" ]; then
    log "Создание .env из шаблона..."
    cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"
    chmod 600 "$INSTALL_DIR/.env"
    NEEDS_ENV_FILL=1
    ok ".env создан"
else
    ok ".env уже существует — не перезатираю"
fi

# 5. Папка для данных и бэкапов
mkdir -p "$INSTALL_DIR/data/backups"

# 6. systemd unit
log "Установка systemd сервиса..."
cp "$INSTALL_DIR/ahrimod.service" /etc/systemd/system/ahrimod.service
systemctl daemon-reload
ok "systemd unit установлен"

# 7. Скрипт ahrimod-update
log "Установка команды ${BOLD}ahrimod-update${NC}..."
cat > /usr/local/bin/ahrimod-update << UPDATE_EOF
#!/bin/bash
# Обновление Ahrimod из GitHub
set -e
INSTALL_DIR=$INSTALL_DIR

CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'; BOLD='\033[1m'

echo -e "\${CYAN}▸\${NC} \${BOLD}Останавливаю бот\${NC}"
systemctl stop ahrimod

echo -e "\${CYAN}▸\${NC} \${BOLD}git pull\${NC}"
git -C "\$INSTALL_DIR" pull --ff-only

echo -e "\${CYAN}▸\${NC} \${BOLD}Обновление зависимостей\${NC}"
"\$INSTALL_DIR/venv/bin/pip" install -q -r "\$INSTALL_DIR/requirements.txt"

echo -e "\${CYAN}▸\${NC} \${BOLD}Обновление systemd unit\${NC}"
cp "\$INSTALL_DIR/ahrimod.service" /etc/systemd/system/ahrimod.service
systemctl daemon-reload

echo -e "\${CYAN}▸\${NC} \${BOLD}Запуск\${NC}"
systemctl start ahrimod
sleep 3

echo -e "\${GREEN}✓ Готово\${NC}\n"
journalctl -u ahrimod -n 30 --no-pager -o cat
UPDATE_EOF
chmod +x /usr/local/bin/ahrimod-update
ok "ahrimod-update установлен"

# 8. Финал
hdr "✅ Установка завершена"
echo
if [ "$NEEDS_ENV_FILL" -eq 1 ]; then
    echo -e "${YELLOW}${BOLD}Дальнейшие шаги:${NC}"
    echo -e "  ${BOLD}1.${NC} Заполни конфиг:"
    echo -e "     ${CYAN}sudo nano $INSTALL_DIR/.env${NC}"
    echo -e "     ${DIM}нужно: BOT_TOKEN, ADMIN_CHAT_ID, LOG_CHAT_ID,${NC}"
    echo -e "     ${DIM}        PROTECTED_CHAT_IDS, ADMIN_USER_IDS${NC}"
    echo
    echo -e "  ${BOLD}2.${NC} Запусти бота с автозапуском:"
    echo -e "     ${CYAN}sudo systemctl enable --now ahrimod${NC}"
    echo
    echo -e "  ${BOLD}3.${NC} Проверь логи:"
    echo -e "     ${CYAN}sudo journalctl -u ahrimod -f${NC}"
else
    echo -e "${GREEN}Бот обновлён.${NC} Запусти:"
    echo -e "  ${CYAN}sudo systemctl start ahrimod${NC}"
fi
echo
echo -e "${DIM}Обновление в будущем:${NC} ${BOLD}sudo ahrimod-update${NC}"
echo -e "${DIM}Настройки бота через Telegram:${NC} ${BOLD}/menu${NC} или ${BOLD}/config${NC}"
echo

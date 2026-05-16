#!/bin/bash
# -----------------------------------------------------------------------------
# Ahrimod — автоустановка на Ubuntu 22.04 / 24.04
# Использование:
#   curl -fsSL https://raw.githubusercontent.com/kifchandr/AhriMOD/main/install.sh -o install.sh
#   sudo bash install.sh
# -----------------------------------------------------------------------------
set -euo pipefail

REPO_URL="${AHRIMOD_REPO_URL:-https://github.com/kifchandr/AhriMOD.git}"
INSTALL_DIR="${AHRIMOD_INSTALL_DIR:-/opt/ahrimod}"
SERVICE_USER="${AHRIMOD_USER:-ahrimod}"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BOLD='\033[1m'
NC='\033[0m'

log()   { echo -e "${GREEN}▸${NC} $*"; }
warn()  { echo -e "${YELLOW}!${NC} $*"; }
err()   { echo -e "${RED}✗${NC} $*"; exit 1; }

# Проверка root
[ "$EUID" -eq 0 ] || err "Запусти через sudo: sudo bash install.sh"

# 1. Системные зависимости
log "Установка системных зависимостей (python3, venv, git)..."
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git rsync sqlite3 >/dev/null

# 2. Пользователь для бота
if id "$SERVICE_USER" &>/dev/null; then
    log "Пользователь $SERVICE_USER уже существует"
else
    log "Создание системного пользователя $SERVICE_USER..."
    useradd --system \
            --home-dir "/var/lib/$SERVICE_USER" \
            --create-home \
            --shell /usr/sbin/nologin \
            "$SERVICE_USER"
fi

# 3. Клонирование или обновление репозитория
if [ -d "$INSTALL_DIR/.git" ]; then
    log "Репозиторий уже в $INSTALL_DIR — обновляю (git pull)..."
    systemctl stop ahrimod 2>/dev/null || true
    sudo -u "$SERVICE_USER" git -C "$INSTALL_DIR" pull --ff-only
else
    if [ -d "$INSTALL_DIR" ]; then
        warn "$INSTALL_DIR существует, но без .git — пересоздаю"
        warn "Сохраняю .env и data/ если есть..."
        BACKUP_DIR=$(mktemp -d)
        [ -f "$INSTALL_DIR/.env" ] && cp "$INSTALL_DIR/.env" "$BACKUP_DIR/.env"
        [ -d "$INSTALL_DIR/data" ] && cp -r "$INSTALL_DIR/data" "$BACKUP_DIR/data"
        systemctl stop ahrimod 2>/dev/null || true
        rm -rf "$INSTALL_DIR"
    fi

    log "Клонирование $REPO_URL в $INSTALL_DIR..."
    git clone "$REPO_URL" "$INSTALL_DIR"
    chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"

    # Восстановление
    if [ -n "${BACKUP_DIR:-}" ]; then
        [ -f "$BACKUP_DIR/.env" ] && cp "$BACKUP_DIR/.env" "$INSTALL_DIR/.env"
        [ -d "$BACKUP_DIR/data" ] && cp -r "$BACKUP_DIR/data" "$INSTALL_DIR/data"
        chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR/.env" "$INSTALL_DIR/data" 2>/dev/null || true
        rm -rf "$BACKUP_DIR"
    fi
fi

# 4. Виртуальное окружение
log "Создание venv и установка зависимостей..."
if [ ! -d "$INSTALL_DIR/venv" ]; then
    sudo -u "$SERVICE_USER" python3 -m venv "$INSTALL_DIR/venv"
fi
sudo -u "$SERVICE_USER" "$INSTALL_DIR/venv/bin/pip" install --upgrade pip --quiet
sudo -u "$SERVICE_USER" "$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt" --quiet

# 5. .env
if [ ! -f "$INSTALL_DIR/.env" ]; then
    log "Создание .env из шаблона..."
    cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"
    chown "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR/.env"
    chmod 600 "$INSTALL_DIR/.env"
    NEEDS_ENV_FILL=1
else
    log ".env уже существует — не перезатираю"
    NEEDS_ENV_FILL=0
fi

# 6. Папка для данных и бэкапов
mkdir -p "$INSTALL_DIR/data/backups"
chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR/data"

# 7. systemd unit
log "Установка systemd сервиса..."
cp "$INSTALL_DIR/ahrimod.service" /etc/systemd/system/ahrimod.service
systemctl daemon-reload

# 8. Скрипт ahrimod-update
log "Установка команды ahrimod-update..."
cat > /usr/local/bin/ahrimod-update << 'UPDATE_EOF'
#!/bin/bash
# Обновление Ahrimod из GitHub
set -e
INSTALL_DIR=/opt/ahrimod
SERVICE_USER=ahrimod

GREEN='\033[0;32m'; NC='\033[0m'
echo -e "${GREEN}🛑 Остановка${NC}"
systemctl stop ahrimod
echo -e "${GREEN}📥 git pull${NC}"
sudo -u "$SERVICE_USER" git -C "$INSTALL_DIR" pull --ff-only
echo -e "${GREEN}📦 pip install${NC}"
sudo -u "$SERVICE_USER" "$INSTALL_DIR/venv/bin/pip" install -q -r "$INSTALL_DIR/requirements.txt"
echo -e "${GREEN}⚙️ systemd unit${NC}"
cp "$INSTALL_DIR/ahrimod.service" /etc/systemd/system/ahrimod.service
systemctl daemon-reload
echo -e "${GREEN}🚀 Старт${NC}"
systemctl start ahrimod
sleep 3
journalctl -u ahrimod -n 30 --no-pager -o cat
UPDATE_EOF
chmod +x /usr/local/bin/ahrimod-update

# 9. Финал
echo
echo -e "${BOLD}${GREEN}✅ Установка завершена${NC}"
echo
if [ "$NEEDS_ENV_FILL" -eq 1 ]; then
    echo -e "${YELLOW}Дальнейшие шаги:${NC}"
    echo "  1. Открой и заполни конфиг:"
    echo "     ${BOLD}sudo nano $INSTALL_DIR/.env${NC}"
    echo "     (нужно: BOT_TOKEN, ADMIN_CHAT_ID, LOG_CHAT_ID, PROTECTED_CHAT_IDS, ADMIN_USER_IDS)"
    echo
    echo "  2. Запусти бота:"
    echo "     ${BOLD}sudo systemctl enable --now ahrimod${NC}"
    echo
    echo "  3. Проверь логи:"
    echo "     ${BOLD}sudo journalctl -u ahrimod -f${NC}"
else
    echo "Бот обновлён. Запусти:"
    echo "  ${BOLD}sudo systemctl start ahrimod${NC}"
fi
echo
echo -e "Обновление в будущем: ${BOLD}sudo ahrimod-update${NC}"
echo -e "Настройки бота через Telegram: ${BOLD}/menu${NC} или ${BOLD}/config${NC}"

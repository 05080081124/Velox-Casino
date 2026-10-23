#!/bin/bash
# velox_support/start.sh — Запуск панели поддержки

set -e
cd "$(dirname "$0")"

# Устанавливаем зависимости если нет
if ! python3 -c "import flask" 2>/dev/null; then
  echo "[install] Устанавливаем зависимости..."
  pip install -r requirements.txt
fi

# Генерируем .env если его нет
if [ ! -f .env ]; then
  echo "[init] Создаём .env из .env.example..."
  cp .env.example .env
  RKEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
  sed -i "s/замени_на_случайный_ключ/$RKEY/" .env
  echo "[init] ADMIN_REGISTER_KEY сгенерирован: $RKEY"
  echo "       Сохраните его! Он нужен для регистрации первого администратора."
fi

PORT=${PORT:-5001}
DEBUG=${DEBUG:-false}

if [ "$DEBUG" = "true" ]; then
  echo "[run] Режим разработки на порту $PORT"
  python3 main.py
else
  echo "[run] Продакшн на порту $PORT (gunicorn)"
  gunicorn "main:create_app()" \
    -w 2 \
    -k gevent \
    --bind "0.0.0.0:$PORT" \
    --timeout 60 \
    --access-logfile - \
    --error-logfile -
fi

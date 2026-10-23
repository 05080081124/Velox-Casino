#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
if [ ! -f .env ]; then cp .env.example .env; echo "[!] Создан .env — заполни переменные окружения!"; fi
echo "  ⚡ Запускаем VELOX Casino v3..."
if [ "${1}" = "prod" ]; then
    exec gunicorn "main:create_app()" --workers 4 --worker-class gevent --bind 0.0.0.0:${PORT:-5000} --timeout 120
else
    export DEBUG=true
    exec python main.py
fi

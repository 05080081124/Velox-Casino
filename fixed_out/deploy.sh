#!/bin/bash
# ══════════════════════════════════════════════════════════════════════════════
# VELOX Casino — Production Deploy Script
# Usage: sudo bash deploy.sh yourdomain.com
# Tested on: Ubuntu 22.04 LTS
# ══════════════════════════════════════════════════════════════════════════════

set -e

DOMAIN=${1:-"yourdomain.com"}
APP_DIR=$(pwd)
APP_USER=$(whoami)
PORT=5000

echo ""
echo "  ⚡ VELOX Casino — Production Deploy"
echo "  Domain: $DOMAIN"
echo "  App dir: $APP_DIR"
echo ""

# ── 1. System packages ────────────────────────────────────────────────────────
echo "[1/6] Installing system packages..."
apt-get update -qq
apt-get install -y -qq nginx certbot python3-certbot-nginx python3-pip python3-venv

# ── 2. Python env + deps ──────────────────────────────────────────────────────
echo "[2/6] Setting up Python environment..."
python3 -m venv venv 2>/dev/null || true
source venv/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements.txt
deactivate

# ── 3. nginx config ───────────────────────────────────────────────────────────
echo "[3/6] Configuring nginx..."

# Replace placeholder domain and path in nginx.conf
sed -e "s/yourdomain\.com/$DOMAIN/g" \
    -e "s|/path/to/fixed_out|$APP_DIR|g" \
    nginx.conf > /etc/nginx/sites-available/velox

# Create proxy_params if missing
if [ ! -f /etc/nginx/proxy_params ]; then
cat > /etc/nginx/proxy_params << 'EOF'
proxy_set_header Host $host;
proxy_set_header X-Real-IP $remote_addr;
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $scheme;
proxy_connect_timeout 10s;
proxy_read_timeout 60s;
proxy_send_timeout 30s;
EOF
fi

# Enable site
ln -sf /etc/nginx/sites-available/velox /etc/nginx/sites-enabled/velox
rm -f /etc/nginx/sites-enabled/default

# Test config
nginx -t && systemctl reload nginx
echo "  nginx: OK"

# ── 4. SSL certificate ────────────────────────────────────────────────────────
echo "[4/6] Obtaining SSL certificate..."
certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos \
        --email "admin@$DOMAIN" --redirect || echo "  SSL: Check certbot output above"

# Set https=true in security config
python3 -c "
import json
path = 'config/security.json'
d = json.load(open(path))
d['https'] = True
json.dump(d, open(path,'w'), ensure_ascii=False, indent=2)
print('  security.json https=true: OK')
"

# ── 5. systemd service ────────────────────────────────────────────────────────
echo "[5/6] Creating systemd service..."
cat > /etc/systemd/system/velox.service << EOF
[Unit]
Description=VELOX Casino
After=network.target

[Service]
User=$APP_USER
WorkingDirectory=$APP_DIR
Environment="PATH=$APP_DIR/venv/bin"
Environment="PYTHONPATH=$APP_DIR"
ExecStart=$APP_DIR/venv/bin/gunicorn "main:create_app()" \\
    -w 4 -k gevent --bind 127.0.0.1:$PORT \\
    --timeout 120 --keep-alive 5 \\
    --access-logfile logs/access.log \\
    --error-logfile logs/error.log \\
    --log-level warning
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

mkdir -p logs
systemctl daemon-reload
systemctl enable velox
systemctl restart velox
echo "  velox service: OK"

# ── 6. Certbot auto-renew ─────────────────────────────────────────────────────
echo "[6/6] Setting up auto-renewal..."
systemctl enable certbot.timer 2>/dev/null || \
    (crontab -l 2>/dev/null; echo "0 3 * * * certbot renew --quiet") | crontab -

echo ""
echo "  ✅ Deploy complete!"
echo "  Site: https://$DOMAIN"
echo "  Status: systemctl status velox"
echo "  Logs:   tail -f $APP_DIR/logs/error.log"
echo ""

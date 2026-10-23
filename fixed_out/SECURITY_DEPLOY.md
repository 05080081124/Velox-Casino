# VELOX Security Deployment Notes

This project is prepared for secure demo/production deployment.

## 1) CSRF protection

- CSRF is enabled in `config/security.json` (`checks.csrf = true`).
- Frontend sends `X-CSRF-Token` automatically for all state-changing requests.
- Token source: `velox_csrf` cookie (double-submit cookie pattern).

## 2) Secrets before sale

Replace all placeholders in `.env` with your own values:

- `SECRET_KEY`
- `SMTP_PASSWORD`
- `ADMIN_REGISTER_KEY` (in support panel `.env`)

Do not ship real keys in archives.

## 3) 2FA = TOTP (Google Authenticator/Authy)

- 2FA now uses TOTP shared secret (stored in `users.two_fa_pass`).
- Setup returns:
  - `otpauth_url`
  - `qr_url` (for QR scan)
  - `secret` (manual fallback key)
- Install dependency on backend:

```bash
pip install pyotp
```

## 4) Rate limiter in production

`memory://` is for local development only.

Set Redis in `.env`:

```env
REDIS_URL=redis://localhost:6379/0
```

Without Redis, counters reset after every restart.

## 5) HTTPS + Secure cookies (mandatory)

For demo/prod:

- `HTTPS=true`
- `SESSION_COOKIE_SECURE=true` (handled via `HTTPS=true` in this project)
- valid TLS certificate (Let's Encrypt + certbot recommended)

Without HTTPS browser will mark the site as insecure.

## 6) CDN recommendation

CDN is optional but recommended for buyers:

- Cloudflare in front of your domain
- Enable WAF, bot protection, caching, and DDoS filtering


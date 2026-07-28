# Deploying Varthaai on the shared EC2

Target: `ubuntu@` EC2 already running other Django apps behind gunicorn + nginx.
This app runs as its own **systemd service** on **127.0.0.1:8007**, with **nginx**
proxying `varthaai.com` to it and serving `/static/` + `/media/` directly.

Path: `/home/ubuntu/varthaai-new` · Venv: `/home/ubuntu/varthaai-new/venv`

---

## 0. Prerequisites (once per box)
- PostgreSQL 16 running locally, plus a `varthaai_db` DB and `varthaai` user.
- nginx installed with the usual `sites-available` / `sites-enabled` layout.
- DNS: `varthaai.com` and `www.varthaai.com` A-records → this EC2's public IP.
- Security Group: inbound 80 (and 443 later) open; 8007 stays private (localhost only).

## 1. Get the code
```bash
cd /home/ubuntu
git clone <repo-url> varthaai-new
cd varthaai-new
python3.12 -m venv venv
./venv/bin/pip install -r requirements.txt   # includes gunicorn
```

## 2. Environment
Your `.env` already exists at `/home/ubuntu/varthaai-new/.env`. Confirm it has the
three production-critical values (settings.py reads them via python-dotenv):
```
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=varthaai.com,www.varthaai.com
DJANGO_CSRF_TRUSTED_ORIGINS=http://varthaai.com,http://www.varthaai.com
```
With `DEBUG=False`, Django stops serving static/media — nginx takes over (step 4).
`DJANGO_SECRET_KEY` must be set (a blank one falls back to the insecure dev key).

## 3. Database + static
```bash
cd /home/ubuntu/varthaai-new
# If restoring the dump instead of migrating from scratch:
#   psql -U varthaai -d varthaai_db -f varthaai_db.sql
./venv/bin/python manage.py migrate
./venv/bin/python manage.py collectstatic --noinput   # -> staticfiles/
./venv/bin/python manage.py check --deploy            # sanity-check prod settings
```
Ensure nginx (www-data) can read the files it serves:
```bash
chmod o+x /home/ubuntu                                 # traverse into home dir
chmod -R o+rX /home/ubuntu/varthaai-new/staticfiles /home/ubuntu/varthaai-new/media
```

## 4. gunicorn service (systemd)
```bash
sudo cp deploy/varthaai.service /etc/systemd/system/varthaai.service
sudo systemctl daemon-reload
sudo systemctl enable --now varthaai
systemctl status varthaai            # active (running)?
curl -I http://127.0.0.1:8007/       # gunicorn answering locally?
```
> Port 8007 taken? `sudo ss -ltnp | grep 800` to find a free one, then change it in
> **both** `deploy/gunicorn.conf.py` (`bind`) and `deploy/varthaai.nginx.conf` (`upstream`).

## 5. nginx vhost
```bash
sudo cp deploy/varthaai.nginx.conf /etc/nginx/sites-available/varthaai
sudo ln -s /etc/nginx/sites-available/varthaai /etc/nginx/sites-enabled/varthaai
sudo nginx -t && sudo systemctl reload nginx
```
Visit `http://varthaai.com/` — storefront should load, `/admin/` should reach login.

## 6. TLS later (when ready)
```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d varthaai.com -d www.varthaai.com
```
Then flip these in `.env` and restart (`sudo systemctl restart varthaai`):
```
DJANGO_CSRF_TRUSTED_ORIGINS=https://varthaai.com,https://www.varthaai.com
```
and consider adding to settings.py: `SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO','https')`,
`SESSION_COOKIE_SECURE=True`, `CSRF_COOKIE_SECURE=True`.

---

## Redeploy (pull new code)
```bash
cd /home/ubuntu/varthaai-new
git pull
./venv/bin/pip install -r requirements.txt
./venv/bin/python manage.py migrate
./venv/bin/python manage.py collectstatic --noinput
sudo systemctl restart varthaai
```

## Troubleshooting
- `journalctl -u varthaai -f` — app/gunicorn logs (tracebacks show here).
- `/var/log/nginx/varthaai.error.log` — proxy/static errors.
- **502 Bad Gateway** → gunicorn down or wrong port; check `systemctl status varthaai`.
- **403 CSRF** on login/POST → fix `DJANGO_CSRF_TRUSTED_ORIGINS` (must match scheme+host).
- **Unstyled pages / 404 static** → `collectstatic` not run, or nginx can't read `staticfiles/` (step 3 perms).
- **DisallowedHost** → add the host to `DJANGO_ALLOWED_HOSTS`.

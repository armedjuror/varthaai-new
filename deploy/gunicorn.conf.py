# Gunicorn config for Varthaai — loaded via `gunicorn -c deploy/gunicorn.conf.py`.
# Binds to a localhost TCP port; nginx reverse-proxies to it (matches the
# other Django apps on this box).
import multiprocessing

# Port 8007 is arbitrary — change it if it clashes with another app.
# Check what's already listening:  sudo ss -ltnp | grep 800
bind = "127.0.0.1:8007"

# Rule of thumb: (2 x cores) + 1. Cap it so several apps can coexist on one box.
workers = min(multiprocessing.cpu_count() * 2 + 1, 5)
worker_class = "sync"
timeout = 60
graceful_timeout = 30
keepalive = 5

# Recycle workers periodically to bound memory growth.
max_requests = 1000
max_requests_jitter = 100

# Log to stdout/stderr so `journalctl -u varthaai` captures everything.
accesslog = "-"
errorlog = "-"
loglevel = "info"

proc_name = "varthaai"

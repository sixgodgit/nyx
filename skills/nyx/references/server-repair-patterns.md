# Server Repair Patterns — Reference

## Python HTTP Server Hanging Fix

**Symptom:** Python `http.server.HTTPServer` becomes unresponsive under load or concurrent requests.

**Root cause:** `HTTPServer` is single-threaded. When a request handler blocks (slow client, network issue), all subsequent requests queue up and the server appears "hung".

**Fix:** Replace with `ThreadingHTTPServer`:
```python
# Before (hangs):
import http.server
server = http.server.HTTPServer(('0.0.0.0', PORT), Handler)

# After (multi-threaded):
server = http.server.ThreadingHTTPServer(('0.0.0.0', PORT), Handler)
```

**When to apply:** Any long-running Python HTTP service that serves external traffic.

## Nginx Reverse Proxy for Python Apps

**Config template:**
```nginx
server {
    listen 80;
    server_name domain.com;
    return 301 https://$host$request_uri;
}
server {
    listen 443 ssl http2;
    server_name domain.com;
    ssl_certificate /etc/letsencrypt/live/domain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/domain.com/privkey.pem;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header Connection "";
        proxy_connect_timeout 10s;
        proxy_send_timeout 30s;
        proxy_read_timeout 30s;
    }
}
```

**Critical:** File must have `.conf` extension to be included by nginx (`include /etc/nginx/sites-enabled/*.conf;`)

## SSL Certificate Renewal

```bash
# Check existing certs
certbot certificates

# Renew (if near expiry)
certbot renew

# New cert via webroot
certbot certonly --webroot -w /var/www/acme-challenge -d domain.com --non-interactive --agree-tos --email you@domain.com
```

**Note:** `certbot --nginx` requires `python3-certbot-nginx` plugin. If not installed, use `--webroot` mode.

## Systemd Auto-Restart

```ini
[Service]
Restart=always
RestartSec=5
```

**Verification:**
```bash
systemctl is-active service-name
journalctl -u service-name --no-pager -n 20
```

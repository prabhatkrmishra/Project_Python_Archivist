# Bare-Metal Deployment Guide

Deploy Archivist on Ubuntu 22.04+ VPS. Uses SQLite FTS5 (zero external services).

## Prerequisites

- Ubuntu 22.04 or newer (or compatible Linux)
- 4 GB RAM minimum
- 4 CPU cores minimum
- 50 GB SSD
- Ports open: `8000` (API), `80/443` (nginx)
- Root or sudo access

## 1. Install Archivist

```bash
sudo useradd -r -s /bin/bash -d /opt/archivist archivist
sudo mkdir -p /opt/archivist /var/lib/archivist /var/log/archivist
sudo chown -R archivist:archivist /opt/archivist /var/lib/archivist /var/log/archivist

# Deploy code
sudo -u archivist git clone <repo-url> /opt/archivist/app
cd /opt/archivist/app
sudo -u archivist python -m venv .venv
sudo -u archivist .venv/bin/pip install -r requirements.txt
sudo -u archivist .venv/bin/pip install -e .
```

### SQLite Backend

No external services needed. Data stored in `DATA_DIR`:

```bash
# Ingest documents
sudo -u archivist /opt/archivist/app/.venv/bin/archivist ingest /path/to/documents

# Search
sudo -u archivist /opt/archivist/app/.venv/bin/archivist search "quarterly budget"
```

## 2. API Service

### Systemd unit: `/etc/systemd/system/archivist-api.service`

```ini
[Unit]
Description=Archivist Document Search API
After=network.target

[Service]
Type=simple
User=archivist
Group=archivist
WorkingDirectory=/opt/archivist/app
Environment=ARCHIVIST_DATA_DIR=/var/lib/archivist
ExecStart=/opt/archivist/app/.venv/bin/uvicorn archivist.main:create_app \
    --factory \
    --host 0.0.0.0 \
    --port 8000 \
    --workers 4 \
    --access-log
Restart=on-failure
RestartSec=5
StandardOutput=append:/var/log/archivist/api.log
StandardError=append:/var/log/archivist/api.error.log

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable archivist-api
sudo systemctl start archivist-api
sudo systemctl status archivist-api
```

## 3. Nginx Reverse Proxy

### Config: `/etc/nginx/sites-available/archivist`

```nginx
server {
    listen 80;
    server_name search.yourdomain.com;

    client_max_body_size 50M;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 60s;
        proxy_send_timeout 60s;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/archivist /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

### TLS with Let's Encrypt

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d search.yourdomain.com
```

Auto-renewal is configured automatically by certbot.

## 4. Verify Deployment

```bash
# Health check
curl http://localhost:8000/health
# → {"status":"ok"}

# Index status
archivist status

# Test search via API
curl "http://search.yourdomain.com/api/v1/search?q=test&size=3"
```

## 5. Backup

### SQLite FTS5

```bash
cp /var/lib/archivist/search.db /mnt/backups/search_$(date +%F).db
cp /var/lib/archivist/ingested_files.db /mnt/backups/tracker_$(date +%F).db
```

### Cron: daily backup at 2 AM

```cron
0 2 * * * root cp /var/lib/archivist/search.db /mnt/backups/search_$(date +\%F).db && cp /var/lib/archivist/ingested_files.db /mnt/backups/tracker_$(date +\%F).db
```

## 6. Upgrade

```bash
cd /opt/archivist/app
sudo -u archivist git pull
sudo -u archivist .venv/bin/pip install -r requirements.txt
sudo -u archivist .venv/bin/pip install -e .
sudo systemctl restart archivist-api
```

## 7. Firewall (UFW)

```bash
sudo ufw allow 22/tcp      # SSH
sudo ufw allow 80/tcp      # HTTP (nginx)
sudo ufw allow 443/tcp     # HTTPS (nginx)
sudo ufw enable
```

## Troubleshooting

### SQLite FTS5 issues

```bash
# Check database file
ls -la /var/lib/archivist/search.db

# Check permissions
sudo -u archivist /opt/archivist/app/.venv/bin/archivist status
```

### Permission denied on ingest

```bash
# Ensure archivist user owns the data directory
sudo chown -R archivist:archivist /var/lib/archivist

# Check file permissions on source documents
ls -la /path/to/documents
```

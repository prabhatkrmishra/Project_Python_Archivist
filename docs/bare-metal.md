# Bare-Metal Deployment Guide

Deploy Archivist on Ubuntu 22.04+ VPS. Supports SQLite FTS5 (default, zero external services) or Qdrant vector search.

## Prerequisites

- Ubuntu 22.04 or newer (or compatible Linux)
- 4 GB RAM minimum (8 GB recommended for 1M docs with Qdrant)
- 4 CPU cores minimum
- 50 GB SSD (200 GB recommended)
- Ports open: `8000` (API), `80/443` (nginx), `6333` (Qdrant — optional)
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

# Select backend (persisted to ~/.config/archivist/backend)
sudo -u archivist /opt/archivist/app/.venv/bin/archivist use sqlite
```

### SQLite Backend (Default)

No external services needed. Data stored in `DATA_DIR`:

```bash
# Ingest documents
sudo -u archivist /opt/archivist/app/.venv/bin/archivist ingest /path/to/documents

# Search
sudo -u archivist /opt/archivist/app/.venv/bin/archivist search "quarterly budget"
```

## 2. Qdrant Backend (Optional)

For 1M+ files or vector similarity search, install Qdrant:

### Download and install

```bash
QDRANT_VERSION=v1.18.0
wget https://github.com/qdrant/qdrant/releases/download/${QDRANT_VERSION}/qdrant-x86_64-unknown-linux-gnu.tar.gz
tar -xzf qdrant-x86_64-unknown-linux-gnu.tar.gz
sudo mv qdrant /opt/qdrant/
sudo mkdir -p /var/lib/qdrant/storage /var/log/qdrant /opt/qdrant/config
```

### Create Qdrant user

```bash
sudo useradd -r -s /bin/bash -d /var/lib/qdrant qdrant
sudo chown -R qdrant:qdrant /var/lib/qdrant /var/log/qdrant /opt/qdrant
```

### Config: `/opt/qdrant/config/config.yaml`

```yaml
storage:
  storage_path: /var/lib/qdrant/storage
service:
  host: 0.0.0.0
  http_port: 6333
  grpc_port: 6334
  api_key: ${QDRANT_API_KEY}
```

**Never hardcode the API key** — set it via environment variable in the systemd unit.

### System limits

```bash
# /etc/security/limits.d/99-qdrant.conf
qdrant soft memlock unlimited
qdrant hard memlock unlimited
qdrant soft nofile 65536
qdrant hard nofile 65536

# /etc/sysctl.d/99-qdrant.conf
vm.max_map_count = 262144

sudo sysctl -p /etc/sysctl.d/99-qdrant.conf
```

### Systemd unit: `/etc/systemd/system/qdrant.service`

```ini
[Unit]
Description=Qdrant Vector Search Engine
After=network.target

[Service]
Type=simple
User=qdrant
Group=qdrant
WorkingDirectory=/opt/qdrant
Environment=QDRANT_API_KEY=your-secure-api-key-here
ExecStart=/opt/qdrant/qdrant --config-path /opt/qdrant/config/config.yaml
Restart=on-failure
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable qdrant
sudo systemctl start qdrant
sudo systemctl status qdrant   # verify: active (running)
```

### Switch Archivist to Qdrant

```bash
sudo -u archivist /opt/archivist/app/.venv/bin/archivist use qdrant
```

## 3. API Service

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
Environment=DATA_DIR=/var/lib/archivist
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

**Note:** If using Qdrant backend, add these environment variables:

```ini
Environment=QDRANT_URL=http://localhost:6333
Environment=QDRANT_API_KEY=your-secure-api-key-here
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable archivist-api
sudo systemctl start archivist-api
sudo systemctl status archivist-api
```

## 4. Nginx Reverse Proxy

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

## 5. Verify Deployment

```bash
# Health check
curl http://localhost:8000/health
# → {"status":"ok"}

# Index status (shows backend type and document count)
archivist status

# Test search via API
curl "http://search.yourdomain.com/api/v1/search?q=test&size=3"

# Qdrant health (if using Qdrant backend)
curl http://localhost:6333/healthz
# → {"title":"qdrant - vector search engine"}
```

## 6. Backup

### SQLite FTS5 (default backend)

```bash
cp /var/lib/archivist/search.db /mnt/backups/search_$(date +%F).db
cp /var/lib/archivist/ingested_files.db /mnt/backups/tracker_$(date +%F).db
```

### Qdrant snapshots (if using Qdrant)

```bash
# Create snapshot via REST API
curl -X POST -H "api-key: $QDRANT_API_KEY" \
  http://localhost:6333/collections/archivist_docs/snapshots

# List snapshots
curl -H "api-key: $QDRANT_API_KEY" \
  http://localhost:6333/collections/archivist_docs/snapshots

# Copy off-host (cron job)
cp /var/lib/qdrant/storage/snapshots/archivist_docs/*.snapshot /mnt/backups/
```

### Cron: daily backup at 2 AM

```cron
0 2 * * * root cp /var/lib/archivist/search.db /mnt/backups/search_$(date +\%F).db && cp /var/lib/archivist/ingested_files.db /mnt/backups/tracker_$(date +\%F).db
```

For Qdrant, add the snapshot curl command before the copy.

## 7. Upgrade

### Qdrant (if using)

```bash
# Download new version
wget https://github.com/qdrant/qdrant/releases/download/vNEW_VERSION/qdrant-x86_64-unknown-linux-gnu.tar.gz
sudo systemctl stop qdrant
sudo mv qdrant /opt/qdrant/qdrant.new
sudo systemctl start qdrant
# Data is in /var/lib/qdrant/storage — not affected by binary upgrade
```

### Archivist

```bash
cd /opt/archivist/app
sudo -u archivist git pull
sudo -u archivist .venv/bin/pip install -r requirements.txt
sudo -u archivist .venv/bin/pip install -e .
sudo systemctl restart archivist-api
```

## 8. Firewall (UFW)

```bash
sudo ufw allow 22/tcp      # SSH
sudo ufw allow 80/tcp      # HTTP (nginx)
sudo ufw allow 443/tcp     # HTTPS (nginx)
sudo ufw enable
```

### If using Qdrant backend

```bash
sudo ufw allow 6333/tcp    # Qdrant (restrict to localhost if needed)

# To restrict Qdrant to localhost only:
sudo ufw deny 6333/tcp
# Qdrant still reachable via nginx proxy if needed, or keep open for direct CLI access
```

## Troubleshooting

### SQLite FTS5 issues

```bash
# Check database file
ls -la /var/lib/archivist/search.db

# Check permissions
sudo -u archivist /opt/archivist/app/.venv/bin/archivist status
```

### Qdrant connection refused

```bash
# Verify Qdrant is running
sudo systemctl status qdrant

# Check logs
sudo journalctl -u qdrant -f

# Test connectivity
curl http://localhost:6333/healthz
```

### Permission denied on ingest

```bash
# Ensure archivist user owns the data directory
sudo chown -R archivist:archivist /var/lib/archivist

# Check file permissions on source documents
ls -la /path/to/documents
```

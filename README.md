# SSL Certificate Monitor Service

Simple Python service for a VPS that checks SSL certificates for domains and subdomains, exposes a small web dashboard, and sends email alerts when certificates are close to expiration or a TLS check fails.

## Features

- Checks multiple domains and subdomains over port `443`
- Detects certificates that are close to expiration
- Detects TLS and connectivity failures
- Serves a dashboard at `/` and raw JSON at `/status`
- Runs as a long-lived `systemd` service
- Uses only the Python standard library

## Project Structure

- `ssl_monitor.py` - main monitoring service
- `.env.example` - example configuration
- `domains.txt.example` - example monitored domains list
- `ssl-monitor.service` - example `systemd` unit
- `nginx-monitor.example.conf` - example reverse proxy config
- `requirements.txt` - included for deployment symmetry

## How It Works

The monitor opens a TLS connection to each configured hostname on port `443`, reads the peer certificate, and calculates the remaining lifetime.

For each run it:

- logs successful checks
- collects warnings for certificates below the configured threshold
- collects errors for failed TLS or network checks
- updates the local status dashboard
- optionally sends an email alert when issues are present

## Quick Start

1. Copy the project to your server, for example to `/opt/ssl-monitor`
2. Copy `.env.example` to `.env`
3. Copy `domains.txt.example` to `domains.txt`
4. Update both files with your real values
5. Run a one-time test

```bash
RUN_MODE=once python3 ssl_monitor.py
```

If the test looks good, install the service:

```bash
sudo cp ssl-monitor.service /etc/systemd/system/ssl-monitor.service
sudo systemctl daemon-reload
sudo systemctl enable --now ssl-monitor
sudo systemctl status ssl-monitor
```

## Configuration

### Environment variables

| Variable | Description |
| --- | --- |
| `RUN_MODE` | `loop` for service mode, `once` for a one-time check |
| `CHECK_INTERVAL_SECONDS` | Delay between monitoring runs |
| `EXPIRY_WARNING_DAYS` | Days remaining threshold for warnings |
| `SOCKET_TIMEOUT_SECONDS` | Network timeout for TLS checks |
| `STATUS_HOST` | Bind address for the local HTTP status server |
| `STATUS_PORT` | Port for the local HTTP status server |
| `SMTP_HOST` | SMTP server hostname |
| `SMTP_PORT` | SMTP server port |
| `SMTP_USERNAME` | SMTP login username |
| `SMTP_PASSWORD` | SMTP login password |
| `SMTP_SENDER` | Sender email address |
| `SMTP_RECIPIENT` | Recipient email address |
| `SMTP_STARTTLS` | `true` or `false` |

### Example `.env`

```env
RUN_MODE=loop
CHECK_INTERVAL_SECONDS=21600
EXPIRY_WARNING_DAYS=21
SOCKET_TIMEOUT_SECONDS=10
STATUS_HOST=127.0.0.1
STATUS_PORT=8080

SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USERNAME=monitor@example.com
SMTP_PASSWORD=change-me
SMTP_SENDER=monitor@example.com
SMTP_RECIPIENT=admin@example.com
SMTP_STARTTLS=true
```

### Example `domains.txt`

```text
example.com
www.example.com
api.example.com
```

## Dashboard and API

Once the service is running:

- `/` returns a human-friendly dashboard
- `/status` returns JSON
- `/health` returns the same JSON status payload

This makes it easy to use the service both in a browser and from other monitoring tools.

## Reverse Proxy Example

Example Nginx config:

```nginx
server {
    listen 80;
    server_name monitor.example.com;

    location / {
        proxy_pass http://127.0.0.1:8080/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

If you run a reverse proxy manager or Docker-based proxy, you may need to forward to a host bridge IP instead of `127.0.0.1`.

## systemd Notes

The included `ssl-monitor.service` uses:

- working directory: `/opt/ssl-monitor`
- Python path: `/usr/bin/python3`
- user: `www-data`

Adjust these values to match your server.

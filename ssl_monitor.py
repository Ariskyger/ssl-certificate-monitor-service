import logging
import os
import smtplib
import socket
import ssl
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from threading import Lock, Thread
from typing import Iterable


BASE_DIR = Path(__file__).resolve().parent
DOMAINS_FILE = BASE_DIR / "domains.txt"


def load_env_file(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


load_env_file(BASE_DIR / ".env")


def env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name, default)
    return value.strip() if isinstance(value, str) else value


@dataclass
class Config:
    check_interval_seconds: int
    expiry_warning_days: int
    socket_timeout_seconds: int
    status_host: str
    status_port: int
    smtp_host: str | None
    smtp_port: int
    smtp_username: str | None
    smtp_password: str | None
    smtp_sender: str | None
    smtp_recipient: str | None
    smtp_starttls: bool


def load_config() -> Config:
    return Config(
        check_interval_seconds=int(env("CHECK_INTERVAL_SECONDS", "21600")),
        expiry_warning_days=int(env("EXPIRY_WARNING_DAYS", "21")),
        socket_timeout_seconds=int(env("SOCKET_TIMEOUT_SECONDS", "10")),
        status_host=env("STATUS_HOST", "127.0.0.1"),
        status_port=int(env("STATUS_PORT", "8080")),
        smtp_host=env("SMTP_HOST"),
        smtp_port=int(env("SMTP_PORT", "587")),
        smtp_username=env("SMTP_USERNAME"),
        smtp_password=env("SMTP_PASSWORD"),
        smtp_sender=env("SMTP_SENDER"),
        smtp_recipient=env("SMTP_RECIPIENT"),
        smtp_starttls=env("SMTP_STARTTLS", "true").lower() == "true",
    )


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


class StatusStore:
    def __init__(self) -> None:
        self._lock = Lock()
        self._snapshot = {
            "status": "starting",
            "checked_at": None,
            "healthy": [],
            "failures": [],
        }

    def update(self, healthy: list[str], failures: list[str]) -> None:
        with self._lock:
            self._snapshot = {
                "status": "ok" if not failures else "warning",
                "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
                "healthy": healthy,
                "failures": failures,
            }

    def error(self, message: str) -> None:
        with self._lock:
            self._snapshot = {
                "status": "error",
                "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
                "healthy": [],
                "failures": [message],
            }

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._snapshot)


STATUS_STORE = StatusStore()


class StatusHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        snapshot = STATUS_STORE.snapshot()

        if self.path == "/":
            self._send_html(render_status_page(snapshot))
            return

        if self.path in ("/health", "/status"):
            self._send_json(snapshot)
            return

        self.send_response(404)
        self.end_headers()

    def _send_json(self, payload: dict) -> None:
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def render_status_page(snapshot: dict) -> str:
    status = str(snapshot.get("status", "unknown"))
    checked_at = str(snapshot.get("checked_at") or "Not checked yet")
    healthy = list(snapshot.get("healthy", []))
    failures = list(snapshot.get("failures", []))

    status_class = {
        "ok": "ok",
        "warning": "warning",
        "error": "error",
        "starting": "starting",
    }.get(status, "starting")

    healthy_items = "".join(
        f"<li><div class='entry-head'><strong>{escape(split_host(item)[0])}</strong>"
        f"<span class='badge ok'>Aktiv</span></div><span>{escape(split_host(item)[1])}</span></li>"
        for item in healthy
    )
    failure_items = "".join(
        f"<li><div class='entry-head'><strong>Issue</strong><span class='badge error'>Review</span></div>"
        f"<span>{escape(item)}</span></li>"
        for item in failures
    )

    status_text = {
        "ok": "All systems healthy",
        "warning": "Attention needed",
        "error": "Error detected",
        "starting": "Starting up",
    }.get(status, "Unknown status")

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SSL Certificate Monitor</title>
  <style>
    :root {{
      --bg: #f3eee5;
      --panel: rgba(255, 252, 246, 0.88);
      --panel-strong: #fffdf9;
      --ink: #182019;
      --muted: #667063;
      --line: rgba(122, 113, 96, 0.18);
      --ok: #1d7a4a;
      --warning: #b27112;
      --error: #a53c33;
      --starting: #4b6482;
      --shadow: 0 24px 70px rgba(40, 34, 24, 0.10);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", Arial, sans-serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(29,122,74,.16), transparent 28%),
        radial-gradient(circle at 85% 0%, rgba(178,113,18,.16), transparent 24%),
        linear-gradient(180deg, #faf7f1 0%, var(--bg) 100%);
    }}
    .wrap {{ max-width: 1180px; margin: 0 auto; padding: 30px 18px 42px; }}
    .hero, .card {{
      background: var(--panel);
      backdrop-filter: blur(14px);
      border: 1px solid var(--line);
      border-radius: 28px;
      box-shadow: var(--shadow);
    }}
    .hero {{
      padding: 30px;
      position: relative;
      overflow: hidden;
      background:
        linear-gradient(135deg, rgba(255,255,255,.84), rgba(255,251,243,.72)),
        var(--panel);
    }}
    .hero::after {{
      content: "";
      position: absolute;
      right: -60px;
      top: -60px;
      width: 220px;
      height: 220px;
      border-radius: 50%;
      background: radial-gradient(circle, rgba(29,122,74,.16), transparent 62%);
      pointer-events: none;
    }}
    .eyebrow {{ color: var(--muted); font-size: 12px; letter-spacing: .16em; text-transform: uppercase; }}
    h1 {{ margin: 10px 0 12px; font-size: clamp(32px, 5vw, 58px); line-height: .96; max-width: 760px; }}
    .hero-row {{
      display: flex;
      gap: 16px;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
    }}
    .pill {{
      display: inline-flex; align-items: center; gap: 10px; border-radius: 999px;
      padding: 11px 16px; font-weight: 700; background: rgba(77,100,127,.14); color: var(--starting);
    }}
    .pill.ok {{ background: rgba(31,122,79,.12); color: var(--ok); }}
    .pill.warning {{ background: rgba(168,107,23,.14); color: var(--warning); }}
    .pill.error {{ background: rgba(157,63,52,.14); color: var(--error); }}
    .dot {{ width: 10px; height: 10px; border-radius: 50%; background: currentColor; }}
    .sub {{ margin-top: 12px; color: var(--muted); font-size: 15px; }}
    .hero-meta {{
      margin-top: 20px;
      display: flex;
      gap: 14px;
      flex-wrap: wrap;
    }}
    .meta-chip {{
      padding: 10px 14px;
      border-radius: 16px;
      border: 1px solid var(--line);
      background: rgba(255,255,255,.58);
      color: var(--muted);
      font-size: 14px;
    }}
    .stats, .grid {{ display: grid; gap: 16px; margin-top: 18px; }}
    .stats {{ grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); }}
    .grid {{ grid-template-columns: minmax(0, 2fr) minmax(320px, 1fr); }}
    .card {{ padding: 22px; }}
    .label {{ color: var(--muted); font-size: 12px; letter-spacing: .10em; text-transform: uppercase; margin-bottom: 10px; }}
    .value {{ font-size: 34px; font-weight: 700; line-height: 1; }}
    .mini {{ color: var(--muted); margin-top: 8px; font-size: 14px; }}
    .list {{ list-style: none; padding: 0; margin: 0; display: grid; gap: 10px; }}
    .list li {{
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 16px;
      background: rgba(255,255,255,.68);
      transition: transform .16s ease, box-shadow .16s ease;
    }}
    .list li:hover {{
      transform: translateY(-1px);
      box-shadow: 0 10px 28px rgba(35, 31, 24, .06);
    }}
    .entry-head {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      margin-bottom: 6px;
    }}
    .list strong {{ display: block; font-size: 16px; }}
    .list span {{ color: var(--muted); font-size: 14px; }}
    .badge {{
      border-radius: 999px;
      padding: 6px 10px;
      font-size: 12px;
      font-weight: 700;
      white-space: nowrap;
    }}
    .badge.ok {{ background: rgba(31,122,79,.12); color: var(--ok); }}
    .badge.error {{ background: rgba(157,63,52,.12); color: var(--error); }}
    .empty {{ margin: 0; color: var(--muted); }}
    a {{ color: inherit; text-decoration: none; border-bottom: 1px dashed rgba(24,32,25,.28); }}
    .section-title {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 14px;
    }}
    .section-title h2 {{
      margin: 0;
      font-size: 22px;
    }}
    .section-title span {{
      color: var(--muted);
      font-size: 14px;
    }}
    @media (max-width: 860px) {{
      .grid {{ grid-template-columns: 1fr; }}
      .hero {{ padding: 24px; }}
      .card {{ padding: 18px; }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <div class="eyebrow">SSL Certificate Monitor</div>
      <div class="hero-row">
        <h1>Overview of your monitored domains</h1>
        <div class="pill {status_class}"><span class="dot"></span><span>{escape(status_text)}</span></div>
      </div>
      <div class="sub">See at a glance whether your certificates are reachable and when they expire.</div>
      <div class="hero-meta">
        <div class="meta-chip">Last check: {escape(checked_at)}</div>
        <div class="meta-chip">Monitored domains: {len(healthy) + len(failures)}</div>
      </div>
    </section>
    <section class="stats">
      <article class="card">
        <div class="label">Healthy certificates</div>
        <div class="value">{len(healthy)}</div>
        <div class="mini">Currently without warnings</div>
      </article>
      <article class="card">
        <div class="label">Issues</div>
        <div class="value">{len(failures)}</div>
        <div class="mini">Warnings or errors</div>
      </article>
      <article class="card">
        <div class="label">Status API</div>
        <div class="value" style="font-size:20px;"><a href="/status">/status</a></div>
        <div class="mini">JSON for tools and automations</div>
      </article>
    </section>
    <section class="grid">
      <article class="card">
        <div class="section-title">
          <h2>Healthy certificates</h2>
          <span>{len(healthy)} entries</span>
        </div>
        {"<ul class='list'>" + healthy_items + "</ul>" if healthy_items else "<p class='empty'>No successful certificate checks yet.</p>"}
      </article>
      <article class="card">
        <div class="section-title">
          <h2>Warnings & errors</h2>
          <span>{len(failures)} entries</span>
        </div>
        {"<ul class='list'>" + failure_items + "</ul>" if failure_items else "<p class='empty'>No active issues right now.</p>"}
      </article>
    </section>
  </div>
</body>
</html>"""


def split_host(line: str) -> tuple[str, str]:
    if ":" not in line:
        return line, ""
    host, rest = line.split(":", 1)
    return host.strip(), rest.strip()


def start_status_server(config: Config) -> None:
    server = ThreadingHTTPServer((config.status_host, config.status_port), StatusHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logging.info(
        "Status endpoint available on http://%s:%s",
        config.status_host,
        config.status_port,
    )


def load_domains(path: Path) -> list[str]:
    if not path.exists():
        raise FileNotFoundError(f"Domain list not found: {path}")

    domains: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        domains.append(line)
    return domains


def fetch_certificate_expiry(hostname: str, timeout: int) -> datetime:
    context = ssl.create_default_context()
    with socket.create_connection((hostname, 443), timeout=timeout) as sock:
        with context.wrap_socket(sock, server_hostname=hostname) as secure_sock:
            certificate = secure_sock.getpeercert()

    expires_at = certificate["notAfter"]
    return datetime.strptime(expires_at, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)


def send_email_alert(config: Config, subject: str, body: str) -> None:
    required_values = [
        config.smtp_host,
        config.smtp_sender,
        config.smtp_recipient,
    ]
    if any(not value for value in required_values):
        logging.warning("Email alert skipped because SMTP settings are incomplete.")
        return

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config.smtp_sender
    message["To"] = config.smtp_recipient
    message.set_content(body)

    try:
        with smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=30) as server:
            if config.smtp_starttls:
                server.starttls()
            if config.smtp_username and config.smtp_password:
                server.login(config.smtp_username, config.smtp_password)
            server.send_message(message)
    except smtplib.SMTPException as exc:
        logging.exception("Email alert could not be sent: %s", exc)


def format_report_line(hostname: str, expires_at: datetime, days_left: int) -> str:
    return (
        f"{hostname}: expires on {expires_at.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
        f" ({days_left} days left)"
    )


def iter_failures(domains: Iterable[str], config: Config) -> tuple[list[str], list[str]]:
    healthy: list[str] = []
    failures: list[str] = []
    now = datetime.now(timezone.utc)

    for hostname in domains:
        try:
            expires_at = fetch_certificate_expiry(hostname, config.socket_timeout_seconds)
            days_left = (expires_at - now).days
            healthy.append(format_report_line(hostname, expires_at, days_left))
            if days_left <= config.expiry_warning_days:
                failures.append(
                    f"{hostname}: certificate expires soon on "
                    f"{expires_at.strftime('%Y-%m-%d %H:%M:%S UTC')} ({days_left} days left)"
                )
        except Exception as exc:
            failures.append(f"{hostname}: check failed ({exc})")

    return healthy, failures


def run_once(config: Config) -> None:
    domains = load_domains(DOMAINS_FILE)
    healthy, failures = iter_failures(domains, config)
    STATUS_STORE.update(healthy, failures)

    for line in healthy:
        logging.info(line)

    if failures:
        subject = f"SSL monitor alert: {len(failures)} issue(s)"
        body = "\n".join(failures)
        logging.warning(body)
        send_email_alert(config, subject, body)
    else:
        logging.info("All monitored certificates are healthy.")


def main() -> None:
    setup_logging()
    config = load_config()
    run_mode = env("RUN_MODE", "loop").lower()

    if run_mode == "once":
        run_once(config)
        return

    start_status_server(config)
    logging.info("SSL monitor started.")
    while True:
        try:
            run_once(config)
        except Exception as exc:
            STATUS_STORE.error(str(exc))
            logging.exception("Monitor cycle failed: %s", exc)
        time.sleep(config.check_interval_seconds)


if __name__ == "__main__":
    main()

#!/usr/bin/env bash
#
# MCC Portal — lab PC bootstrap (Ubuntu Server 24.04 LTS)
# =========================================================================
# One-shot setup for a blank machine: checks every dependency, installs
# whatever's missing, and gets the portal running as a systemd service with
# its own hourly/nightly jobs. Safe to re-run — every step below checks
# "is this already done?" before doing it, so if something fails partway
# through (network hiccup, wrong DNS token, whatever), fix that one thing
# and just run the script again rather than cleaning up by hand.
#
# What this does NOT do, on purpose:
#   - Fabricate secrets. GOOGLE_CLIENT_ID/SECRET and the real PORTAL_HOST
#     have to come from you (Google Cloud Console, IT's DNS record) — the
#     script checks .env has them and stops with a clear message if not,
#     rather than half-configuring something that will fail mysteriously
#     later. See portal/.env.example.
#   - Fully automate HTTPS. Plain Caddy (installed here) can reverse-proxy
#     over plain HTTP on the LAN today. Real DNS-01 HTTPS needs a Caddy
#     build with your DNS provider's plugin baked in, which depends on
#     info only IT has (see the "Once you know your DNS provider" section
#     printed at the end).
#
# Usage:
#   git clone https://github.com/WhiteWalker07/MCC_portal.git
#   cd MCC_portal/portal
#   cp .env.example .env && nano .env   # fill in GOOGLE_CLIENT_ID/SECRET etc.
#   sudo bash deploy/setup.sh
#
# Must be run from inside portal/ (the directory with manage.py) as a user
# with sudo access — the script uses `sudo` for the specific commands that
# need it (package install, systemd, ufw), not for everything, so it can
# also just be run as a normal user and it'll prompt for your password when
# it actually needs to.

set -euo pipefail

# ── 0. Where are we, who are we ─────────────────────────────────────────────

if [[ ! -f "manage.py" ]] || [[ ! -f "mccportal/settings.py" ]]; then
  echo "Run this from inside the 'portal' directory (the one with manage.py)." >&2
  echo "e.g.: cd MCC_portal/portal && sudo bash deploy/setup.sh" >&2
  exit 1
fi

APP_DIR="$(pwd)"
# The service runs as whoever actually owns these files (so file permissions
# stay sane) — normally the person who ran `git clone`, not root.
SERVICE_USER="${SUDO_USER:-$(whoami)}"
SERVICE_GROUP="$(id -gn "$SERVICE_USER")"
VENV_DIR="$APP_DIR/.venv"
GUNICORN_PORT=8000

log()  { printf '\n\033[1;36m▶ %s\033[0m\n' "$1"; }
ok()   { printf '  \033[1;32m✓\033[0m %s\n' "$1"; }
warn() { printf '  \033[1;33m! %s\033[0m\n' "$1"; }
have() { command -v "$1" >/dev/null 2>&1; }

if [[ $EUID -eq 0 && -z "${SUDO_USER:-}" ]]; then
  echo "Don't run this directly as root — run it as your normal user with sudo access" >&2
  echo "(the script calls sudo itself for the steps that need it)." >&2
  exit 1
fi

log "Setting up MCC Portal in $APP_DIR (service will run as $SERVICE_USER)"

# ── 1. OS sanity check ──────────────────────────────────────────────────────

if [[ -r /etc/os-release ]]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  if [[ "${ID:-}" != "ubuntu" && "${ID_LIKE:-}" != *debian* ]]; then
    warn "This script assumes Ubuntu/Debian (apt, systemd, ufw). Detected: ${PRETTY_NAME:-unknown}."
    warn "It may still mostly work on another systemd-based distro, but the package"
    warn "install steps below will likely need hand-editing first."
  else
    ok "${PRETTY_NAME:-Debian-based Linux} detected"
  fi
else
  warn "Can't detect the OS (no /etc/os-release) — continuing, but this script targets Ubuntu."
fi

# ── 2. System packages ───────────────────────────────────────────────────────

log "Checking system dependencies"

NEED_APT_UPDATE=0
apt_install() {
  # Installs only if genuinely missing, and only runs `apt update` once,
  # right before the first thing that actually needs it — not on every
  # re-run of this script.
  if ! dpkg -s "$1" >/dev/null 2>&1; then
    if [[ $NEED_APT_UPDATE -eq 1 ]]; then
      sudo apt-get update -qq
      NEED_APT_UPDATE=0
    fi
    echo "  installing $1…"
    sudo apt-get install -y -qq "$1" >/dev/null
    ok "installed $1"
  else
    ok "$1 already installed"
  fi
}
NEED_APT_UPDATE=1

apt_install python3
apt_install python3-venv
apt_install python3-pip
apt_install git
apt_install curl
apt_install sqlite3   # CLI tool for ad-hoc DB inspection; Python's own sqlite3 module needs nothing extra

PY_VERSION="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
PY_MAJOR="$(echo "$PY_VERSION" | cut -d. -f1)"
PY_MINOR="$(echo "$PY_VERSION" | cut -d. -f2)"
if [[ "$PY_MAJOR" -lt 3 || ( "$PY_MAJOR" -eq 3 && "$PY_MINOR" -lt 10 ) ]]; then
  echo "Python $PY_VERSION found, but Django 5.2 needs 3.10+ (the code uses PEP 604" >&2
  echo "union syntax). Ubuntu 22.04+ ships a new enough python3 by default." >&2
  exit 1
fi
ok "Python $PY_VERSION"

if ! have caddy; then
  echo "  installing Caddy (official apt repo)…"
  sudo apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https >/dev/null
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    | sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
  sudo apt-get update -qq
  sudo apt-get install -y -qq caddy >/dev/null
  ok "installed Caddy"
else
  ok "Caddy already installed ($(caddy version | head -n1))"
fi

# ── 3. Python virtual environment ────────────────────────────────────────────

log "Setting up the Python virtual environment"

if [[ ! -d "$VENV_DIR" ]]; then
  python3 -m venv "$VENV_DIR"
  ok "created $VENV_DIR"
else
  ok "$VENV_DIR already exists"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt
# gunicorn (not waitress — that's the Windows dev-machine choice; gunicorn
# doesn't run on Windows at all, it needs os.fork()) is Linux-only on
# purpose, so it isn't in the shared requirements.txt.
pip install --quiet gunicorn
ok "dependencies installed"

# ── 4. .env — check, don't fabricate ────────────────────────────────────────

log "Checking configuration"

if [[ ! -f ".env" ]]; then
  echo "No .env file found. Copy the template and fill in the real values first:" >&2
  echo "  cp .env.example .env && nano .env" >&2
  echo "At minimum: GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, PORTAL_HOST." >&2
  exit 1
fi

missing=()
for key in GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET; do
  value="$(grep -E "^${key}=" .env | tail -n1 | cut -d= -f2-)"
  if [[ -z "$value" || "$value" == "xxxx"* ]]; then
    missing+=("$key")
  fi
done
if [[ ${#missing[@]} -gt 0 ]]; then
  echo "These are still unset (or left as the placeholder) in .env: ${missing[*]}" >&2
  echo "Get them from Google Cloud Console → APIs & Services → Credentials." >&2
  exit 1
fi
ok "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET are set"

PORTAL_HOST="$(grep -E '^PORTAL_HOST=' .env | tail -n1 | cut -d= -f2-)"
if [[ -z "$PORTAL_HOST" || "$PORTAL_HOST" == "localhost" ]]; then
  warn "PORTAL_HOST is unset or still 'localhost' — fine for now, but Google sign-in"
  warn "from any device but this one needs a real hostname. Revisit once IT gives you one."
else
  ok "PORTAL_HOST=$PORTAL_HOST"
fi

# ── 5. Database, static files, real data ────────────────────────────────────

log "Preparing the database"

python manage.py migrate --noinput
ok "migrations applied"

python manage.py collectstatic --noinput >/dev/null
ok "static files collected"

python manage.py seed_real_data
ok "committees + team roster seeded (safe to re-run — never resets points/strikes)"

if ! python manage.py shell -c "from django.contrib.auth import get_user_model; import sys; sys.exit(0 if get_user_model().objects.filter(is_superuser=True).exists() else 1)" 2>/dev/null; then
  warn "No superuser exists yet — you'll want a break-glass admin account for when"
  warn "Google sign-in or the internet is down. Create one now:"
  warn "  $VENV_DIR/bin/python manage.py createsuperuser"
  warn "Give it an email nobody's real Google account uses (e.g. breakglass-admin@mcc-portal.local)"
  warn "— using a real person's address here breaks their actual Google sign-in later."
else
  ok "a superuser already exists"
fi

deactivate

# ── 6. systemd service — the app itself ─────────────────────────────────────

log "Installing the systemd service"

sudo tee /etc/systemd/system/mcc-portal.service >/dev/null <<EOF
[Unit]
Description=MCC Portal (Django/gunicorn)
After=network.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_GROUP
WorkingDirectory=$APP_DIR
ExecStart=$VENV_DIR/bin/gunicorn mccportal.wsgi:application --bind 127.0.0.1:$GUNICORN_PORT --workers 3
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
ok "wrote /etc/systemd/system/mcc-portal.service"

# ── 7. systemd timers — the hourly deadline sweep + nightly backup ─────────

log "Installing the scheduled jobs (replaces Windows Task Scheduler / the old GitHub Actions cron)"

sudo tee /etc/systemd/system/mcc-deadline-check.service >/dev/null <<EOF
[Unit]
Description=MCC Portal — hourly deadline sweep
[Service]
Type=oneshot
User=$SERVICE_USER
WorkingDirectory=$APP_DIR
ExecStart=$VENV_DIR/bin/python manage.py deadline_check
EOF

sudo tee /etc/systemd/system/mcc-deadline-check.timer >/dev/null <<EOF
[Unit]
Description=Run the MCC Portal deadline sweep hourly
[Timer]
OnCalendar=hourly
Persistent=true
[Install]
WantedBy=timers.target
EOF

sudo tee /etc/systemd/system/mcc-backup.service >/dev/null <<EOF
[Unit]
Description=MCC Portal — nightly database backup
[Service]
Type=oneshot
User=$SERVICE_USER
WorkingDirectory=$APP_DIR
ExecStart=$VENV_DIR/bin/python manage.py backup_db
EOF

sudo tee /etc/systemd/system/mcc-backup.timer >/dev/null <<EOF
[Unit]
Description=Run the MCC Portal backup nightly
[Timer]
OnCalendar=*-*-* 02:30:00
Persistent=true
[Install]
WantedBy=timers.target
EOF
ok "wrote the deadline-check and backup service+timer units"

sudo systemctl daemon-reload
sudo systemctl enable --now mcc-portal.service
sudo systemctl enable --now mcc-deadline-check.timer
sudo systemctl enable --now mcc-backup.timer
ok "enabled mcc-portal.service, mcc-deadline-check.timer, mcc-backup.timer (all start on boot)"

# ── 8. Caddy — plain reverse proxy for now ──────────────────────────────────

log "Configuring Caddy"

if [[ -z "$PORTAL_HOST" || "$PORTAL_HOST" == "localhost" ]]; then
  sudo tee /etc/caddy/Caddyfile >/dev/null <<EOF
# Placeholder — no real hostname configured yet (see .env's PORTAL_HOST).
# Serves plain HTTP on the LAN so you can at least reach the app today.
# Once IT gives you a subdomain, see deploy/README.md for the real,
# HTTPS/DNS-01 config to replace this with.
:80 {
	reverse_proxy 127.0.0.1:$GUNICORN_PORT
}
EOF
  warn "Caddy set up for plain HTTP on port 80 (no domain yet) — this is temporary."
else
  sudo tee /etc/caddy/Caddyfile >/dev/null <<EOF
# NOTE: this still serves plain HTTP. Real HTTPS via Let's Encrypt DNS-01
# needs a Caddy build with your DNS provider's plugin — see
# deploy/README.md "Once you know your DNS provider". Once you've built
# that binary, replace this whole file with the DNS-01 version shown there.
$PORTAL_HOST:80 {
	reverse_proxy 127.0.0.1:$GUNICORN_PORT
}
EOF
fi
sudo systemctl reload caddy || sudo systemctl restart caddy
ok "Caddy configured and (re)started"

# ── 9. Firewall ──────────────────────────────────────────────────────────────

log "Configuring the firewall (ufw)"

if have ufw; then
  sudo ufw allow OpenSSH >/dev/null
  sudo ufw allow 80/tcp >/dev/null
  sudo ufw allow 443/tcp >/dev/null
  if ! sudo ufw status | grep -q "Status: active"; then
    sudo ufw --force enable >/dev/null
  fi
  ok "ufw: SSH, 80, 443 allowed; everything else denied by default"
else
  apt_install ufw
  sudo ufw allow OpenSSH >/dev/null
  sudo ufw allow 80/tcp >/dev/null
  sudo ufw allow 443/tcp >/dev/null
  sudo ufw --force enable >/dev/null
  ok "installed and configured ufw"
fi

# ── 10. Never sleep ──────────────────────────────────────────────────────────

log "Disabling sleep/suspend (a sleeping server is an outage)"
sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target >/dev/null 2>&1 || true
ok "sleep/suspend/hibernate masked"

# ── Done ─────────────────────────────────────────────────────────────────────

log "Done"
echo "  mcc-portal.service          — check with: systemctl status mcc-portal"
echo "  mcc-deadline-check.timer    — check with: systemctl list-timers mcc-deadline-check.timer"
echo "  mcc-backup.timer            — check with: systemctl list-timers mcc-backup.timer"
echo "  Logs:                         journalctl -u mcc-portal -f"
echo
if [[ -z "$PORTAL_HOST" || "$PORTAL_HOST" == "localhost" ]]; then
  echo "  Reachable now at: http://<this-machine's-LAN-IP>/ (plain HTTP, no real domain yet)"
else
  echo "  Reachable now at: http://$PORTAL_HOST/ (plain HTTP — see deploy/README.md for HTTPS)"
fi
echo
echo "  Still to do:"
echo "  1. Get IT to point a subdomain at this machine's LAN IP and tell you their DNS provider."
echo "  2. Follow deploy/README.md's DNS-01 section to get real HTTPS + register the Google"
echo "     OAuth redirect URI against it."
echo "  3. If you skipped it above, create a break-glass superuser:"
echo "       $VENV_DIR/bin/python manage.py createsuperuser"
echo "  4. Restore-test a backup before you trust it: see deploy/README.md."

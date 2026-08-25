# Deploying on the lab PC (Ubuntu Server 24.04 LTS)

Run [`setup.sh`](setup.sh) first — it checks and installs every dependency,
sets up the app as a `systemd` service, wires up the hourly deadline sweep and
nightly backup as `systemd` timers, configures the firewall, and disables
sleep. This file covers everything that's left afterward: the parts that
depend on information only IT has, and how to operate the thing day to day.

## 1. Get real HTTPS (once you know your DNS provider)

`setup.sh` leaves Caddy serving plain HTTP — that's deliberate, not a bug. Real
HTTPS via Let's Encrypt needs a **DNS-01** challenge (proves you own the domain
via a DNS TXT record, not an inbound connection — no port-forwarding needed,
unlike the alternative HTTP-01 approach), and that needs a Caddy build with
your specific DNS provider's plugin compiled in. The plain `apt install caddy`
binary doesn't include any DNS provider plugins.

**Ask IT two things:** the subdomain (e.g. `mcc.iimsirmaur.ac.in`) pointed at
this machine's LAN IP, and which DNS provider hosts `iimsirmaur.ac.in`
(Cloudflare, Route53, Google Domains, etc. — the Caddy plugin is
provider-specific).

Once you know the provider, build Caddy with its plugin using
[`xcaddy`](https://github.com/caddyserver/xcaddy):

```bash
sudo apt install golang-go   # xcaddy needs a Go toolchain to build
go install github.com/caddyserver/xcaddy/cmd/xcaddy@latest
~/go/bin/xcaddy build --with github.com/caddy-dns/<provider>   # e.g. caddy-dns/cloudflare

sudo systemctl stop caddy
sudo cp caddy /usr/bin/caddy    # replace the apt-installed binary
sudo systemctl start caddy
```

Then replace `/etc/caddy/Caddyfile`:

```
mcc.iimsirmaur.ac.in {
	tls {
		dns <provider> {env.DNS_API_TOKEN}
	}
	reverse_proxy 127.0.0.1:8000
}
```

Put the actual API token in `/etc/caddy/caddy.env` (mode 600, owned by
`caddy`) and reference it from the systemd unit — don't paste it directly into
the Caddyfile:

```bash
echo 'DNS_API_TOKEN=your-real-token' | sudo tee /etc/caddy/caddy.env
sudo chmod 600 /etc/caddy/caddy.env
sudo chown caddy:caddy /etc/caddy/caddy.env
sudo systemctl edit caddy   # add: [Service]\nEnvironmentFile=/etc/caddy/caddy.env
sudo systemctl restart caddy
```

Watch it get a real certificate: `sudo journalctl -u caddy -f`.

## 2. Register the real redirect URI with Google

Once HTTPS is live, add this as an **Authorized redirect URI** on the OAuth
client (Google Cloud Console → APIs & Services → Credentials):

```
https://mcc.iimsirmaur.ac.in/accounts/google/login/callback/
```

Update `.env`'s `PORTAL_HOST` and `PORTAL_HTTPS=1`, then:

```bash
cd MCC_portal/portal
sudo systemctl restart mcc-portal
```

## 3. Day-to-day operations

```bash
# Status / logs
systemctl status mcc-portal
journalctl -u mcc-portal -f
systemctl list-timers mcc-deadline-check.timer mcc-backup.timer

# Deploy a code update
cd MCC_portal/portal
git pull
source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
deactivate
sudo systemctl restart mcc-portal

# Re-apply the roster after editing core/seed_data.py — never resets
# points/strikes/availability history
.venv/bin/python manage.py seed_real_data
```

## 4. Backups

`mcc-backup.timer` runs nightly at 02:30, writing to `data/backups/` on this
same machine — which protects against a bad deploy or accidental data change,
**not** against this machine's disk failing. Point `BACKUP_DIR` in `.env` at a
mounted network share or external drive once one's available.

**Test a restore before you trust it** — an untested backup isn't one:

```bash
cd MCC_portal/portal
cp data/backups/mcc-daily-<latest>.sqlite3 /tmp/restore-test.sqlite3
sqlite3 /tmp/restore-test.sqlite3 "select count(*) from core_committee; select count(*) from core_teammember;"
# should print 41 and 23 (or however many you've actually got)
```

## 5. Troubleshooting

- **Service won't start**: `journalctl -u mcc-portal -e` — almost always a
  missing/wrong `.env` value or a pending migration.
- **502 from Caddy**: `mcc-portal.service` probably isn't running —
  `systemctl status mcc-portal`.
- **Google sign-in fails with `redirect_uri_mismatch`**: the URI registered in
  Google Cloud Console has to match `PORTAL_HOST` (and the scheme — `https`
  once real TLS is up) exactly, trailing slash included.
- **Cert never issues**: check `journalctl -u caddy -f` — usually a wrong DNS
  API token, or the subdomain's DNS record hasn't propagated yet
  (`dig mcc.iimsirmaur.ac.in` from another machine to confirm).

# Deploying on Windows Server

Run [`setup.ps1`](setup.ps1) first, from an **elevated PowerShell** (Run as
Administrator) — it checks and installs every dependency, sets the app up as
a Windows service (`MCCPortal`, running Waitress — `gunicorn` doesn't run on
Windows at all, it needs `os.fork()`) with Caddy as a second service in front
of it, registers the hourly deadline sweep and nightly backup as Scheduled
Tasks, opens the firewall, and disables sleep/hibernate. This file covers
everything that's left afterward.

See [`README.md`](README.md) for the one shared step (making CI actually gate
the auto-merge) that applies regardless of which OS the lab PC ends up on.

## 1. Get real HTTPS (once you know your DNS provider)

`setup.ps1` leaves Caddy serving plain HTTP — that's deliberate, not a bug.
Real HTTPS via Let's Encrypt needs a **DNS-01** challenge (proves you own the
domain via a DNS TXT record, not an inbound connection — no port-forwarding
needed, unlike the alternative HTTP-01 approach), and that needs a Caddy build
with your specific DNS provider's plugin compiled in. The plain binary
`setup.ps1` downloads doesn't include any DNS provider plugins.

**Ask IT two things:** the subdomain (e.g. `mcc.iimsirmaur.ac.in`) pointed at
this machine's LAN IP, and which DNS provider hosts `iimsirmaur.ac.in`.
Don't take an internal DNS server's name at face value here (an AD-integrated
DNS server, `*-ad-vm.iimsirmaur.ac.in`-style, is common and answers fine for
machines *inside* the network, but it's not what Let's Encrypt's validators —
which query from the public internet — actually see). Confirm what the public
internet resolves, from any machine:

```powershell
Resolve-DnsName -Type NS iimsirmaur.ac.in -Server 8.8.8.8
```

`iimsirmaur.ac.in` resolves to **Google Cloud DNS**
(`ns-cloud-e1.googledomains.com` and friends) — worked example below. For a
different provider, swap the plugin module and Caddyfile block; see
[Caddy's DNS provider list](https://caddyserver.com/download) for the exact
module name and its own config syntax (they aren't all shaped alike).

Build Caddy with the plugin using
[`xcaddy`](https://github.com/caddyserver/xcaddy) — needs a Go toolchain:

```powershell
# Install Go if it's not already present:
Invoke-WebRequest -Uri "https://go.dev/dl/go1.23.4.windows-amd64.msi" -OutFile "$env:TEMP\go.msi"
Start-Process msiexec.exe -ArgumentList "/i", "$env:TEMP\go.msi", "/quiet" -Wait
$env:Path += ";C:\Program Files\Go\bin;$env:USERPROFILE\go\bin"

go install github.com/caddyserver/xcaddy/cmd/xcaddy@latest
xcaddy build --with github.com/caddy-dns/googleclouddns   # produces caddy.exe

Stop-Service Caddy
Copy-Item .\caddy.exe "C:\mcc-portal-tools\caddy.exe" -Force
Start-Service Caddy
```

**Credentials** — a GCP service account with the **DNS Administrator**
(`roles/dns.admin`) role on the project hosting the zone, and a downloaded
JSON key for it (GCP Console → IAM & Admin → Service Accounts → that account
→ Keys → Add Key → JSON). Needs someone with IAM access on that GCP project —
likely IT, unless you already have it. Put the key file on this machine and
point Caddy's service at it via an environment variable, same mechanism as
any other provider's API token would use:

```powershell
Copy-Item "<path to the downloaded key>.json" "C:\mcc-portal-tools\gcp-dns-key.json"
& "C:\mcc-portal-tools\nssm.exe" set Caddy AppEnvironmentExtra "GOOGLE_APPLICATION_CREDENTIALS=C:\mcc-portal-tools\gcp-dns-key.json"
```

Then replace `C:\mcc-portal-tools\Caddyfile`:

```
mcc.iimsirmaur.ac.in {
	tls {
		dns googleclouddns {
			gcp_project <your-gcp-project-id>
		}
	}
	reverse_proxy 127.0.0.1:8000
}
```

(`<your-gcp-project-id>` — the GCP project the service account and DNS zone
both live in, visible at the top of the GCP Console.)

```powershell
Restart-Service Caddy
```

Watch it get a real certificate: `Get-Content C:\mcc-portal-tools\caddy-stdout.log -Tail 50 -Wait`
(add `AppStdout`/`AppStderr` via `nssm set Caddy AppStdout ...` first if you
haven't already — `setup.ps1` doesn't configure Caddy's own log redirection,
only the app's).

**For a provider other than Google Cloud DNS**, the shape's the same but the
specifics differ: swap `github.com/caddy-dns/googleclouddns` for that
provider's module (e.g. `caddy-dns/cloudflare`, `caddy-dns/route53`), and its
Caddyfile block usually just wants an API token rather than a JSON key file
— e.g. `dns cloudflare {env.CF_API_TOKEN}` with the token set the same way
via `AppEnvironmentExtra`. Check that specific module's own README for its
exact directive names before assuming they match this example.

## 2. Register the real redirect URI with Google

Once HTTPS is live, add this as an **Authorized redirect URI** on the OAuth
client (Google Cloud Console → APIs & Services → Credentials):

```
https://mcc.iimsirmaur.ac.in/accounts/google/login/callback/
```

Update `.env`'s `PORTAL_HOST` and `PORTAL_HTTPS=1`, then:

```powershell
cd MCC_portal\portal
Restart-Service MCCPortal
```

## 3. Day-to-day operations

```powershell
# Status / logs
Get-Service MCCPortal, Caddy
Get-Content data\service-stderr.log -Tail 50 -Wait
Get-ScheduledTask -TaskName "MCC Portal*" | Get-ScheduledTaskInfo

# Deploy a code update
cd MCC_portal\portal
git pull
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe manage.py migrate
.venv\Scripts\python.exe manage.py collectstatic --noinput
Restart-Service MCCPortal

# Re-apply the roster after editing core/seed_data.py — never resets
# points/strikes/availability history
.venv\Scripts\python.exe manage.py seed_real_data
```

## 4. Backups

The nightly "MCC Portal - Nightly Backup" Scheduled Task runs at 02:30,
writing to `data\backups\` on this same machine — which protects against a
bad deploy or accidental data change, **not** against this machine's disk
failing. Point `BACKUP_DIR` in `.env` at a mounted network share or external
drive once one's available.

**Test a restore before you trust it** — an untested backup isn't one:

```powershell
cd MCC_portal\portal
$latest = Get-ChildItem data\backups\mcc-daily-*.sqlite3 | Sort-Object LastWriteTime -Descending | Select-Object -First 1
Copy-Item $latest.FullName "$env:TEMP\restore-test.sqlite3"
sqlite3 "$env:TEMP\restore-test.sqlite3" "select count(*) from core_committee; select count(*) from core_teammember;"
# should print 41 and 23 (or however many you've actually got) -- if `sqlite3`
# isn't on PATH, .venv\Scripts\python.exe -c "import sqlite3; ..." works too
```

## 5. Troubleshooting

- **Service won't start**: `Get-Content data\service-stderr.log -Tail 50` —
  almost always a missing/wrong `.env` value or a pending migration. Also
  check `Get-EventLog -LogName Application -Source nssm -Newest 10`.
- **502 / connection refused from Caddy**: `MCCPortal` service probably isn't
  running — `Get-Service MCCPortal`.
- **Google sign-in fails with `redirect_uri_mismatch`**: the URI registered in
  Google Cloud Console has to match `PORTAL_HOST` (and the scheme — `https`
  once real TLS is up) exactly, trailing slash included.
- **Cert never issues**: check Caddy's log output — usually a wrong DNS API
  token, or the subdomain's DNS record hasn't propagated yet
  (`Resolve-DnsName mcc.iimsirmaur.ac.in` from another machine to confirm).
- **Scheduled task didn't run**: Task Scheduler GUI (`taskschd.msc`) → find
  the task under the root folder → check its **History** tab; the most common
  cause is the task's configured account not having "Log on as a batch job"
  rights, or the venv path having moved.

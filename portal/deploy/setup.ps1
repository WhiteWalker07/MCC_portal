# MCC Portal — lab PC bootstrap (Windows Server)
# =============================================================================
# One-shot setup for a blank machine: checks every dependency, installs
# whatever's missing, and gets the portal running as a Windows service with
# its own hourly/nightly scheduled tasks. Safe to re-run — every step below
# checks "is this already done?" before doing it.
#
# What this does NOT do, on purpose (matches deploy/setup.sh's Linux version):
#   - Fabricate secrets. GOOGLE_CLIENT_ID/SECRET and the real PORTAL_HOST have
#     to come from you (Google Cloud Console, IT's DNS record) — the script
#     checks .env has them and stops with a clear message if not.
#   - Fully automate HTTPS. Plain Caddy (installed here) can reverse-proxy
#     over plain HTTP on the LAN today. Real DNS-01 HTTPS needs a Caddy build
#     with your DNS provider's plugin baked in — see deploy/README.md.
#
# Deliberately avoids winget: Windows Server Core has no winget at all, and
# even with Desktop Experience it's not guaranteed present or current, so
# every dependency here is fetched directly from its vendor's official
# download URL instead — works the same on every Windows Server edition.
#
# Usage (from an elevated PowerShell — Run as Administrator):
#   git clone https://github.com/WhiteWalker07/MCC_portal.git
#   cd MCC_portal\portal
#   Copy-Item .env.example .env; notepad .env   # fill in GOOGLE_CLIENT_ID/SECRET etc.
#   .\deploy\setup.ps1

# NOTE for future edits: keep string literals (double/single-quoted, and
# here-strings) plain ASCII. Windows PowerShell 5.1 reads a .ps1 file without
# a UTF-8 BOM using the system's ANSI codepage, which garbles multi-byte
# UTF-8 sequences (an em-dash silently broke this exact file's parsing that
# way once already — see git history). Comments are fine either way, since
# they're never tokenized as string content.

$ErrorActionPreference = "Stop"

# ── 0. Where are we, are we elevated ────────────────────────────────────────

if (-not (Test-Path "manage.py") -or -not (Test-Path "mccportal\settings.py")) {
    Write-Error "Run this from inside the 'portal' directory (the one with manage.py). e.g.: cd MCC_portal\portal; .\deploy\setup.ps1"
    exit 1
}

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Error "Re-run this from an elevated PowerShell (Run as Administrator) -- it needs to register services, scheduled tasks, and firewall rules."
    exit 1
}

$AppDir = (Get-Location).Path
$VenvDir = Join-Path $AppDir ".venv"
$ToolsDir = "C:\mcc-portal-tools"   # Caddy + NSSM live here, outside the repo
$GunicornPort = 8000                 # same var name as the Linux script for a diff-friendly pair; this is the Waitress port

function Write-Step($msg)  { Write-Host "`n>> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)    { Write-Host "   [ok] $msg" -ForegroundColor Green }
function Write-Warn($msg)  { Write-Host "   [!] $msg" -ForegroundColor Yellow }
function Test-CommandExist($name)   { [bool](Get-Command $name -ErrorAction SilentlyContinue) }

New-Item -ItemType Directory -Force -Path $ToolsDir | Out-Null

Write-Step "Setting up MCC Portal in $AppDir"

# ── 1. Python ────────────────────────────────────────────────────────────────

Write-Step "Checking Python"

$pythonOk = $false
$verOut = ""
$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if ($pythonCmd -and $pythonCmd.Source -notmatch "\\WindowsApps\\python[0-9.]*\.exe$") {
    # Deliberately no `2>&1` on the native call: under $ErrorActionPreference =
    # "Stop", PowerShell 5.1 wraps each redirected stderr line into a
    # terminating NativeCommandError, which crashed this exact line the first
    # time a broken "python" resolved to something that writes to stderr. The
    # try/catch is a second, independent safety net for any other way a
    # native call here could misbehave.
    try {
        $verOut = & python --version
        if ($LASTEXITCODE -eq 0 -and $verOut -match "Python (\d+)\.(\d+)") {
            $maj = [int]$Matches[1]; $min = [int]$Matches[2]
            if ($maj -gt 3 -or ($maj -eq 3 -and $min -ge 10)) { $pythonOk = $true }
        }
    } catch {
        Write-Warn "Existing 'python' at $($pythonCmd.Source) didn't run cleanly ($($_.Exception.Message)) -- installing a fresh one."
        # Fall through to installing a real one below.
    }
} elseif ($pythonCmd) {
    # This is Windows' "python" App Execution Alias -- a stub that either
    # opens the Microsoft Store or, run with arguments, errors outright. It's
    # discoverable via Get-Command like a real executable, so checking
    # existence alone isn't enough; it has to be excluded by path.
    Write-Warn "Found Windows' python stub (App Execution Alias) at $($pythonCmd.Source) -- that's not a real Python. Installing the real one."
}

if (-not $pythonOk) {
    Write-Host "   installing Python 3.12 (official python.org installer)..."
    $pyInstaller = Join-Path $env:TEMP "python-installer.exe"
    Invoke-WebRequest -Uri "https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe" -OutFile $pyInstaller
    Start-Process -FilePath $pyInstaller -ArgumentList "/quiet", "InstallAllUsers=1", "PrependPath=1", "Include_test=0" -Wait
    Remove-Item $pyInstaller -Force
    # Installer updates machine PATH; refresh this session's copy of it.
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
    Write-Ok "installed Python 3.12"
} else {
    Write-Ok "$verOut already installed"
}

# ── 2. Git ───────────────────────────────────────────────────────────────────

Write-Step "Checking Git"
if (-not (Test-CommandExist git)) {
    Write-Host "   installing Git for Windows..."
    $gitInstaller = Join-Path $env:TEMP "git-installer.exe"
    Invoke-WebRequest -Uri "https://github.com/git-for-windows/git/releases/download/v2.47.1.windows.1/Git-2.47.1-64-bit.exe" -OutFile $gitInstaller
    Start-Process -FilePath $gitInstaller -ArgumentList "/VERYSILENT", "/NORESTART" -Wait
    Remove-Item $gitInstaller -Force
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
    Write-Ok "installed Git"
} else {
    Write-Ok "Git already installed"
}

# ── 3. Caddy ─────────────────────────────────────────────────────────────────

Write-Step "Checking Caddy"
$CaddyExe = Join-Path $ToolsDir "caddy.exe"
if (-not (Test-Path $CaddyExe)) {
    Write-Host "   downloading Caddy..."
    # Pinned, like Python and Git above -- Caddy's release assets embed the
    # version in the filename (caddy_2.11.4_windows_amd64.zip, not
    # caddy_windows_amd64.zip), so there's no stable "latest" URL the way
    # some repos offer; .../releases/latest/download/<name> 404s here because
    # that exact name has never actually existed in any release.
    $caddyZip = Join-Path $env:TEMP "caddy.zip"
    Invoke-WebRequest -Uri "https://github.com/caddyserver/caddy/releases/download/v2.11.4/caddy_2.11.4_windows_amd64.zip" -OutFile $caddyZip
    Expand-Archive -Path $caddyZip -DestinationPath $ToolsDir -Force
    Remove-Item $caddyZip -Force
    Write-Ok "installed Caddy to $CaddyExe"
} else {
    Write-Ok "Caddy already present"
}

# ── 4. NSSM (wraps Waitress + Caddy as real Windows services) ──────────────

Write-Step "Checking NSSM"
$NssmExe = Join-Path $ToolsDir "nssm.exe"
if (-not (Test-Path $NssmExe)) {
    Write-Host "   downloading NSSM..."
    $nssmZip = Join-Path $env:TEMP "nssm.zip"
    Invoke-WebRequest -Uri "https://nssm.cc/release/nssm-2.24.zip" -OutFile $nssmZip
    Expand-Archive -Path $nssmZip -DestinationPath $env:TEMP -Force
    Copy-Item (Join-Path $env:TEMP "nssm-2.24\win64\nssm.exe") $NssmExe -Force
    Remove-Item $nssmZip -Force
    Remove-Item (Join-Path $env:TEMP "nssm-2.24") -Recurse -Force
    Write-Ok "installed NSSM to $NssmExe"
} else {
    Write-Ok "NSSM already present"
}

# ── 5. Python virtual environment ───────────────────────────────────────────

Write-Step "Setting up the Python virtual environment"
if (-not (Test-Path $VenvDir)) {
    python -m venv $VenvDir
    Write-Ok "created $VenvDir"
} else {
    Write-Ok "$VenvDir already exists"
}

$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
& $VenvPython -m pip install --quiet --upgrade pip
& $VenvPython -m pip install --quiet -r requirements.txt
& $VenvPython -m pip install --quiet waitress
Write-Ok "dependencies installed"

# ── 6. .env — check, don't fabricate ────────────────────────────────────────

Write-Step "Checking configuration"

if (-not (Test-Path ".env")) {
    Write-Error ".env not found. Copy the template and fill in the real values first: Copy-Item .env.example .env; notepad .env`nAt minimum: GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, PORTAL_HOST."
    exit 1
}

$envContent = Get-Content ".env" -Raw
$missing = @()
foreach ($key in @("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET")) {
    if ($envContent -notmatch "(?m)^$key=(.+)$" -or $Matches[1].Trim().StartsWith("xxxx")) {
        $missing += $key
    }
}
if ($missing.Count -gt 0) {
    Write-Error "These are still unset (or left as the placeholder) in .env: $($missing -join ', '). Get them from Google Cloud Console -> APIs & Services -> Credentials."
    exit 1
}
Write-Ok "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET are set"

$PortalHost = "localhost"
if ($envContent -match "(?m)^PORTAL_HOST=(.+)$") { $PortalHost = $Matches[1].Trim() }
if ($PortalHost -eq "" -or $PortalHost -eq "localhost") {
    Write-Warn "PORTAL_HOST is unset or still 'localhost' -- fine for now, but Google sign-in from any device but this one needs a real hostname. Revisit once IT gives you one."
} else {
    Write-Ok "PORTAL_HOST=$PortalHost"
}

# ── 7. Database, static files, real data ────────────────────────────────────

Write-Step "Preparing the database"

& $VenvPython manage.py migrate --noinput
Write-Ok "migrations applied"

& $VenvPython manage.py collectstatic --noinput | Out-Null
Write-Ok "static files collected"

& $VenvPython manage.py seed_real_data
Write-Ok "committees + team roster seeded (safe to re-run -- never resets points/strikes)"

$hasSuperuser = & $VenvPython manage.py shell -c "from django.contrib.auth import get_user_model; print(get_user_model().objects.filter(is_superuser=True).exists())"
if ($hasSuperuser.Trim() -ne "True") {
    Write-Warn "No superuser exists yet -- you'll want a break-glass admin account for when Google sign-in or the internet is down."
    Write-Warn "  $VenvPython manage.py createsuperuser"
    Write-Warn "Give it an email nobody's real Google account uses (e.g. breakglass-admin@mcc-portal.local)"
    Write-Warn "-- using a real person's institute address here breaks their actual Google sign-in later."
} else {
    Write-Ok "a superuser already exists"
}

# ── 8. Windows service — the app itself (Waitress via NSSM) ────────────────

Write-Step "Installing the app as a Windows service"

$WaitressExe = Join-Path $VenvDir "Scripts\waitress-serve.exe"
$existingService = Get-Service -Name "MCCPortal" -ErrorAction SilentlyContinue
if ($existingService) {
    & $NssmExe stop MCCPortal | Out-Null
    & $NssmExe remove MCCPortal confirm | Out-Null
}
& $NssmExe install MCCPortal $WaitressExe "--host=127.0.0.1" "--port=$GunicornPort" "mccportal.wsgi:application"
& $NssmExe set MCCPortal AppDirectory $AppDir
& $NssmExe set MCCPortal Start SERVICE_AUTO_START
& $NssmExe set MCCPortal AppStdout (Join-Path $AppDir "data\service-stdout.log")
& $NssmExe set MCCPortal AppStderr (Join-Path $AppDir "data\service-stderr.log")
& $NssmExe start MCCPortal
Write-Ok "MCCPortal service installed and started (Waitress on 127.0.0.1:$GunicornPort)"

# ── 9. Caddy — plain reverse proxy for now, also as an NSSM service ────────

Write-Step "Configuring Caddy"

$CaddyfilePath = Join-Path $ToolsDir "Caddyfile"
if ($PortalHost -eq "" -or $PortalHost -eq "localhost") {
@"
# Placeholder -- no real hostname configured yet (see .env's PORTAL_HOST).
# Serves plain HTTP on the LAN so you can at least reach the app today.
# Once IT gives you a subdomain, see deploy/README.md for the real,
# HTTPS/DNS-01 config to replace this with.
:80 {
	reverse_proxy 127.0.0.1:$GunicornPort
}
"@ | Set-Content -Path $CaddyfilePath -Encoding utf8
    Write-Warn "Caddy set up for plain HTTP on port 80 (no domain yet) -- this is temporary."
} else {
@"
# NOTE: this still serves plain HTTP. Real HTTPS via Let's Encrypt DNS-01
# needs a Caddy build with your DNS provider's plugin -- see
# deploy/README.md "Once you know your DNS provider". Once you've built
# that binary, replace this whole file with the DNS-01 version shown there.
${PortalHost}:80 {
	reverse_proxy 127.0.0.1:$GunicornPort
}
"@ | Set-Content -Path $CaddyfilePath -Encoding utf8
}

$existingCaddy = Get-Service -Name "Caddy" -ErrorAction SilentlyContinue
if ($existingCaddy) {
    & $NssmExe stop Caddy | Out-Null
    & $NssmExe remove Caddy confirm | Out-Null
}
& $NssmExe install Caddy $CaddyExe "run" "--config" $CaddyfilePath
& $NssmExe set Caddy AppDirectory $ToolsDir
& $NssmExe set Caddy Start SERVICE_AUTO_START
& $NssmExe start Caddy
Write-Ok "Caddy configured and started as a service"

# ── 10. Scheduled tasks — hourly deadline sweep + nightly backup ───────────

Write-Step "Installing the scheduled jobs"

$deadlineAction = "$VenvPython manage.py deadline_check"
schtasks /Create /TN "MCC Portal - Deadline Check" /SC HOURLY /RU SYSTEM /RL HIGHEST /TR "cmd /c cd /d `"$AppDir`" && $deadlineAction" /F | Out-Null
Write-Ok "hourly deadline-check task registered"

$backupAction = "$VenvPython manage.py backup_db"
schtasks /Create /TN "MCC Portal - Nightly Backup" /SC DAILY /ST 02:30 /RU SYSTEM /RL HIGHEST /TR "cmd /c cd /d `"$AppDir`" && $backupAction" /F | Out-Null
Write-Ok "nightly backup task registered (02:30)"

# ── 11. Firewall ─────────────────────────────────────────────────────────────

Write-Step "Configuring the firewall"
# No `2>&1` here either -- same reasoning as the Python check above. netsh's
# own exit code is reliable enough on its own; stdout is suppressed, stderr
# is left alone so it can't be promoted into a terminating error.
foreach ($rule in @(
    @{ Name = "MCC Portal HTTP"; Port = 80 },
    @{ Name = "MCC Portal HTTPS"; Port = 443 }
)) {
    netsh advfirewall firewall show rule name="$($rule.Name)" >$null
    if ($LASTEXITCODE -ne 0) {
        netsh advfirewall firewall add rule name="$($rule.Name)" dir=in action=allow protocol=TCP localport=$($rule.Port) | Out-Null
    }
}
Write-Ok "firewall rules for 80/443 in place (Waitress itself stays bound to loopback, unreachable directly)"

# ── 12. Never sleep ──────────────────────────────────────────────────────────

Write-Step "Disabling sleep/hibernate (a sleeping server is an outage)"
powercfg /change standby-timeout-ac 0 | Out-Null
powercfg /change hibernate-timeout-ac 0 | Out-Null
powercfg /hibernate off
Write-Ok "sleep/hibernate disabled"

# ── Done ─────────────────────────────────────────────────────────────────────

Write-Step "Done"
Write-Host "  MCCPortal service    -- check with: Get-Service MCCPortal"
Write-Host "  Caddy service        -- check with: Get-Service Caddy"
Write-Host "  Scheduled tasks      -- check with: Get-ScheduledTask -TaskName 'MCC Portal*'"
Write-Host "  App logs:               Get-Content data\service-std*.log -Tail 50 -Wait"
Write-Host ""
if ($PortalHost -eq "" -or $PortalHost -eq "localhost") {
    Write-Host "  Reachable now at: http://<this-machine's-LAN-IP>/ (plain HTTP, no real domain yet)"
} else {
    Write-Host "  Reachable now at: http://$PortalHost/ (plain HTTP -- see deploy/README.md for HTTPS)"
}
Write-Host ""
Write-Host "  Still to do:"
Write-Host "  1. Get IT to point a subdomain at this machine's LAN IP and tell you their DNS provider."
Write-Host "  2. Follow deploy/README.md's DNS-01 section to get real HTTPS + register the Google"
Write-Host "     OAuth redirect URI against it."
Write-Host "  3. If you skipped it above, create a break-glass superuser:"
Write-Host "       $VenvPython manage.py createsuperuser"
Write-Host "  4. Restore-test a backup before you trust it: see deploy/README.md."

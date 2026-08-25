"""
Django settings for the MCC Portal.

This replaces the Express server's `server/src/config.ts` + `.env` pair. The
portal runs on a single PC in the computer lab, reachable over the college WiFi
at a real hostname with a real certificate (see HANDOVER.md for the deployment
walkthrough) — so unlike the old Vercel+Render split, frontend and API share one
origin. That single fact retires the whole Bearer-token workaround the old stack
needed for Safari/iOS: a normal SameSite=Lax session cookie now works
everywhere.

Configuration comes from environment variables, optionally seeded from a `.env`
file next to manage.py (see `.env.example`). Nothing secret is committed.
"""

from pathlib import Path
import os
import secrets

BASE_DIR = Path(__file__).resolve().parent.parent

# Holds the SQLite database, the log, the generated secret key and the backups.
# Gitignored, so it won't exist on a fresh clone — create it before anything
# below tries to write into it.
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)


# ── .env loading ─────────────────────────────────────────────────────────────
# A deliberately tiny loader instead of a dependency: `KEY=value` per line, `#`
# comments, blank lines ignored. Real environment variables always win, so a
# Windows service definition can override the file.

def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(BASE_DIR / ".env")


def env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def env_bool(key: str, default: bool = False) -> bool:
    return env(key, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_list(key: str, default: str = "") -> list[str]:
    return [item.strip() for item in env(key, default).split(",") if item.strip()]


# ── Core ─────────────────────────────────────────────────────────────────────

DEBUG = env_bool("DJANGO_DEBUG", False)


def _secret_key() -> str:
    """
    SECRET_KEY from the environment, else a persisted random one.

    On the lab PC nobody wants a manual key-generation step that, if skipped,
    silently invalidates every session on restart. So we generate once into
    data/secret_key.txt (gitignored, alongside the database) and reuse it.
    Setting DJANGO_SECRET_KEY in the environment overrides this entirely.
    """
    from_env = env("DJANGO_SECRET_KEY")
    if from_env:
        return from_env
    key_file = BASE_DIR / "data" / "secret_key.txt"
    if key_file.is_file():
        return key_file.read_text(encoding="utf-8").strip()
    key_file.parent.mkdir(parents=True, exist_ok=True)
    generated = secrets.token_urlsafe(64)
    key_file.write_text(generated, encoding="utf-8")
    return generated


SECRET_KEY = _secret_key()

# The public hostname of the portal, e.g. mcc.iimsirmaur.ac.in. Localhost entries
# are for development; production sets PORTAL_HOST.
PORTAL_HOST = env("PORTAL_HOST", "localhost")
ALLOWED_HOSTS = [PORTAL_HOST, "localhost", "127.0.0.1"]
if DEBUG:
    ALLOWED_HOSTS.append("testserver")
# Lets other devices on the same WiFi reach a `runserver` bound to 0.0.0.0 during
# local testing — e.g. EXTRA_ALLOWED_HOSTS=10.10.65.63. Google sign-in will still
# refuse a bare-IP redirect URI (see README.md); this only helps pages that don't
# need it, or a tunnel/hostname you've separately registered with Google.
ALLOWED_HOSTS += env_list("EXTRA_ALLOWED_HOSTS")

# Behind Caddy, Django must be told the original request was HTTPS, or it will
# build http:// callback URLs and reject secure-cookie writes.
CSRF_TRUSTED_ORIGINS = [f"https://{PORTAL_HOST}"]
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True


INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",
    # Google sign-in.
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.google",
    # The portal itself.
    "core",
    "engine",
    "accounts",
    "ui",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    # Resolves the caller's portal roles once per request into request.roles —
    # the equivalent of the old Express `attachRoles`.
    "core.middleware.PortalRolesMiddleware",
]

ROOT_URLCONF = "mccportal.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "ui.context_processors.portal_nav",
            ],
        },
    },
]

WSGI_APPLICATION = "mccportal.wsgi.application"


# ── Database ─────────────────────────────────────────────────────────────────
# SQLite, one file, on the lab PC. The OPTIONS are not optional decoration:
#
#   journal_mode=WAL     readers don't block the writer (and vice versa), which
#                        is what makes SQLite viable for a multi-user portal
#   synchronous=NORMAL   safe under WAL, far fewer fsyncs
#   foreign_keys=ON      SQLite ignores FK constraints unless asked
#   transaction_mode     "IMMEDIATE" takes the write lock at BEGIN rather than
#                        mid-transaction, which is what prevents the classic
#                        "database is locked" error under concurrent submissions
#
# init_command and transaction_mode both require Django 5.1+.

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "data" / "mcc.sqlite3",
        "OPTIONS": {
            "timeout": 20,
            "init_command": (
                "PRAGMA journal_mode=WAL;"
                "PRAGMA synchronous=NORMAL;"
                "PRAGMA foreign_keys=ON;"
            ),
            "transaction_mode": "IMMEDIATE",
        },
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# ── Authentication ───────────────────────────────────────────────────────────

SITE_ID = 1

AUTHENTICATION_BACKENDS = [
    # Kept so the one break-glass superuser can reach /admin/ when Google or the
    # internet is unavailable. See HANDOVER.md.
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

LOGIN_URL = "/accounts/google/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/"

# Google is the only way into the portal itself; there are no portal passwords.
SOCIALACCOUNT_ONLY = True
SOCIALACCOUNT_AUTO_SIGNUP = True
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*"]
# Google has already verified the address; a second confirmation round-trip would
# only fail on a network without outbound mail.
ACCOUNT_EMAIL_VERIFICATION = "none"
ACCOUNT_ADAPTER = "accounts.adapters.PortalAccountAdapter"
SOCIALACCOUNT_ADAPTER = "accounts.adapters.PortalSocialAccountAdapter"
# Skip allauth's "continue with Google?" interstitial — the button already said so.
SOCIALACCOUNT_LOGIN_ON_GET = True

SOCIALACCOUNT_PROVIDERS = {
    "google": {
        "APP": {
            "client_id": env("GOOGLE_CLIENT_ID"),
            "secret": env("GOOGLE_CLIENT_SECRET"),
            "key": "",
        },
        "SCOPE": ["profile", "email"],
        "AUTH_PARAMS": {
            "access_type": "online",
            # Pre-filters Google's account chooser to the institute domain. This
            # is a convenience only — Google does not guarantee it, which is why
            # PortalSocialAccountAdapter enforces the domain server-side.
            "hd": env("ALLOWED_DOMAINS", "iimsirmaur.ac.in").split(",")[0].strip(),
        },
    }
}

# Fallback domain gate, used only until PortalSettings exists in the database
# (i.e. before the first `seed_real_data` run).
ALLOWED_EMAIL_DOMAINS = env_list("ALLOWED_DOMAINS", "iimsirmaur.ac.in")

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# ── Sessions & security ──────────────────────────────────────────────────────
# Same-origin now, so plain Lax cookies are correct and sufficient.

SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_AGE = 14 * 24 * 60 * 60  # 14 days, matching the old JWT TTL
SESSION_SAVE_EVERY_REQUEST = True  # rolling expiry, as express-session did

# Only enforce HTTPS-only cookies when actually served over HTTPS, or local
# development over http://localhost can never log in.
_SECURE = env_bool("PORTAL_HTTPS", not DEBUG)
SESSION_COOKIE_SECURE = _SECURE
CSRF_COOKIE_SECURE = _SECURE
SECURE_SSL_REDIRECT = False  # Caddy already redirects http→https
SECURE_HSTS_SECONDS = 31536000 if _SECURE else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = False  # we own one subdomain, not the apex
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"


# ── Internationalisation ─────────────────────────────────────────────────────

LANGUAGE_CODE = "en-in"
TIME_ZONE = "Asia/Kolkata"  # IIM Sirmaur; post slots are IST wall-clock times
USE_I18N = True
USE_TZ = True


# ── Static files ─────────────────────────────────────────────────────────────

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    # Compressed, but NOT the Manifest variant: the manifest one renames every
    # file to name.<hash>.ext and requires `collectstatic` to have run first,
    # which would make `{% static %}` raise in dev and in tests (there is no
    # build step here — matching the old SPA's "no build step" ethos, per
    # docs/PRD.md §7). Plain compression still lets WhiteNoise gzip files it
    # serves without that prerequisite.
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}


# ── Email ────────────────────────────────────────────────────────────────────
# Mirrors the old services/email.ts behaviour: a real sender when configured, a
# harmless log otherwise — so dev and production run identical code paths.

if env("EMAIL_HOST"):
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = env("EMAIL_HOST")
    EMAIL_PORT = int(env("EMAIL_PORT", "587"))
    EMAIL_HOST_USER = env("EMAIL_HOST_USER")
    EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD")
    EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
else:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", "MCC Portal <mediacell@iimsirmaur.ac.in>")
EMAIL_SUBJECT_PREFIX = ""
EMAIL_TIMEOUT = 10  # never let a stalled relay hang a request


# ── Calendar (optional) ──────────────────────────────────────────────────────
# Path to, or inline contents of, a Google service-account JSON with domain-wide
# delegation. Unset → services/calendar.py uses its logging stub and every
# calendar call becomes a no-op. See docs/PIC.md §3.

CALENDAR_SERVICE_ACCOUNT_JSON = env("CALENDAR_SERVICE_ACCOUNT_JSON")


# ── Backups ──────────────────────────────────────────────────────────────────

BACKUP_DIR = Path(env("BACKUP_DIR", str(BASE_DIR / "data" / "backups")))
BACKUP_KEEP_DAILY = int(env("BACKUP_KEEP_DAILY", "14"))
BACKUP_KEEP_WEEKLY = int(env("BACKUP_KEEP_WEEKLY", "8"))


# ── Logging ──────────────────────────────────────────────────────────────────

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "portal": {"format": "[{asctime}] {levelname} {name}: {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "portal"},
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": BASE_DIR / "data" / "portal.log",
            "maxBytes": 5 * 1024 * 1024,
            "backupCount": 5,
            "formatter": "portal",
            "encoding": "utf-8",
        },
    },
    "root": {"handlers": ["console", "file"], "level": "INFO"},
    "loggers": {
        "django.db.backends": {"level": "WARNING"},
    },
}

MESSAGE_STORAGE = "django.contrib.messages.storage.session.SessionStorage"

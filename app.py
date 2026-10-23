# Local Visual Asset Library
# Copyright (c) 2026 Mahmud (https://github.com/DeveloperMahmud)
# All rights reserved.
#
import os, sqlite3, hashlib, json, threading, warnings, time, sys, subprocess, shutil, gc, secrets, mimetypes
from pathlib import Path
from flask import Flask, request, jsonify, render_template, send_file, abort, session, redirect, url_for, after_this_request, Response
import socket
import uuid
import re
from urllib.parse import quote

APP_DIR = Path(__file__).resolve().parent
DB = APP_DIR / "assets.db"
THUMBS = APP_DIR / "thumbnails"
MEDIA_CACHE = APP_DIR / "media_cache"
CONFIG = APP_DIR / "config.json"
SECRET_FILE = APP_DIR / "secret.key"
DEFAULT_ROOT = str(Path.home() / "Documents" / "Home Cloud Library")
DEFAULT_SHARE_ROOT = str(Path.home() / "Documents" / "Home Cloud Shares")
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".avif"}
VIDEO_EXTS = ['.3gp', '.avi', '.m4v', '.mkv', '.mov', '.mp4', '.mpeg', '.mpg', '.ogv', '.webm']
AUDIO_EXTS = ['.aac', '.flac', '.m4a', '.mp3', '.ogg', '.opus', '.wav', '.wma']
MEDIA_EXTS = IMAGE_EXTS | set(VIDEO_EXTS) | set(AUDIO_EXTS)
PREVIEW_MIME_TYPES = {
    ".3gp": "video/3gpp",
    ".avi": "video/x-msvideo",
    ".m4v": "video/x-m4v",
    ".mkv": "video/x-matroska",
    ".mov": "video/quicktime",
    ".mp4": "video/mp4",
    ".mpeg": "video/mpeg",
    ".mpg": "video/mpeg",
    ".ogv": "video/ogg",
    ".webm": "video/webm",
    ".aac": "audio/aac",
    ".flac": "audio/flac",
    ".m4a": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
    ".wav": "audio/wav",
    ".wma": "audio/x-ms-wma",
}
RECYCLE_NAME = ".local_asset_library_recycle_bin"
MAX_PIXELS = 50_000_000
AI_MODEL_NAME = "ViT-B-16"
AI_PRETRAINED = "laion400m_e32"
AI_BATCH_SIZE = 8
# Phase 5 performance safeguards
LEXICAL_CANDIDATE_LIMIT = 1000
AI_SEARCH_CANDIDATE_LIMIT = 10000
MAX_HEAVY_JOBS = 1
PERF_BATCH_PAUSE = 0.01
APP_VERSION = "11.0.0-beta.3"
SERVER_STARTED_AT = time.time()
LOGIN_RATE_WINDOW = 300
LOGIN_RATE_MAX_FAILURES = 8
LOGIN_RATE_LOCK_SECONDS = 300
_login_failures = {}
_chat_rate = {}
_chat_rate_lock = threading.Lock()
_server_events = []
_server_events_lock = threading.Lock()
_media_jobs = {}
_media_jobs_lock = threading.Lock()

PERMANENT_DELETE_PHRASE = "DELETE PERMANENTLY"
EMPTY_RECYCLE_PHRASE = "EMPTY RECYCLE BIN"
ACTIVE_USER_WINDOW = 300
GEO_CACHE_TTL = 3600
_mac_cache = {}
_geo_cache = {}

def load_app_secret():
    env_secret = os.environ.get("VAL_SECRET", "").strip()
    if len(env_secret) >= 32:
        return env_secret
    try:
        saved = SECRET_FILE.read_text(encoding="utf-8").strip()
        if len(saved) >= 32:
            return saved
    except OSError:
        pass
    generated = secrets.token_urlsafe(48)
    try:
        SECRET_FILE.write_text(generated, encoding="utf-8")
        return generated
    except OSError as exc:
        raise RuntimeError("Set VAL_SECRET or allow Home Cloud to create secret.key in the application folder.") from exc


app = Flask(__name__)
app.secret_key = load_app_secret()
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
THUMBS.mkdir(exist_ok=True)


def server_event(message, level="INFO"):
    try:
        item={"time":time.time(),"level":str(level).upper()[:10],"message":str(message)[:1200]}
        with _server_events_lock:
            _server_events.append(item)
            del _server_events[:-500]
    except Exception:
        pass


class _HomeCloudLogHandler(__import__("logging").Handler):
    def emit(self, record):
        try:
            server_event(record.getMessage(), record.levelname)
        except Exception:
            pass


try:
    app.logger.addHandler(_HomeCloudLogHandler())
    server_event("Home Cloud application logger initialized.")
except Exception:
    pass


def detect_lan_ip():
    """Find a LAN address using local interface/hostname data only.

    No public DNS server or external endpoint is contacted. This keeps startup
    deterministic when the host has no internet connection.
    """
    try:
        import ipaddress
        candidates = []
        for result in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET, socket.SOCK_DGRAM):
            address = result[4][0]
            if address not in candidates:
                candidates.append(address)
        usable = []
        for address in candidates:
            parsed = ipaddress.ip_address(address)
            if parsed.is_loopback or parsed.is_link_local or parsed.is_unspecified:
                continue
            usable.append(address)
        private = [address for address in usable if ipaddress.ip_address(address).is_private]
        return (private or usable or [""])[0]
    except OSError:
        return ""


def guest_base_url():
    """Return a guest URL base that another LAN device can actually reach.

    If the admin opened the app via a LAN hostname/IP, preserve that host.
    If the admin opened via localhost/127.0.0.1, detect the Windows PC's
    primary LAN address and use it instead.
    """
    host = request.host
    hostname = request.host.split(":", 1)[0].strip().lower()
    loopback_hosts = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}
    if hostname and hostname not in loopback_hosts:
        return request.host_url.rstrip("/")

    detected_ip = detect_lan_ip()

    if detected_ip and detected_ip not in ("127.0.0.1", "0.0.0.0"):
        port = request.environ.get("SERVER_PORT", "8080")
        return f"{request.scheme}://{detected_ip}:{port}"

    return request.host_url.rstrip("/")

scan_lock = threading.Lock()
ai_lock = threading.Lock()
thumb_lock = threading.Lock()
ocr_lock = threading.Lock()
ai_enrich_lock = threading.Lock()
job_manager_lock = threading.Lock()
job_pause_event = threading.Event()
job_stop_event = threading.Event()
job_manager = {"active": "", "started_at": 0, "queued": []}
ai_enrich_state = {"running": False, "processed": 0, "total": 0, "tagged": 0, "failed": 0, "current": "", "message": "Idle"}
ocr_state = {"running": False, "paused": False, "stopped": False, "processed": 0, "total": 0, "ocr_found": 0, "failed": 0, "current": "", "message": "Idle", "started_at": 0, "finished_at": 0, "duration": 0}
thumb_state = {"running": False, "paused": False, "stopped": False, "mode": "", "processed": 0, "total": 0, "created": 0, "skipped": 0, "failed": 0, "current": "", "message": "Idle", "started_at": 0, "finished_at": 0, "duration": 0}
scan_state = {"running": False, "paused": False, "stopped": False, "processed": 0, "total": 0, "indexed": 0, "skipped": 0, "new": 0, "changed": 0, "unchanged": 0, "removed": 0, "current": "", "message": "Idle", "started_at": 0, "finished_at": 0, "duration": 0}
ai_state = {"running": False, "paused": False, "stopped": False, "processed": 0, "total": 0, "embedded": 0, "skipped": 0, "current": "", "message": "Idle", "started_at": 0, "finished_at": 0, "duration": 0}

_ai = {"model": None, "preprocess": None, "device": None}


def load_config():
    if CONFIG.exists():
        try:
            # Windows PowerShell 5 writes UTF-8 JSON with a BOM by default.
            return json.loads(CONFIG.read_text(encoding="utf-8-sig"))
        except Exception:
            pass
    cfg = {"asset_root": DEFAULT_ROOT}
    CONFIG.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return cfg


CFG = load_config()
ROOT = Path(CFG["asset_root"]).expanduser()

CONFIG_DEFAULTS = {
    "app_name": "Home Cloud",
    "port": 8080,
    "ai_mode": "full",
    "chat_enabled": True,
    "chat_allow_anonymous": True,
    "chat_allow_guests": True,
    "chat_retention_days": 30,
    "temp_share_root": DEFAULT_SHARE_ROOT,
    "thumbnail_quality": 85,
    "update_repo": "DeveloperMahmud/home-cloud-v11",
    "update_channel": "beta",
    "update_auto_check": True,
}
for _key, _value in CONFIG_DEFAULTS.items():
    CFG.setdefault(_key, _value)


def config_value(key):
    return CFG.get(key, CONFIG_DEFAULTS.get(key))


def save_config_values(values):
    global CFG
    CFG.update(values)
    CONFIG.write_text(json.dumps(CFG, indent=2), encoding="utf-8")

def recycle_root():
    p = (ROOT / RECYCLE_NAME).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p

def is_in_recycle(path):
    try:
        Path(path).resolve().relative_to(recycle_root())
        return True
    except (ValueError, OSError):
        return False

def set_asset_root(path_str):
    global ROOT, CFG
    p = Path(path_str).expanduser().resolve()
    if not p.exists() or not p.is_dir():
        raise ValueError("Selected folder does not exist or is not a directory.")
    ROOT = p
    CFG["asset_root"] = str(p)
    CONFIG.write_text(json.dumps(CFG, indent=2), encoding="utf-8")

def choose_folder_native(initial=None):
    # Open a native Windows folder picker in a child process so Flask's
    # worker thread does not have to own a Tkinter event loop.
    code = '''import sys, tkinter as tk
from tkinter import filedialog
root=tk.Tk()
root.withdraw()
root.attributes("-topmost", True)
path=filedialog.askdirectory(title="Choose Asset Library Folder", initialdir=sys.argv[1] if len(sys.argv)>1 and sys.argv[1] else None, mustexist=True)
root.destroy()
print(path or "")'''
    kwargs = {"capture_output": True, "text": True, "timeout": 300}
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        kwargs["startupinfo"] = startupinfo
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    result = subprocess.run([sys.executable, "-c", code, str(initial or ROOT)], **kwargs)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Folder picker could not be opened.")
    return result.stdout.strip()


def db():
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=30000")
    c.execute("PRAGMA synchronous=NORMAL")
    c.execute("PRAGMA temp_store=MEMORY")
    c.execute("PRAGMA cache_size=-32768")
    return c


def init_db():
    c = db()
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    c.execute("""CREATE TABLE IF NOT EXISTS assets(
        id INTEGER PRIMARY KEY,
        path TEXT UNIQUE,
        name TEXT,
        ext TEXT,
        size INTEGER,
        mtime REAL,
        thumb TEXT,
        width INTEGER NOT NULL DEFAULT 0,
        height INTEGER NOT NULL DEFAULT 0,
        orientation TEXT NOT NULL DEFAULT '',
        dominant_color TEXT NOT NULL DEFAULT '',
        ocr_text TEXT NOT NULL DEFAULT '',
        ocr_attempted INTEGER NOT NULL DEFAULT 0)""")
    c.execute("""CREATE TABLE IF NOT EXISTS embeddings(
        asset_id INTEGER PRIMARY KEY,
        vector BLOB NOT NULL,
        dims INTEGER NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS embedding_meta(
        key TEXT PRIMARY KEY,
        value TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS job_runs(
        id INTEGER PRIMARY KEY,
        job_type TEXT NOT NULL,
        started_at REAL NOT NULL,
        finished_at REAL NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'running',
        duration REAL NOT NULL DEFAULT 0,
        summary_json TEXT NOT NULL DEFAULT '{}',
        message TEXT NOT NULL DEFAULT ''
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_job_runs_type_finished ON job_runs(job_type, finished_at DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_assets_name ON assets(name COLLATE NOCASE)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_assets_ext ON assets(ext)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_assets_mtime ON assets(mtime DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_assets_size ON assets(size DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_assets_path ON assets(path COLLATE NOCASE)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_assets_ocr_attempted ON assets(ocr_attempted)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_embeddings_asset ON embeddings(asset_id)")
    c.execute("""CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'client',
        permissions TEXT NOT NULL DEFAULT '{}',
        active INTEGER NOT NULL DEFAULT 1,
        created_at REAL NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS guest_tokens(
        id INTEGER PRIMARY KEY,
        token TEXT UNIQUE NOT NULL,
        permissions TEXT NOT NULL DEFAULT '{}',
        expires_at REAL NOT NULL,
        active INTEGER NOT NULL DEFAULT 1,
        created_at REAL NOT NULL)""")
    # Phase 2 metadata migrations. Existing databases are preserved.
    for col, definition in (
        ("width", "INTEGER NOT NULL DEFAULT 0"),
        ("height", "INTEGER NOT NULL DEFAULT 0"),
        ("orientation", "TEXT NOT NULL DEFAULT ''"),
        ("dominant_color", "TEXT NOT NULL DEFAULT ''"),
        ("ocr_text", "TEXT NOT NULL DEFAULT ''"),
        ("ocr_attempted", "INTEGER NOT NULL DEFAULT 0"),
        ("ai_tags", "TEXT NOT NULL DEFAULT ''"),
        ("ai_caption", "TEXT NOT NULL DEFAULT ''"),
        ("ai_tag_attempted", "INTEGER NOT NULL DEFAULT 0"),
    ):
        try:
            c.execute(f"ALTER TABLE assets ADD COLUMN {col} {definition}")
        except sqlite3.OperationalError:
            pass

    # Columns added by migrations must exist before their indexes are created.
    # Keeping this after the ALTER loop also makes a brand-new database bootable.
    c.execute("CREATE INDEX IF NOT EXISTS idx_assets_ai_tag_attempted ON assets(ai_tag_attempted)")

    try:
        c.execute("ALTER TABLE users ADD COLUMN allowed_dir TEXT NOT NULL DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    for column, definition in (
        ("last_login", "REAL NOT NULL DEFAULT 0"),
        ("storage_limit_mb", "INTEGER NOT NULL DEFAULT 0"),
    ):
        try:
            c.execute(f"ALTER TABLE users ADD COLUMN {column} {definition}")
        except sqlite3.OperationalError:
            pass
    try:
        c.execute("ALTER TABLE guest_tokens ADD COLUMN allowed_dir TEXT NOT NULL DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    c.execute("""CREATE TABLE IF NOT EXISTS user_sessions(
        id INTEGER PRIMARY KEY,
        session_key TEXT UNIQUE NOT NULL,
        user_id INTEGER NOT NULL,
        first_seen REAL NOT NULL,
        last_seen REAL NOT NULL,
        ip TEXT NOT NULL DEFAULT '',
        mac TEXT NOT NULL DEFAULT '',
        user_agent TEXT NOT NULL DEFAULT '',
        geo_json TEXT NOT NULL DEFAULT '{}',
        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE)""")
    c.execute("""CREATE TABLE IF NOT EXISTS activity_log(
        id INTEGER PRIMARY KEY,
        timestamp REAL NOT NULL,
        user_id INTEGER,
        username TEXT NOT NULL DEFAULT '',
        role TEXT NOT NULL DEFAULT '',
        action TEXT NOT NULL,
        target TEXT NOT NULL DEFAULT '',
        details TEXT NOT NULL DEFAULT '',
        ip TEXT NOT NULL DEFAULT '',
        user_agent TEXT NOT NULL DEFAULT ''
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_activity_log_timestamp ON activity_log(timestamp DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_activity_log_action ON activity_log(action)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_activity_log_username ON activity_log(username)")
    c.execute("""CREATE TABLE IF NOT EXISTS revoked_sessions(
        session_key TEXT PRIMARY KEY,
        revoked_at REAL NOT NULL,
        revoked_by INTEGER
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS chat_messages(
        id INTEGER PRIMARY KEY,
        sender_type TEXT NOT NULL,
        sender_key TEXT NOT NULL,
        display_name TEXT NOT NULL,
        message TEXT NOT NULL,
        created_at REAL NOT NULL,
        deleted_at REAL NOT NULL DEFAULT 0
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_chat_messages_created ON chat_messages(created_at DESC)")
    c.execute("""CREATE TABLE IF NOT EXISTS chat_presence(
        session_key TEXT PRIMARY KEY,
        sender_type TEXT NOT NULL,
        display_name TEXT NOT NULL,
        last_seen REAL NOT NULL,
        ip TEXT NOT NULL DEFAULT ''
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_chat_presence_seen ON chat_presence(last_seen DESC)")
    c.execute("""CREATE TABLE IF NOT EXISTS temp_share_channels(
        id INTEGER PRIMARY KEY,
        token TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        mode TEXT NOT NULL DEFAULT 'upload',
        directory TEXT NOT NULL,
        expires_at REAL NOT NULL,
        max_file_bytes INTEGER NOT NULL DEFAULT 1073741824,
        max_total_bytes INTEGER NOT NULL DEFAULT 5368709120,
        max_devices INTEGER NOT NULL DEFAULT 10,
        active INTEGER NOT NULL DEFAULT 1,
        created_at REAL NOT NULL,
        created_by INTEGER,
        uploaded_bytes INTEGER NOT NULL DEFAULT 0,
        transfer_count INTEGER NOT NULL DEFAULT 0
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_temp_share_expiry ON temp_share_channels(active,expires_at DESC)")
    c.execute("""CREATE TABLE IF NOT EXISTS temp_share_devices(
        channel_id INTEGER NOT NULL,
        ip TEXT NOT NULL,
        first_seen REAL NOT NULL,
        last_seen REAL NOT NULL,
        PRIMARY KEY(channel_id,ip)
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS temp_share_events(
        id INTEGER PRIMARY KEY,
        channel_id INTEGER NOT NULL,
        event_type TEXT NOT NULL,
        filename TEXT NOT NULL DEFAULT '',
        size INTEGER NOT NULL DEFAULT 0,
        ip TEXT NOT NULL DEFAULT '',
        created_at REAL NOT NULL
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_temp_share_events ON temp_share_events(channel_id,created_at DESC)")
    # Home OTT / Media Hub. Only references indexed assets; original media is
    # never copied, renamed or modified by the catalogue.
    c.execute("""CREATE TABLE IF NOT EXISTS media_hub_items(
        asset_id INTEGER PRIMARY KEY,
        title TEXT NOT NULL DEFAULT '',
        description TEXT NOT NULL DEFAULT '',
        category TEXT NOT NULL DEFAULT 'Movies',
        media_kind TEXT NOT NULL DEFAULT 'movie',
        series_title TEXT NOT NULL DEFAULT '',
        season INTEGER NOT NULL DEFAULT 0,
        episode INTEGER NOT NULL DEFAULT 0,
        featured INTEGER NOT NULL DEFAULT 0,
        poster TEXT NOT NULL DEFAULT '',
        added_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        FOREIGN KEY(asset_id) REFERENCES assets(id) ON DELETE CASCADE)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_media_hub_added ON media_hub_items(added_at DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_media_hub_series ON media_hub_items(series_title,season,episode)")
    c.execute("""CREATE TABLE IF NOT EXISTS media_hub_progress(
        viewer_key TEXT NOT NULL,
        asset_id INTEGER NOT NULL,
        position REAL NOT NULL DEFAULT 0,
        duration REAL NOT NULL DEFAULT 0,
        completed INTEGER NOT NULL DEFAULT 0,
        updated_at REAL NOT NULL,
        PRIMARY KEY(viewer_key,asset_id),
        FOREIGN KEY(asset_id) REFERENCES assets(id) ON DELETE CASCADE)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_media_hub_progress_viewer ON media_hub_progress(viewer_key,updated_at DESC)")
    c.execute("""CREATE TABLE IF NOT EXISTS media_hub_watchlist(
        viewer_key TEXT NOT NULL,
        asset_id INTEGER NOT NULL,
        added_at REAL NOT NULL,
        PRIMARY KEY(viewer_key,asset_id),
        FOREIGN KEY(asset_id) REFERENCES assets(id) ON DELETE CASCADE)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_assets_ext ON assets(ext)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_assets_mtime ON assets(mtime DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_assets_name ON assets(name COLLATE NOCASE)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_assets_size ON assets(size)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_embeddings_asset_id ON embeddings(asset_id)")
    # Permission migration: existing client users keep dashboard access unless it was explicitly configured later.
    try:
        rows = c.execute("SELECT id,role,permissions FROM users").fetchall()
        for row in rows:
            try:
                perms = json.loads(row["permissions"] or "{}")
            except Exception:
                perms = {}
            if row["role"] == "admin":
                perms.update(DEFAULT_ADMIN_PERMS)
            else:
                if "dashboard" not in perms:
                    perms["dashboard"] = True
                # Preserve the capabilities that the legacy Upload permission
                # implicitly granted before v10.1 split them into safe controls.
                if perms.get("upload"):
                    for key in ("create_folder", "rename", "copy", "move"):
                        perms.setdefault(key, True)
                perms.setdefault("overwrite", False)
            c.execute("UPDATE users SET permissions=? WHERE id=?", (json.dumps(perms), row["id"]))
    except Exception:
        pass
    # Guests never receive dashboard access. Remove the flag if it exists from older records.
    try:
        rows = c.execute("SELECT id,permissions FROM guest_tokens").fetchall()
        for row in rows:
            try:
                perms = json.loads(row["permissions"] or "{}")
            except Exception:
                perms = {}
            perms.pop("dashboard", None)
            if perms.get("upload"):
                for key in ("create_folder", "rename", "copy", "move"):
                    perms.setdefault(key, True)
            perms.setdefault("overwrite", False)
            c.execute("UPDATE guest_tokens SET permissions=? WHERE id=?", (json.dumps(perms), row["id"]))
    except Exception:
        pass
    c.commit()
    c.close()


DEFAULT_CLIENT_PERMS = {"view": True, "search": True, "ai_search": True, "preview": True, "download": False, "scan": False, "upload": False, "create_folder": False, "rename": False, "copy": False, "move": False, "overwrite": False, "delete": False, "dashboard": True, "manage_users": False, "manage_settings": False}
DEFAULT_ADMIN_PERMS = {k: True for k in ("view","search","ai_search","preview","download","scan","upload","create_folder","rename","copy","move","overwrite","delete","dashboard","manage_users","manage_settings")}
ALL_PERMISSIONS = tuple(DEFAULT_ADMIN_PERMS.keys())

def clean_permissions(value, default=None):
    if not isinstance(value, dict):
        return dict(default or {})
    return {k: bool(value.get(k, False)) for k in ALL_PERMISSIONS}


def normalize_scope(value):
    value = str(value or '').replace("\\", "/").strip().strip("/")
    if value in ("", "."):
        return ""
    parts = [p for p in value.split("/") if p not in ("", ".")]
    if any(p == ".." or any(ord(ch) < 32 for ch in p) for p in parts):
        raise ValueError("Invalid directory scope.")
    return "/".join(parts)

def current_scope():
    u = current_user()
    if u:
        if u["role"] == "admin":
            return ""
        try:
            return normalize_scope(u["allowed_dir"])
        except Exception:
            return ""
    gid = session.get("guest_id")
    if gid:
        try:
            c = db()
            g = c.execute("SELECT allowed_dir,active,expires_at FROM guest_tokens WHERE id=?", (gid,)).fetchone()
            c.close()
            if not g or not g["active"] or g["expires_at"] < time.time():
                return ""
            return normalize_scope(g["allowed_dir"])
        except Exception:
            return ""
    return ""


def scoped_storage_usage(scope):
    base=(ROOT/scope).resolve() if scope else ROOT.resolve()
    total=0
    try:
        for current,dirs,files in os.walk(base):
            dirs[:]=[name for name in dirs if name!=RECYCLE_NAME]
            for name in files:
                try: total+=(Path(current)/name).stat().st_size
                except OSError: pass
    except OSError:
        pass
    return total

def session_has_perm(name):
    u = current_user()
    if u:
        if u["role"] == "admin":
            return True
        try:
            return bool(json.loads(u["permissions"]).get(name, False))
        except Exception:
            return False
    gid = session.get("guest_id")
    if gid:
        try:
            c = db()
            g = c.execute("SELECT permissions,active,expires_at FROM guest_tokens WHERE id=?", (gid,)).fetchone()
            c.close()
            if not g or not g["active"] or g["expires_at"] < time.time():
                return False
            return bool(json.loads(g["permissions"]).get(name, False))
        except Exception:
            return False
    return False

def path_in_scope(relpath, scope=None, allow_scope_root=True):
    relpath = normalize_scope(relpath)
    scope = current_scope() if scope is None else normalize_scope(scope)
    if not scope:
        return True
    if relpath == scope:
        return allow_scope_root
    return relpath.startswith(scope + "/")

def scope_sql(scope):
    scope = normalize_scope(scope)
    if not scope:
        return "", ()
    return " AND (path=? OR path LIKE ?)", (scope, scope + "/%")

def resolve_mac_address(ip):
    if not ip or ip in {"127.0.0.1", "::1", "0.0.0.0"}:
        return "Local host"
    now = time.time()
    cached = _mac_cache.get(ip)
    if cached and now - cached[0] < 300:
        return cached[1]
    mac = "Unavailable"
    try:
        if os.name == "nt":
            out = subprocess.run(["arp", "-a", ip], capture_output=True, text=True, timeout=3, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
            m = re.search(r"\b([0-9a-f]{2}(?:-[0-9a-f]{2}){5})\b", out, re.I)
            if m:
                mac = m.group(1).replace("-", ":").upper()
        else:
            out = subprocess.run(["ip", "neigh", "show", ip], capture_output=True, text=True, timeout=3).stdout
            m = re.search(r"lladdr\s+([0-9a-f:]{17})", out, re.I)
            if m:
                mac = m.group(1).upper()
    except Exception:
        pass
    _mac_cache[ip] = (now, mac)
    return mac

def geo_for_ip(ip):
    """Return local-only address classification; never call an internet API."""
    if not ip:
        return {}
    try:
        import ipaddress
        parsed = ipaddress.ip_address(ip)
        if parsed.is_private or parsed.is_loopback or parsed.is_link_local:
            return {"status":"private","message":"Private/LAN IP; public geographic location is not available."}
    except Exception:
        return {"status":"unknown","message":"Invalid IP address."}
    data = {"status":"offline","message":"Public IP geolocation is disabled. Home Cloud makes no runtime internet requests."}
    _geo_cache[ip] = (time.time(), data)
    return data

def client_ip():
    """Return the actual client IP when available, including reverse-proxy setups."""
    # Prefer the first forwarded client address when the app is behind a
    # reverse proxy/tunnel. Flask's remote_addr is otherwise the proxy/server.
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        first = forwarded.split(",", 1)[0].strip()
        if first:
            return first
    real = request.headers.get("X-Real-IP", "").strip()
    if real:
        return real
    return request.remote_addr or ""


def is_lan_request():
    """Chat is deliberately limited to the host and private LAN clients."""
    try:
        import ipaddress
        raw = (request.remote_addr or "").split("%", 1)[0]
        address = ipaddress.ip_address(raw)
        if getattr(address, "ipv4_mapped", None):
            address = address.ipv4_mapped
        return address.is_private or address.is_loopback or address.is_link_local
    except ValueError:
        return False


def chat_identity():
    user = current_user()
    if user:
        return {"type": user["role"], "key": f"user:{user['id']}", "name": user["username"], "admin": user["role"] == "admin"}
    guest_id = session.get("guest_id")
    if guest_id:
        c = db()
        guest = c.execute("SELECT id,active,expires_at FROM guest_tokens WHERE id=?", (guest_id,)).fetchone()
        c.close()
        if guest and guest["active"] and guest["expires_at"] >= time.time():
            return {"type": "guest", "key": f"guest:{guest_id}", "name": "Guest", "admin": False}
    anon_name = str(session.get("chat_name") or "").strip()
    anon_key = str(session.get("chat_session_id") or "").strip()
    if anon_name and anon_key:
        return {"type": "anonymous", "key": f"anon:{anon_key}", "name": anon_name, "admin": False}
    return None


_CHAT_ADJECTIVES = ("Amber", "Blue", "Calm", "Coral", "Cosmic", "Green", "Happy", "Ivory", "Lucky", "Silver", "Sunny", "Velvet")
_CHAT_ANIMALS = ("Badger", "Falcon", "Fox", "Koala", "Otter", "Owl", "Panda", "Robin", "Tiger", "Turtle", "Wolf", "Yak")
_CHAT_AVATARS = ("🦊", "🐼", "🦉", "🐯", "🐨", "🦦", "🐢", "🐺", "🦅", "🐦")


def ensure_anonymous_chat_identity():
    """Create a stable, friendly identity for this browser session."""
    identity = chat_identity()
    if identity:
        return identity
    session["chat_name"] = f"{secrets.choice(_CHAT_ADJECTIVES)} {secrets.choice(_CHAT_ANIMALS)} {secrets.randbelow(900) + 100}"
    session["chat_session_id"] = uuid.uuid4().hex
    session["chat_avatar"] = secrets.choice(_CHAT_AVATARS)
    return chat_identity()


def chat_avatar(identity):
    if identity["type"] == "admin":
        return "★"
    if identity["type"] == "guest":
        return "◇"
    if identity["type"] == "anonymous":
        digest = hashlib.sha256(str(identity["name"]).encode("utf-8", "ignore")).digest()
        return _CHAT_AVATARS[digest[0] % len(_CHAT_AVATARS)]
    return "●"


def chat_avatar_for_message(sender_type, display_name):
    if sender_type == "admin": return "★"
    if sender_type == "guest": return "◇"
    digest = hashlib.sha256(str(display_name).encode("utf-8", "ignore")).digest()
    return _CHAT_AVATARS[digest[0] % len(_CHAT_AVATARS)] if sender_type == "anonymous" else "●"


def temp_share_channel(token, allow_expired=False):
    c = db()
    row = c.execute("SELECT * FROM temp_share_channels WHERE token=?", (str(token or ""),)).fetchone()
    c.close()
    if not row or not row["active"]:
        return None
    if not allow_expired and float(row["expires_at"]) < time.time():
        return None
    return row


def temp_share_access(channel):
    """Register one LAN device and enforce the channel's device limit."""
    if not is_lan_request():
        return "This share is available only on the local network."
    ip = client_ip() or request.remote_addr or "unknown"
    now = time.time()
    c = db()
    known = c.execute("SELECT 1 FROM temp_share_devices WHERE channel_id=? AND ip=?", (channel["id"], ip)).fetchone()
    count = c.execute("SELECT COUNT(*) FROM temp_share_devices WHERE channel_id=?", (channel["id"],)).fetchone()[0]
    if not known and count >= int(channel["max_devices"]):
        c.close()
        return "This share has reached its device limit."
    c.execute("""INSERT INTO temp_share_devices(channel_id,ip,first_seen,last_seen) VALUES(?,?,?,?)
                 ON CONFLICT(channel_id,ip) DO UPDATE SET last_seen=excluded.last_seen""",
              (channel["id"], ip, now, now))
    c.commit(); c.close()
    return None


def temp_share_safe_path(channel, relative_path="", must_file=False):
    root = Path(channel["directory"]).expanduser().resolve()
    raw = str(relative_path or "").replace("\\", "/").strip("/")
    target = (root / raw).resolve() if raw else root
    try:
        target.relative_to(root)
    except ValueError:
        abort(403)
    if must_file and not target.is_file():
        abort(404)
    return target


def temp_share_public_data(channel, include_token=False):
    now = time.time()
    c = db()
    devices = c.execute("SELECT COUNT(*) FROM temp_share_devices WHERE channel_id=?", (channel["id"],)).fetchone()[0]
    events = c.execute("SELECT event_type,filename,size,ip,created_at FROM temp_share_events WHERE channel_id=? ORDER BY id DESC LIMIT 20", (channel["id"],)).fetchall()
    c.close()
    event_items = [dict(x) for x in events]
    if not include_token:
        for item in event_items: item.pop("ip", None)
    data = {"id": channel["id"], "name": channel["name"], "mode": channel["mode"],
            "expires_at": channel["expires_at"], "seconds_left": max(0, int(channel["expires_at"] - now)),
            "max_file_bytes": channel["max_file_bytes"], "max_total_bytes": channel["max_total_bytes"],
            "uploaded_bytes": channel["uploaded_bytes"], "transfer_count": channel["transfer_count"],
            "devices": devices, "max_devices": channel["max_devices"], "active": bool(channel["active"]),
            "events": event_items}
    if include_token:
        data["url"] = f"{guest_base_url()}/share/{channel['token']}"
        data["token"] = channel["token"]
        data["directory"] = channel["directory"]
        try:
            library_path = str(Path(channel["directory"]).resolve().relative_to(ROOT.resolve())).replace("\\", "/")
            data["in_library"] = True
            data["library_path"] = library_path
        except (ValueError, OSError):
            data["in_library"] = False
            data["library_path"] = ""
    return data


def temp_share_files(channel, download_prefix, limit=500):
    root = temp_share_safe_path(channel)
    files = []
    for base, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in names:
            if name.startswith(".") or name.endswith(".part"): continue
            path = Path(base, name)
            try:
                relative = str(path.relative_to(root)).replace("\\", "/")
                stat = path.stat()
                files.append({"name": name, "path": relative, "size": stat.st_size, "modified": stat.st_mtime,
                              "download_url": f"{download_prefix}/{quote(relative)}"})
            except OSError: pass
            if len(files) >= limit: break
        if len(files) >= limit: break
    files.sort(key=lambda item: item["modified"], reverse=True)
    return files


def touch_chat_presence(identity):
    session_key = identity["key"]
    c = db()
    c.execute("""INSERT INTO chat_presence(session_key,sender_type,display_name,last_seen,ip)
                 VALUES(?,?,?,?,?)
                 ON CONFLICT(session_key) DO UPDATE SET sender_type=excluded.sender_type,
                 display_name=excluded.display_name,last_seen=excluded.last_seen,ip=excluded.ip""",
              (session_key, identity["type"], identity["name"], time.time(), request.remote_addr or ""))
    c.execute("DELETE FROM chat_presence WHERE last_seen < ?", (time.time() - 120,))
    c.commit()
    c.close()


def chat_rate_allowed(key):
    now = time.time()
    with _chat_rate_lock:
        recent = [stamp for stamp in _chat_rate.get(key, []) if now - stamp < 10]
        if len(recent) >= 8:
            _chat_rate[key] = recent
            return False
        recent.append(now)
        _chat_rate[key] = recent
        return True

def track_user_activity():
    if request.path.startswith("/static/") or request.path.startswith("/thumb/"):
        return
    existing_sid=session.get("activity_sid")
    if existing_sid:
        try:
            c=db(); revoked=c.execute("SELECT 1 FROM revoked_sessions WHERE session_key=?",(existing_sid,)).fetchone(); c.close()
            if revoked:
                session.clear()
                return
        except Exception:
            pass
    u = current_user()
    if not u:
        return
    sid = session.get("activity_sid")
    if not sid:
        sid = uuid.uuid4().hex
        session["activity_sid"] = sid
        session.modified = True
    now = time.time()
    ip = client_ip()
    mac = resolve_mac_address(ip)
    geo = geo_for_ip(ip)
    ua = request.headers.get("User-Agent", "")[:1000]
    try:
        c = db()
        c.execute("""INSERT INTO user_sessions(session_key,user_id,first_seen,last_seen,ip,mac,user_agent,geo_json)
                     VALUES(?,?,?,?,?,?,?,?)
                     ON CONFLICT(session_key) DO UPDATE SET user_id=excluded.user_id,last_seen=excluded.last_seen,ip=excluded.ip,mac=excluded.mac,user_agent=excluded.user_agent,geo_json=excluded.geo_json""",
                  (sid,u["id"],now,now,ip,mac,ua,json.dumps(geo)))
        c.commit(); c.close()
    except Exception:
        pass

@app.before_request
def _security_request_guard():
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.path.startswith("/api/"):
        if request.headers.get("X-HomeCloud-Client") != "1":
            return jsonify(error="Blocked request: missing Home Cloud request marker."), 403


@app.after_request
def _security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
    if request.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


@app.before_request
def _track_authenticated_user():
    if request.endpoint not in {"login", "setup_admin", "guest_login", "logout"}:
        track_user_activity()

def log_activity(action, target="", details="", user=None):
    """Write a structured, local-only audit event to SQLite. Never store passwords or tokens."""
    try:
        u = user if user is not None else current_user()
        if u:
            username = u["username"]
            role = u["role"]
            user_id = u["id"]
        elif session.get("guest_id"):
            username = "Guest"
            role = "guest"
            user_id = None
        else:
            username = "System"
            role = "system"
            user_id = None
        c = db()
        c.execute("""INSERT INTO activity_log(timestamp,user_id,username,role,action,target,details,ip,user_agent)
                     VALUES(?,?,?,?,?,?,?,?,?)""",
                  (time.time(), user_id, str(username)[:120], str(role)[:40], str(action)[:80],
                   str(target)[:1000], str(details)[:2000], client_ip(),
                   request.headers.get("User-Agent", "")[:1000]))
        c.commit(); c.close()
    except Exception:
        pass

def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    c = db(); row = c.execute("SELECT * FROM users WHERE id=? AND active=1", (uid,)).fetchone(); c.close()
    return row

def has_perm(name):
    u = current_user()
    if not u:
        return False
    if u["role"] == "admin":
        return True
    try:
        return bool(json.loads(u["permissions"]).get(name, False))
    except Exception:
        return False

def require_login():
    if not session.get("user_id") and not session.get("guest_id"):
        return jsonify(error="Authentication required"), 401
    return None

def require_perm(name):
    if session.get("guest_id"):
        try:
            c=db(); g=c.execute("SELECT * FROM guest_tokens WHERE id=? AND active=1", (session["guest_id"],)).fetchone(); c.close()
            if not g or g["expires_at"] < time.time():
                session.clear(); return jsonify(error="Guest access expired"), 401
            return None if json.loads(g["permissions"]).get(name, False) else (jsonify(error="Permission denied"), 403)
        except Exception:
            return jsonify(error="Permission denied"), 403
    if not session.get("user_id"):
        return jsonify(error="Authentication required"), 401
    return None if has_perm(name) else (jsonify(error="Permission denied"), 403)

def rel(p):
    return str(p.relative_to(ROOT)).replace("\\", "/")


def safe(relpath):
    p = (ROOT / relpath).resolve()
    try:
        p.relative_to(ROOT.resolve())
    except ValueError:
        abort(403)
    if is_in_recycle(p):
        abort(403)
    if not path_in_scope(rel(p)):
        abort(403)
    if not p.is_file():
        abort(404)
    return p


def safe_any(relpath, allow_root=False):
    relpath = str(relpath or "").replace("\\", "/").strip("/")
    p = (ROOT / relpath).resolve() if relpath else ROOT.resolve()
    try:
        p.relative_to(ROOT.resolve())
    except ValueError:
        abort(403)
    if is_in_recycle(p):
        abort(403)
    if not allow_root and p == ROOT.resolve():
        abort(403)
    if p != ROOT.resolve() and not path_in_scope(rel(p)):
        abort(403)
    if p == ROOT.resolve() and current_scope():
        # A scoped client may only use its assigned folder as the browse root.
        scope_path = (ROOT / current_scope()).resolve()
        if not allow_root or p != scope_path:
            abort(403)
    if not allow_root and p == ROOT.resolve():
        abort(403)
    return p


def safe_relative_upload_path(raw, base):
    """Resolve a browser folder-upload path beneath its selected destination."""
    raw = str(raw or "").replace("\\", "/").strip("/")
    parts = [x for x in raw.split("/") if x not in ("", ".")]
    if not parts or any(valid_new_name(x) is None for x in parts):
        return None
    target = (Path(base).joinpath(*parts)).resolve()
    try:
        target.relative_to(Path(base).resolve())
        target.relative_to(ROOT.resolve())
    except ValueError:
        return None
    if is_in_recycle(target):
        return None
    try:
        if not path_in_scope(rel(target)):
            return None
    except Exception:
        return None
    return target


def recycle_size_bytes():
    total = 0
    root = recycle_root()
    for base, dirs, files in os.walk(root):
        for name in files:
            try:
                total += Path(base, name).stat().st_size
            except OSError:
                pass
    return total


def folder_size_bytes(path):
    total = 0
    try:
        for base, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if d != RECYCLE_NAME]
            for name in files:
                try:
                    total += Path(base, name).stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total

def valid_new_name(name):
    name = str(name or "").strip()
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        return None
    if any(ord(ch) < 32 for ch in name):
        return None
    return name


def available_upload_target(target):
    """Return a non-existing sibling path using Windows-style `name (N)` suffixes."""
    target = Path(target)
    stem = target.stem
    suffix = target.suffix
    for number in range(2, 10001):
        candidate = target.with_name(f"{stem} ({number}){suffix}")
        if not candidate.exists():
            return candidate
    raise OSError("Could not create a unique filename at the destination.")


def image_pixels(p):
    from PIL import Image
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", Image.DecompressionBombWarning)
        with Image.open(p) as im:
            return im.size[0] * im.size[1]


def make_thumb(p, force=False):
    try:
        from PIL import Image
        name = hashlib.sha1(str(p).encode("utf-8")).hexdigest() + ".jpg"
        out = THUMBS / name
        if out.exists() and not force:
            return name
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", Image.DecompressionBombWarning)
            with Image.open(p) as im:
                pixels = im.size[0] * im.size[1]
                if pixels > MAX_PIXELS:
                    return None
                im = im.convert("RGB")
                im.thumbnail((420, 420))
                im.save(out, "JPEG", quality=int(config_value("thumbnail_quality") or 85))
        return name
    except Exception:
        return None


def _tesseract_executable():
    """Find a local Tesseract OCR executable without requiring it."""
    candidates = [
        shutil.which("tesseract"),
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ]
    for item in candidates:
        if item and Path(item).is_file():
            return item
    return None


def image_metadata(p):
    """Extract lightweight local metadata for Phase 2.

    OCR is optional: if Tesseract is not installed, the rest of the metadata
    still works and the app remains fully functional.
    """
    from PIL import Image, ImageStat
    width = height = 0
    orientation = ""
    dominant = ""
    ocr_text = ""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", Image.DecompressionBombWarning)
            with Image.open(p) as im:
                width, height = im.size
                if width > height:
                    orientation = "landscape"
                elif height > width:
                    orientation = "portrait"
                else:
                    orientation = "square"
                if width * height <= MAX_PIXELS:
                    small = im.convert("RGB").copy()
                    small.thumbnail((128, 128))
                    stat = ImageStat.Stat(small)
                    rgb = tuple(max(0, min(255, int(v))) for v in stat.mean[:3])
                    dominant = "#%02x%02x%02x" % rgb
    except Exception:
        return width, height, orientation, dominant, ocr_text

    tess = _tesseract_executable()
    if tess and width and height and width * height <= MAX_PIXELS:
        try:
            # Use the CLI directly so pytesseract is not another dependency.
            proc = subprocess.run(
                [tess, str(p), "stdout", "--psm", "6"],
                capture_output=True, text=True, timeout=20,
                encoding="utf-8", errors="ignore"
            )
            if proc.returncode == 0:
                ocr_text = re.sub(r"\s+", " ", proc.stdout or "").strip()[:12000]
        except Exception:
            pass
    return width, height, orientation, dominant, ocr_text


def metadata_needs_update(old, changed):
    if not old:
        return True
    if changed:
        return True
    return not old["width"] or not old["height"] or not old["orientation"] or old["dominant_color"] is None or old["ocr_text"] is None or not old["ocr_attempted"]



def _job_claim(job_type):
    with job_manager_lock:
        active = job_manager.get("active") or ""
        if active and active != job_type:
            return False, active
        if active == job_type:
            return False, active
        job_manager["active"] = job_type
        job_manager["started_at"] = time.time()
        job_pause_event.clear()
        job_stop_event.clear()
        return True, ""

def _job_release(job_type):
    with job_manager_lock:
        if job_manager.get("active") == job_type:
            job_manager["active"] = ""
            job_manager["started_at"] = 0
        job_pause_event.clear()
        job_stop_event.clear()

def _job_control_state(job_type):
    with job_manager_lock:
        active = job_manager.get("active") or ""
    return {"active": active, "paused": bool(active == job_type and job_pause_event.is_set()), "stop_requested": bool(active == job_type and job_stop_event.is_set())}

def _wait_if_paused(state, lock):
    while job_pause_event.is_set() and not job_stop_event.is_set():
        with lock:
            state["paused"] = True
            state["message"] = "Paused — resume from Background Jobs."
        time.sleep(0.25)
    with lock:
        state["paused"] = False

def _should_stop():
    return job_stop_event.is_set()

def _perf_yield():
    time.sleep(PERF_BATCH_PAUSE)
    gc.collect()


def scan_worker(scope=""):
    global scan_state
    c = None
    run_id, started_at = _job_start("scan")
    with scan_lock:
        scan_state["started_at"] = started_at
        scan_state["finished_at"] = 0
        scan_state["duration"] = 0
    try:
        scan_root = (ROOT / normalize_scope(scope)).resolve() if normalize_scope(scope) else ROOT.resolve()
        files = [p for p in scan_root.rglob("*") if p.is_file() and not is_in_recycle(p) and p.suffix.lower() in MEDIA_EXTS]
        with scan_lock:
            scan_state.update(total=len(files), processed=0, indexed=0, skipped=0, new=0, changed=0, unchanged=0, removed=0, current="", message="Scanning...")

        c = db()
        seen = set()
        for p in files:
            _wait_if_paused(scan_state, scan_lock)
            if _should_stop():
                with scan_lock:
                    scan_state["stopped"] = True
                    scan_state["running"] = False
                    scan_state["message"] = "Scan stopped. No removal pass was applied."
                c.commit()
                summary = {k: scan_state.get(k,0) for k in ("processed","total","indexed","skipped","new","changed","unchanged","removed")}
                _job_finish(run_id, "scan", "stopped", started_at, summary, scan_state["message"])
                return
            with scan_lock:
                scan_state["current"] = str(p)
            try:
                st = p.stat()
                rp = rel(p)
                seen.add(rp)
                old = c.execute("SELECT id,size,mtime,thumb,width,height,orientation,dominant_color,ocr_text,ocr_attempted FROM assets WHERE path=?", (rp,)).fetchone()
                changed = (old is None or old["size"] != st.st_size or abs(old["mtime"] - st.st_mtime) > 0.0001)
                with scan_lock:
                    if old is None:
                        scan_state["new"] += 1
                    elif changed:
                        scan_state["changed"] += 1
                    else:
                        scan_state["unchanged"] += 1
                # Automatically repair a missing thumbnail even when the original image did not change.
                thumb_ok = bool(old and old["thumb"] and (THUMBS / str(old["thumb"])).is_file())
                th = old["thumb"] if old and not changed and thumb_ok else make_thumb(p)

                width = old["width"] if old else 0
                height = old["height"] if old else 0
                orientation = old["orientation"] if old else ""
                dominant_color = old["dominant_color"] if old else ""
                ocr_text = old["ocr_text"] if old else ""
                ocr_attempted = int(old["ocr_attempted"] or 0) if old else 0
                # Phase 2 metadata is generated for images only. Existing rows are
                # backfilled during the next scan without touching the original files.
                if p.suffix.lower() in IMAGE_EXTS and metadata_needs_update(old, changed):
                    width, height, orientation, dominant_color, ocr_text = image_metadata(p)
                    ocr_attempted = 1 if _tesseract_executable() else 0

                if old:
                    c.execute("""UPDATE assets SET name=?,ext=?,size=?,mtime=?,thumb=?,width=?,height=?,orientation=?,dominant_color=?,ocr_text=?,ocr_attempted=? WHERE id=?""",
                              (p.name, p.suffix.lower(), st.st_size, st.st_mtime, th, width, height, orientation, dominant_color, ocr_text, ocr_attempted, old["id"]))
                    if changed:
                        c.execute("DELETE FROM embeddings WHERE asset_id=?", (old["id"],))
                else:
                    c.execute("""INSERT INTO assets(path,name,ext,size,mtime,thumb,width,height,orientation,dominant_color,ocr_text,ocr_attempted)
                                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                              (rp, p.name, p.suffix.lower(), st.st_size, st.st_mtime, th, width, height, orientation, dominant_color, ocr_text, ocr_attempted))

                with scan_lock:
                    scan_state["indexed"] += 1
                    if th is None:
                        scan_state["skipped"] += 1
            except Exception:
                with scan_lock:
                    scan_state["skipped"] += 1
            with scan_lock:
                scan_state["processed"] += 1
                _scan_processed = scan_state["processed"]
                _scan_total = scan_state["total"]
            if _scan_processed % 25 == 0:
                c.commit()
            if _scan_processed % 100 == 0 or _scan_processed == _scan_total:
                server_event(f"Library scan progress: {_scan_processed}/{_scan_total} processed · {scan_state.get('new',0)} new · {scan_state.get('changed',0)} changed · {scan_state.get('skipped',0)} skipped.", "SCAN")

        # Remove database entries for files that no longer exist.
        scope_clause,scope_params=scope_sql(scope)
        rows = c.execute("SELECT id,path FROM assets WHERE 1=1"+scope_clause, scope_params).fetchall()
        for r in rows:
            if r["path"] not in seen:
                c.execute("DELETE FROM embeddings WHERE asset_id=?", (r["id"],))
                c.execute("DELETE FROM assets WHERE id=?", (r["id"],))
                with scan_lock:
                    scan_state["removed"] += 1
        c.commit()
        with scan_lock:
            scan_state["running"] = False
            scan_state["current"] = ""
            scan_state["finished_at"] = time.time()
            scan_state["duration"] = max(0.0, scan_state["finished_at"] - started_at)
            scan_state["message"] = f"Scan complete: {scan_state['new']} new, {scan_state['changed']} changed, {scan_state['unchanged']} unchanged, {scan_state['removed']} removed."
            summary = {k: scan_state.get(k,0) for k in ("processed","total","indexed","skipped","new","changed","unchanged","removed")}
        _job_finish(run_id, "scan", "completed", started_at, summary, scan_state["message"])
    except Exception as e:
        if c:
            c.close()
        with scan_lock:
            scan_state["running"] = False
            scan_state["current"] = ""
            scan_state["finished_at"] = time.time()
            scan_state["duration"] = max(0.0, scan_state["finished_at"] - started_at)
            scan_state["message"] = f"Scan error: {e}"
            summary = {k: scan_state.get(k,0) for k in ("processed","total","indexed","skipped","new","changed","unchanged","removed")}
        _job_finish(run_id, "scan", "failed", started_at, summary, scan_state["message"])
        return
    finally:
        if c:
            c.close()
        _job_release("scan")


def _job_start(job_type):
    now = time.time()
    server_event(f"{job_type.replace('_',' ').title()} job started.", "JOB")
    try:
        c = db()
        cur = c.execute("INSERT INTO job_runs(job_type,started_at,status) VALUES(?,?,?)", (job_type, now, "running"))
        c.commit()
        run_id = cur.lastrowid
        c.close()
        return int(run_id), now
    except Exception:
        return 0, now


def _job_finish(run_id, job_type, status, started_at, summary, message=""):
    if not run_id:
        return
    finished = time.time()
    level = "INFO" if status == "completed" else ("WARN" if status == "stopped" else "ERROR")
    server_event(f"{job_type.replace('_',' ').title()} job {status}: {message or 'finished'}.", level)
    try:
        c = db()
        c.execute("UPDATE job_runs SET finished_at=?,status=?,duration=?,summary_json=?,message=? WHERE id=?",
                  (finished, status, max(0.0, finished-started_at), json.dumps(summary, ensure_ascii=False), str(message or ""), run_id))
        c.commit(); c.close()
    except Exception:
        pass


def _job_history(job_type, limit=1):
    try:
        c = db()
        rows = c.execute("SELECT id,job_type,started_at,finished_at,status,duration,summary_json,message FROM job_runs WHERE job_type=? ORDER BY started_at DESC LIMIT ?", (job_type, max(1,min(int(limit),10)))).fetchall()
        c.close()
        out=[]
        for r in rows:
            try: summary=json.loads(r["summary_json"] or "{}")
            except Exception: summary={}
            out.append({"id":r["id"],"job_type":r["job_type"],"started_at":r["started_at"],"finished_at":r["finished_at"],"status":r["status"],"duration":r["duration"],"summary":summary,"message":r["message"]})
        return out
    except Exception:
        return []


def load_ai_model():
    if _ai["model"] is not None:
        return _ai["model"], _ai["preprocess"], _ai["device"]
    import torch
    import open_clip
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model, _, preprocess = open_clip.create_model_and_transforms(AI_MODEL_NAME, pretrained=AI_PRETRAINED, device=device)
    model.eval()
    _ai.update(model=model, preprocess=preprocess, device=device)
    return model, preprocess, device


def ensure_ai_model_compatibility(c):
    """Ensure stored image embeddings belong to the currently configured model.

    Embeddings from a different CLIP model must never be mixed with the new
    model. When the model changes, remove the old vectors and force a rebuild.
    """
    row = c.execute("SELECT value FROM embedding_meta WHERE key='model'").fetchone()
    current = AI_MODEL_NAME + ":" + AI_PRETRAINED
    if row and row["value"] != current:
        c.execute("DELETE FROM embeddings")
        c.execute("DELETE FROM embedding_meta WHERE key='model'")
        c.commit()
        return True
    return False



def ai_worker(scope=""):
    global ai_state
    c = None
    run_id, started_at = _job_start("ai")
    with ai_lock:
        ai_state["started_at"] = started_at
        ai_state["finished_at"] = 0
        ai_state["duration"] = 0
    try:
        with ai_lock:
            model, preprocess, device = load_ai_model()

        c = db()
        ensure_ai_model_compatibility(c)
        image_ext_params = tuple(sorted(IMAGE_EXTS))
        scope_clause,scope_params=scope_sql(scope)
        rows = c.execute("""SELECT a.* FROM assets a
                           LEFT JOIN embeddings e ON a.id=e.asset_id
                           WHERE e.asset_id IS NULL AND a.ext IN (?,?,?,?,?,?,?,?,?)""" + scope_clause + " ORDER BY a.id", image_ext_params + scope_params).fetchall()
        with ai_lock:
            ai_state.update(total=len(rows), processed=0, embedded=0, skipped=0, current="", message="Building AI index...")

        from PIL import Image
        import torch

        for start in range(0, len(rows), AI_BATCH_SIZE):
            _wait_if_paused(ai_state, ai_lock)
            if _should_stop():
                with ai_lock:
                    ai_state["stopped"] = True
                    ai_state["running"] = False
                    ai_state["current"] = ""
                    ai_state["message"] = "AI index stopped. Resume by starting Build AI Index again."
                summary = {k: ai_state.get(k,0) for k in ("processed","total","embedded","skipped")}
                _job_finish(run_id, "ai", "stopped", started_at, summary, ai_state["message"])
                return
            batch_rows = rows[start:start + AI_BATCH_SIZE]
            tensors, valid_rows = [], []
            for r in batch_rows:
                with ai_lock:
                    ai_state["current"] = r["path"]
                try:
                    p = ROOT / r["path"]
                    if image_pixels(p) > MAX_PIXELS:
                        raise ValueError("oversized image")
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore", Image.DecompressionBombWarning)
                        with Image.open(p) as im:
                            tensors.append(preprocess(im.convert("RGB")))
                    valid_rows.append(r)
                except Exception:
                    with ai_lock:
                        ai_state["skipped"] += 1
                        ai_state["processed"] += 1

            if tensors:
                try:
                    batch = torch.stack(tensors).to(device)
                    with torch.no_grad():
                        vectors = model.encode_image(batch)
                        vectors /= vectors.norm(dim=-1, keepdim=True)
                    vectors = vectors.detach().cpu().numpy().astype("float32")
                    for r, vec in zip(valid_rows, vectors):
                        c.execute("INSERT OR REPLACE INTO embeddings(asset_id,vector,dims) VALUES(?,?,?)",
                                  (r["id"], vec.tobytes(), len(vec)))
                    c.commit()
                    with ai_lock:
                        ai_state["embedded"] += len(valid_rows)
                        ai_state["processed"] += len(valid_rows)
                except Exception:
                    # Batch fallback: process one at a time so one bad image does not lose the whole batch.
                    for r in valid_rows:
                        try:
                            p = ROOT / r["path"]
                            with warnings.catch_warnings():
                                warnings.simplefilter("ignore", Image.DecompressionBombWarning)
                                with Image.open(p) as im:
                                    t = preprocess(im.convert("RGB")).unsqueeze(0).to(device)
                            with torch.no_grad():
                                vec = model.encode_image(t)
                                vec /= vec.norm(dim=-1, keepdim=True)
                            arr = vec.cpu().numpy().astype("float32")[0]
                            c.execute("INSERT OR REPLACE INTO embeddings(asset_id,vector,dims) VALUES(?,?,?)",
                                      (r["id"], arr.tobytes(), len(arr)))
                            with ai_lock:
                                ai_state["embedded"] += 1
                                ai_state["processed"] += 1
                        except Exception:
                            with ai_lock:
                                ai_state["skipped"] += 1
                                ai_state["processed"] += 1
                    c.commit()
            with ai_lock:
                _ai_processed = ai_state["processed"]
                _ai_total = ai_state["total"]
                _ai_embedded = ai_state["embedded"]
                _ai_skipped = ai_state["skipped"]
            if _ai_processed % 40 == 0 or _ai_processed == _ai_total:
                server_event(f"AI index progress: {_ai_processed}/{_ai_total} processed · {_ai_embedded} embedded · {_ai_skipped} skipped.", "AI")

        c.execute("INSERT OR REPLACE INTO embedding_meta(key,value) VALUES('model',?)", (AI_MODEL_NAME + ":" + AI_PRETRAINED,))
        c.commit()
        with ai_lock:
            ai_state["running"] = False
            ai_state["current"] = ""
            ai_state["finished_at"] = time.time()
            ai_state["duration"] = max(0.0, ai_state["finished_at"] - started_at)
            ai_state["message"] = f"AI index complete: {ai_state['embedded']} embedded, {ai_state['skipped']} skipped."
            summary = {k: ai_state.get(k,0) for k in ("processed","total","embedded","skipped")}
        _job_finish(run_id, "ai", "completed", started_at, summary, ai_state["message"])
    except Exception as e:
        with ai_lock:
            ai_state["running"] = False
            ai_state["current"] = ""
            ai_state["finished_at"] = time.time()
            ai_state["duration"] = max(0.0, ai_state["finished_at"] - started_at)
            ai_state["message"] = f"AI index error: {e}"
            summary = {k: ai_state.get(k,0) for k in ("processed","total","embedded","skipped")}
        _job_finish(run_id, "ai", "failed", started_at, summary, ai_state["message"])
    finally:
        if c:
            c.close()
        _job_release("ai")


def semantic(q, limit, scope=""):
    """Return semantic image matches using the active CLIP model.

    If the database contains vectors from another model, return no semantic
    results until the user rebuilds the AI index. This prevents incompatible
    embeddings from being compared.
    """
    import numpy as np
    import torch
    import open_clip
    model, _, device = load_ai_model()
    tok = open_clip.get_tokenizer(AI_MODEL_NAME)
    with torch.no_grad():
        t = model.encode_text(tok([q]).to(device))
        t /= t.norm(dim=-1, keepdim=True)
    tv = t.detach().cpu().numpy()[0]

    c = db()
    meta = c.execute("SELECT value FROM embedding_meta WHERE key='model'").fetchone()
    expected = AI_MODEL_NAME + ":" + AI_PRETRAINED
    if not meta or meta["value"] != expected:
        c.close()
        return []

    scope_clause,scope_params=scope_sql(scope)
    rows = c.execute("""SELECT a.id,a.path,a.name,a.ext,a.size,a.mtime,a.thumb,a.ai_tags,a.ai_caption,e.vector
                       FROM assets a JOIN embeddings e ON a.id=e.asset_id
                       WHERE a.ext IN (?,?,?,?,?,?,?,?,?)""" + scope_clause, tuple(sorted(IMAGE_EXTS)) + scope_params).fetchall()
    c.close()
    # Phase 5 safety cap: avoid unbounded vector scoring on very large libraries.
    if len(rows) > AI_SEARCH_CANDIDATE_LIMIT:
        rows = rows[:AI_SEARCH_CANDIDATE_LIMIT]
    out = []
    for r in rows:
        v = np.frombuffer(r["vector"], dtype=np.float32)
        if v.size == 0:
            continue
        # Normalize again defensively in case an older vector was stored
        # without unit normalization.
        norm = float(np.linalg.norm(v))
        if norm > 0:
            v = v / norm
        cosine = float(np.dot(v, tv))
        # Convert cosine [-1, 1] to a stable 0..1 relevance value.
        ai_relevance = max(0.0, min(1.0, (cosine + 1.0) / 2.0))
        item = {k: r[k] for k in ("id", "path", "name", "ext", "size", "mtime", "thumb")}
        try: item["ai_tags"] = json.loads(r["ai_tags"] or "[]") if r["ai_tags"] else []
        except Exception: item["ai_tags"] = []
        item["ai_caption"] = r["ai_caption"] or ""
        out.append((ai_relevance, cosine, item))
    out.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [dict(item, score=ai_score, ai_cosine=cosine) for ai_score, cosine, item in out[:limit]]


# Phase 3 — AI visual enrichment. This intentionally uses the existing CLIP
# model rather than adding a second large generative model. It produces
# searchable visual tags and a compact descriptor from those tags.
AI_TAG_LABELS = [
    "makeup", "cosmetics", "lipstick", "lip gloss", "eyeshadow", "eyeliner",
    "false eyelashes", "nails", "makeup brush", "makeup palette", "beauty product",
    "person", "woman", "man", "face", "eyes", "hands", "fashion", "jewelry",
    "product packaging", "bottle", "tube", "box", "mirror", "bag", "flat lay",
    "studio product photo", "lifestyle photo", "portrait", "outdoor scene",
    "white background", "black background", "pink", "purple", "red", "blue",
    "green", "gold", "brown", "colorful", "minimal"
]
AI_TAG_GROUPS = {
    "category": {"makeup", "cosmetics", "lipstick", "lip gloss", "eyeshadow", "eyeliner", "false eyelashes", "nails", "makeup brush", "makeup palette", "beauty product"},
    "subject": {"person", "woman", "man", "face", "eyes", "hands", "fashion", "jewelry"},
    "object": {"product packaging", "bottle", "tube", "box", "mirror", "bag"},
    "scene": {"flat lay", "studio product photo", "lifestyle photo", "portrait", "outdoor scene", "white background", "black background"},
    "color": {"pink", "purple", "red", "blue", "green", "gold", "brown", "colorful", "minimal"},
}

def _ai_tag_candidates():
    return list(AI_TAG_LABELS)

def _descriptor_from_tags(tags):
    tags = [str(x).strip() for x in tags if str(x).strip()]
    if not tags:
        return ""
    groups = {k: [] for k in AI_TAG_GROUPS}
    for tag in tags:
        for group, vals in AI_TAG_GROUPS.items():
            if tag in vals:
                groups[group].append(tag)
                break
    parts=[]
    if groups["category"]: parts.append(" / ".join(groups["category"][:2]))
    if groups["subject"]: parts.append("featuring " + " / ".join(groups["subject"][:2]))
    if groups["object"]: parts.append("with " + " / ".join(groups["object"][:2]))
    if groups["scene"]: parts.append("in " + " / ".join(groups["scene"][:2]))
    if groups["color"]: parts.append("with " + " / ".join(groups["color"][:2]) + " tones")
    return ", ".join(parts[:4])

def ai_enrichment_worker(scope=""):
    c=None
    try:
        import torch
        import open_clip
        from PIL import Image
        with ai_enrich_lock:
            model, preprocess, device = load_ai_model()
        tok = open_clip.get_tokenizer(AI_MODEL_NAME)
        labels = _ai_tag_candidates()
        prompts = [f"a photo of {x}" for x in labels]
        with torch.no_grad():
            text = model.encode_text(tok(prompts).to(device))
            text /= text.norm(dim=-1, keepdim=True)
        c=db()
        scope_clause, scope_params = scope_sql(scope)
        rows=c.execute("SELECT id,path,name FROM assets WHERE ext IN (?,?,?,?,?,?,?,?,?) AND ai_tag_attempted=0"+scope_clause+" ORDER BY id", tuple(sorted(IMAGE_EXTS))+scope_params).fetchall()
        with ai_enrich_lock:
            ai_enrich_state.update(running=True, processed=0, total=len(rows), tagged=0, failed=0, current="", message="Generating AI visual tags...")
        for r in rows:
            with ai_enrich_lock: ai_enrich_state["current"]=r["path"]
            try:
                p=ROOT/r["path"]
                if not p.exists() or is_in_recycle(p):
                    c.execute("UPDATE assets SET ai_tag_attempted=1 WHERE id=?", (r["id"],))
                    with ai_enrich_lock: ai_enrich_state["processed"] += 1
                    continue
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", Image.DecompressionBombWarning)
                    with Image.open(p) as im:
                        t=preprocess(im.convert("RGB")).unsqueeze(0).to(device)
                with torch.no_grad():
                    iv=model.encode_image(t)
                    iv/=iv.norm(dim=-1, keepdim=True)
                    scores=(iv @ text.T)[0]
                    probs=scores.softmax(dim=-1)
                vals, idxs=torch.topk(probs, k=min(6,len(labels)))
                chosen=[]
                # Require a modest confidence so unrelated labels are not stored.
                for val, idx in zip(vals.tolist(), idxs.tolist()):
                    if float(val) >= 0.045:
                        chosen.append(labels[idx])
                # Always keep up to three useful labels for otherwise low-confidence images.
                if not chosen:
                    chosen=[labels[i] for i in idxs.tolist()[:3]]
                caption=_descriptor_from_tags(chosen)
                c.execute("UPDATE assets SET ai_tags=?, ai_caption=?, ai_tag_attempted=1 WHERE id=?", (json.dumps(chosen, ensure_ascii=False), caption, r["id"]))
                with ai_enrich_lock:
                    ai_enrich_state["tagged"] += 1
                    ai_enrich_state["processed"] += 1
            except Exception:
                c.execute("UPDATE assets SET ai_tag_attempted=1 WHERE id=?", (r["id"],))
                with ai_enrich_lock:
                    ai_enrich_state["failed"] += 1
                    ai_enrich_state["processed"] += 1
            if ai_enrich_state["processed"] % 5 == 0:
                c.commit()
            if ai_enrich_state["processed"] % 25 == 0 or ai_enrich_state["processed"] == ai_enrich_state["total"]:
                server_event(f"AI enrichment progress: {ai_enrich_state['processed']}/{ai_enrich_state['total']} processed · {ai_enrich_state['tagged']} tagged · {ai_enrich_state['failed']} failed.", "AI")
        c.commit()
        with ai_enrich_lock:
            ai_enrich_state.update(running=False, current="", message=f"AI enrichment complete: {ai_enrich_state['tagged']} tagged, {ai_enrich_state['failed']} failed.")
    except Exception as e:
        with ai_enrich_lock:
            ai_enrich_state.update(running=False, current="", message=f"AI enrichment error: {e}")
    finally:
        if c: c.close()
        _job_release("ai_enrichment")

@app.route("/")
def home():
    c=db(); count=c.execute("SELECT COUNT(*) FROM users").fetchone()[0]; c.close()
    if count == 0:
        return render_template("index.html", mode="setup")
    if not session.get("user_id") and not session.get("guest_id"):
        return render_template("index.html", mode="login")
    return render_template("index.html", mode="app")

@app.get("/api/setup-status")
def setup_status():
    c=db(); count=c.execute("SELECT COUNT(*) FROM users").fetchone()[0]; c.close()
    return jsonify(configured=bool(count))

@app.post("/api/setup/choose-folder")
def setup_choose_folder():
    c=db(); count=c.execute("SELECT COUNT(*) FROM users").fetchone()[0]; c.close()
    if count:
        return jsonify(error="Initial setup is already complete."),400
    try:
        selected=choose_folder_native(str(ROOT))
        if not selected:
            return jsonify(cancelled=True)
        return jsonify(cancelled=False, asset_root=selected)
    except Exception as e:
        return jsonify(error=str(e)),500

@app.post("/api/setup")
def setup_admin():
    c=db(); count=c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if count:
        c.close(); return jsonify(error="Admin already configured"), 400
    data=request.get_json(silent=True) or {}; username=data.get("username","").strip(); password=data.get("password",""); selected=str(data.get("asset_root","")).strip()
    if len(username)<3 or len(password)<6:
        c.close(); return jsonify(error="Username must be 3+ characters and password 6+ characters"), 400
    if selected:
        try:
            set_asset_root(selected)
        except Exception as e:
            c.close(); return jsonify(error=str(e)),400
    try: setup_port=max(1024,min(int(data.get("port",8080)),65535))
    except (TypeError,ValueError): c.close(); return jsonify(error="Invalid server port."),400
    setup_ai=str(data.get("ai_mode","standard")).lower()
    if setup_ai not in {"standard","full"}: c.close(); return jsonify(error="Invalid package mode."),400
    setup_name=re.sub(r"\s+"," ",str(data.get("app_name","Home Cloud")).strip())[:60] or "Home Cloud"
    save_config_values({"app_name":setup_name,"port":setup_port,"ai_mode":setup_ai})
    from werkzeug.security import generate_password_hash
    c.execute("INSERT INTO users(username,password_hash,role,permissions,created_at) VALUES(?,?,?,?,?)", (username,generate_password_hash(password),"admin",json.dumps(DEFAULT_ADMIN_PERMS),time.time()))
    c.commit(); uid=c.execute("SELECT id FROM users WHERE username=?",(username,)).fetchone()[0]; c.close(); session.clear(); session["user_id"]=uid
    log_activity("admin_setup", "", "Initial administrator account created.")
    return jsonify(ok=True)

@app.post("/api/login")
def login():
    data=request.get_json(silent=True) or {}; username=data.get("username","").strip(); password=data.get("password","")
    ip=client_ip() or "unknown"; now=time.time()
    rec=_login_failures.get(ip, {"count":0,"first":now,"locked_until":0})
    if rec.get("locked_until",0)>now:
        retry=max(1,int(rec["locked_until"]-now))
        return jsonify(error=f"Too many failed login attempts. Try again in about {retry} seconds.", retry_after=retry), 429
    if now-rec.get("first",now)>LOGIN_RATE_WINDOW:
        rec={"count":0,"first":now,"locked_until":0}
    from werkzeug.security import check_password_hash
    c=db(); u=c.execute("SELECT * FROM users WHERE username=? AND active=1",(username,)).fetchone(); c.close()
    if not u or not check_password_hash(u["password_hash"], password):
        rec["count"]+=1
        if rec["count"]>=LOGIN_RATE_MAX_FAILURES:
            rec["locked_until"]=now+LOGIN_RATE_LOCK_SECONDS
        _login_failures[ip]=rec
        log_activity("login_failed", username, "Invalid username or password.")
        return jsonify(error="Invalid username or password"), 401
    _login_failures.pop(ip,None)
    try:
        c=db(); c.execute("UPDATE users SET last_login=? WHERE id=?", (now, u["id"])); c.commit(); c.close()
    except Exception:
        pass
    session.clear(); session["user_id"]=u["id"]; session["activity_sid"]=uuid.uuid4().hex
    log_activity("login", u["username"], "User signed in.", user=u)
    server_event(f"User login: {u['username']}")
    return jsonify(ok=True)

@app.post("/api/logout")
def logout():
    u=current_user()
    if u:
        log_activity("logout", u["username"], "User signed out.", user=u)
    elif session.get("guest_id"):
        log_activity("guest_logout", "Guest", "Guest session ended.")
    sid=session.get("activity_sid")
    if sid:
        try:
            c=db(); c.execute("DELETE FROM user_sessions WHERE session_key=?", (sid,)); c.commit(); c.close()
        except Exception:
            pass
    session.clear(); return jsonify(ok=True)

@app.get("/api/me")
def me():
    u=current_user()
    if u:
        perms=DEFAULT_ADMIN_PERMS if u["role"]=="admin" else json.loads(u["permissions"])
        return jsonify(authenticated=True, username=u["username"], role=u["role"], permissions=perms, allowed_dir=(u["allowed_dir"] if u["role"]!="admin" else ""))
    if session.get("guest_id"):
        c=db(); g=c.execute("SELECT * FROM guest_tokens WHERE id=? AND active=1",(session["guest_id"],)).fetchone(); c.close()
        if g and g["expires_at"] >= time.time():
            return jsonify(authenticated=True, username="Guest", role="guest",
                           permissions=json.loads(g["permissions"]),
                           allowed_dir=(g["allowed_dir"] or ""),
                           expires_at=g["expires_at"])
    return jsonify(authenticated=False)

@app.get("/guest/<token>")
def guest_login(token):
    c=db(); g=c.execute("SELECT * FROM guest_tokens WHERE token=? AND active=1",(token,)).fetchone(); c.close()
    if not g or g["expires_at"] < time.time(): return "Guest access expired or revoked.", 410
    session.clear(); session["guest_id"]=g["id"]
    log_activity("guest_login", "Guest", "Temporary guest link opened.")
    return redirect(url_for("home"))

@app.get("/api/users")
def list_users():
    if not current_user() or current_user()["role"]!="admin": return jsonify(error="Admin only"),403
    c=db(); rows=c.execute("SELECT id,username,role,permissions,active,created_at,allowed_dir,last_login,storage_limit_mb FROM users ORDER BY id").fetchall(); c.close()
    return jsonify([dict(r) for r in rows])

@app.post("/api/users")
def create_user():
    if not current_user() or current_user()["role"]!="admin": return jsonify(error="Admin only"),403
    data=request.get_json(silent=True) or {}; username=data.get("username","").strip(); password=data.get("password",""); role=data.get("role","client")
    if role not in ("client","admin") or len(username)<3 or len(password)<6: return jsonify(error="Invalid user data"),400
    try:
        allowed_dir = normalize_scope(data.get("allowed_dir", ""))
    except ValueError as e:
        return jsonify(error=str(e)),400
    # Empty scope means the library root (default).
    if allowed_dir:
        folder=(ROOT/allowed_dir).resolve()
        try: folder.relative_to(ROOT.resolve())
        except ValueError: return jsonify(error="Directory must be inside the library root."),400
        if not folder.is_dir(): return jsonify(error="Selected directory does not exist."),400
    perms=clean_permissions(data.get("permissions"), DEFAULT_ADMIN_PERMS if role=="admin" else DEFAULT_CLIENT_PERMS)
    try:
        storage_limit_mb=max(0,min(int(data.get("storage_limit_mb",0) or 0),10_000_000))
    except (TypeError,ValueError):
        return jsonify(error="Storage limit must be a whole number of megabytes."),400
    from werkzeug.security import generate_password_hash
    try:
        c=db(); c.execute("INSERT INTO users(username,password_hash,role,permissions,created_at,allowed_dir,storage_limit_mb) VALUES(?,?,?,?,?,?,?)",(username,generate_password_hash(password),role,json.dumps(perms),time.time(),allowed_dir,storage_limit_mb)); c.commit(); c.close()
    except sqlite3.IntegrityError: return jsonify(error="Username already exists"),409
    log_activity("user_created", username, f"Role: {role}; Directory: {allowed_dir or 'entire library'}")
    return jsonify(ok=True)

@app.post("/api/users/<int:uid>")
def update_user(uid):
    if not current_user() or current_user()["role"]!="admin": return jsonify(error="Admin only"),403
    data=request.get_json(silent=True) or {}; c=db(); u=c.execute("SELECT * FROM users WHERE id=?",(uid,)).fetchone()
    if not u: c.close(); return jsonify(error="User not found"),404
    if uid==current_user()["id"] and data.get("active") is False: c.close(); return jsonify(error="You cannot disable your own admin account"),400
    role=data.get("role",u["role"])
    if role not in ("client", "admin"):
        c.close(); return jsonify(error="Invalid role"),400
    active=bool(data.get("active",u["active"]))
    try:
        allowed_dir = normalize_scope(data.get("allowed_dir", u["allowed_dir"]))
    except ValueError as e:
        c.close(); return jsonify(error=str(e)),400
    # Empty scope means the library root (default).
    if allowed_dir:
        folder=(ROOT/allowed_dir).resolve()
        try: folder.relative_to(ROOT.resolve())
        except ValueError:
            c.close(); return jsonify(error="Directory must be inside the library root."),400
        if not folder.is_dir():
            c.close(); return jsonify(error="Selected directory does not exist."),400
    # Never allow the current admin to accidentally remove their own admin access.
    if uid==current_user()["id"] and (role!="admin" or not active):
        c.close(); return jsonify(error="You cannot remove or disable your own admin account"),400
    perms=clean_permissions(data.get("permissions"), json.loads(u["permissions"]))
    if role=="admin":
        perms=dict(DEFAULT_ADMIN_PERMS)
    try:
        storage_limit_mb=max(0,min(int(data.get("storage_limit_mb",u["storage_limit_mb"] or 0) or 0),10_000_000))
    except (TypeError,ValueError):
        c.close(); return jsonify(error="Storage limit must be a whole number of megabytes."),400
    c.execute("UPDATE users SET role=?,permissions=?,active=?,allowed_dir=?,storage_limit_mb=? WHERE id=?",(role,json.dumps(perms),1 if active else 0,allowed_dir,storage_limit_mb,uid)); c.commit(); c.close()
    log_activity("user_updated", u["username"], f"Role: {role}; Status: {'active' if active else 'disabled'}; Directory: {allowed_dir or 'entire library'}")
    return jsonify(ok=True)


@app.post("/api/users/<int:uid>/password")
def reset_user_password(uid):
    actor=current_user()
    if not actor or actor["role"]!="admin": return jsonify(error="Admin only"),403
    data=request.get_json(silent=True) or {}; password=str(data.get("password") or "")
    if len(password)<6: return jsonify(error="Password must be at least 6 characters."),400
    from werkzeug.security import generate_password_hash
    c=db(); user=c.execute("SELECT username FROM users WHERE id=?",(uid,)).fetchone()
    if not user: c.close(); return jsonify(error="User not found"),404
    c.execute("UPDATE users SET password_hash=? WHERE id=?",(generate_password_hash(password),uid))
    for saved in c.execute("SELECT session_key FROM user_sessions WHERE user_id=?",(uid,)).fetchall():
        c.execute("INSERT OR REPLACE INTO revoked_sessions(session_key,revoked_at,revoked_by) VALUES(?,?,?)",(saved["session_key"],time.time(),actor["id"]))
    c.execute("DELETE FROM user_sessions WHERE user_id=?",(uid,)); c.commit(); c.close()
    log_activity("password_reset",user["username"],"Administrator reset the password and revoked saved sessions.")
    return jsonify(ok=True)


@app.delete("/api/users/<int:uid>")
def delete_user(uid):
    actor = current_user()
    if not actor or actor["role"] != "admin":
        return jsonify(error="Admin only"), 403
    if uid == actor["id"]:
        return jsonify(error="You cannot remove your own admin account."), 400
    c = db()
    user = c.execute("SELECT id,username,role FROM users WHERE id=?", (uid,)).fetchone()
    if not user:
        c.close()
        return jsonify(error="User not found"), 404
    # Foreign-key enforcement is not enabled on older installations, so clean
    # sessions explicitly before removing the account.
    c.execute("DELETE FROM user_sessions WHERE user_id=?", (uid,))
    c.execute("DELETE FROM users WHERE id=?", (uid,))
    c.commit()
    c.close()
    log_activity("user_removed", user["username"], f"Permanently removed {user['role']} account and its active sessions.")
    return jsonify(ok=True, username=user["username"])

@app.post("/api/users/choose-directory")
def choose_user_directory():
    if not current_user() or current_user()["role"] != "admin":
        return jsonify(error="Admin only"),403
    try:
        selected=choose_folder_native(str(ROOT))
        if not selected:
            return jsonify(cancelled=True)
        p=Path(selected).expanduser().resolve()
        try:
            relpath=p.relative_to(ROOT.resolve())
        except ValueError:
            return jsonify(error="Please choose a folder inside the current library root."),400
        return jsonify(cancelled=False,allowed_dir=str(relpath).replace("\\","/"))
    except Exception as e:
        return jsonify(error=str(e)),500

def _active_session_rows(include_inactive=False):
    cutoff=time.time()-ACTIVE_USER_WINDOW
    c=db()
    if include_inactive:
        rows=c.execute("SELECT s.*,u.username,u.role,u.allowed_dir,u.active AS user_active FROM user_sessions s JOIN users u ON u.id=s.user_id ORDER BY s.last_seen DESC").fetchall()
    else:
        rows=c.execute("SELECT s.*,u.username,u.role,u.allowed_dir,u.active AS user_active FROM user_sessions s JOIN users u ON u.id=s.user_id WHERE s.last_seen>=? AND u.active=1 ORDER BY s.last_seen DESC",(cutoff,)).fetchall()
    c.close()
    return rows

def _session_public(row):
    try: geo=json.loads(row["geo_json"] or "{}")
    except Exception: geo={}
    return {"id":row["id"],"session_key":row["session_key"],"username":row["username"],"role":row["role"],"allowed_dir":row["allowed_dir"] if "allowed_dir" in row.keys() else "","ip":row["ip"],"mac":row["mac"],"first_seen":row["first_seen"],"last_seen":row["last_seen"],"user_agent":row["user_agent"],"geo":geo,"active":bool(row["user_active"] and row["last_seen"]>=time.time()-ACTIVE_USER_WINDOW)}

@app.get("/api/admin/active-users")
def active_users():
    if not current_user() or current_user()["role"]!="admin": return jsonify(error="Admin only"),403
    rows=_active_session_rows(False)
    return jsonify(active_window_seconds=ACTIVE_USER_WINDOW, users=[_session_public(r) for r in rows])

@app.get("/api/admin/active-users/<int:sid>")
def active_user_details(sid):
    if not current_user() or current_user()["role"]!="admin": return jsonify(error="Admin only"),403
    c=db(); row=c.execute("SELECT s.*,u.username,u.role,u.allowed_dir,u.active AS user_active FROM user_sessions s JOIN users u ON u.id=s.user_id WHERE s.id=?",(sid,)).fetchone(); c.close()
    if not row: return jsonify(error="Active user session not found."),404
    return jsonify(_session_public(row))

@app.post("/api/guest")
def create_guest():
    if not current_user() or current_user()["role"]!="admin": return jsonify(error="Admin only"),403
    data=request.get_json(silent=True) or {}
    hours=max(1,min(int(data.get("hours",24)),720))
    perms=clean_permissions(data.get("permissions"), DEFAULT_CLIENT_PERMS)
    # Guests never receive dashboard access, even if a crafted request asks for it.
    perms["dashboard"] = False
    try:
        allowed_dir=normalize_scope(data.get("allowed_dir",""))
    except ValueError as e:
        return jsonify(error=str(e)),400
    if allowed_dir:
        folder=(ROOT/allowed_dir).resolve()
        try:
            folder.relative_to(ROOT.resolve())
        except ValueError:
            return jsonify(error="Directory must be inside the library root."),400
        if not folder.is_dir():
            return jsonify(error="Selected directory does not exist."),400
    import secrets
    token=secrets.token_urlsafe(24)
    expires=time.time()+hours*3600
    c=db()
    c.execute("INSERT INTO guest_tokens(token,permissions,expires_at,created_at,allowed_dir) VALUES(?,?,?,?,?)",
              (token,json.dumps(perms),expires,time.time(),allowed_dir))
    c.commit(); c.close()
    log_activity("guest_created", "Guest link", f"Expires: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(expires))}; Directory: {allowed_dir or 'entire library'}")
    return jsonify(ok=True,token=token,url=guest_base_url()+"/guest/"+token,expires_at=expires,allowed_dir=allowed_dir)

@app.get("/api/guests")
def list_guests():
    if not current_user() or current_user()["role"]!="admin": return jsonify(error="Admin only"),403
    c=db()
    rows=c.execute("SELECT id,token,permissions,expires_at,active,created_at,allowed_dir FROM guest_tokens ORDER BY id DESC").fetchall()
    c.close()
    base = guest_base_url()
    result=[]
    for row in rows:
        item=dict(row)
        item["url"] = f"{base}/guest/{item['token']}"
        result.append(item)
    return jsonify(result)

@app.post("/api/guests/clear-history")
def clear_guest_history():
    if not current_user() or current_user()["role"]!="admin": return jsonify(error="Admin only"),403
    # Keep active, unexpired links. Remove only revoked or expired guest history.
    now = time.time()
    c = db()
    cur = c.execute("DELETE FROM guest_tokens WHERE active=0 OR expires_at < ?", (now,))
    deleted = cur.rowcount
    c.commit(); c.close()
    log_activity("guest_history_cleared", "Guest history", f"Deleted {deleted} revoked/expired guest record(s).")
    return jsonify(ok=True, deleted=deleted)

@app.post("/api/guests/<int:gid>/revoke")
def revoke_guest(gid):
    if not current_user() or current_user()["role"]!="admin": return jsonify(error="Admin only"),403
    c=db(); row=c.execute("SELECT id FROM guest_tokens WHERE id=?",(gid,)).fetchone(); c.execute("UPDATE guest_tokens SET active=0 WHERE id=?",(gid,)); c.commit(); c.close()
    if row:
        log_activity("guest_revoked", f"Guest #{gid}", "Temporary guest link revoked.")
    return jsonify(ok=True)



@app.get("/api/settings")
def get_settings():
    guard=require_perm("manage_settings")
    if guard: return guard
    return jsonify(asset_root=str(ROOT),port=int(config_value("port") or 8080),host="0.0.0.0",app_name=str(config_value("app_name") or "Home Cloud"),ai_mode=str(config_value("ai_mode") or "full"),thumbnail_quality=int(config_value("thumbnail_quality") or 85),lan_ip=detect_lan_ip(),internet_required=False,version=APP_VERSION,update_repo=str(config_value("update_repo") or ""),update_channel=str(config_value("update_channel") or "beta"),update_auto_check=bool(config_value("update_auto_check")))

@app.post("/api/settings/choose-folder")
def choose_folder():
    guard=require_perm("manage_settings")
    if guard: return guard
    try:
        selected=choose_folder_native(str(ROOT))
        if not selected:
            return jsonify(cancelled=True)
        return jsonify(cancelled=False, asset_root=selected)
    except Exception as e:
        return jsonify(error=str(e)), 500

@app.get("/api/settings/export")
def export_settings():
    guard = require_perm("manage_settings")
    if guard:
        return guard
    try:
        disk = shutil.disk_usage(ROOT)
        disk_data = {"total": disk.total, "used": disk.used, "free": disk.free, "percent": round((disk.used / disk.total) * 100, 1) if disk.total else 0}
    except OSError:
        disk_data = {"total": 0, "used": 0, "free": 0, "percent": 0}
    c=db()
    users=c.execute("SELECT username,role,active,permissions,created_at,allowed_dir FROM users ORDER BY id").fetchall()
    guests=c.execute("SELECT id,expires_at,active,permissions,created_at FROM guest_tokens ORDER BY id DESC").fetchall()
    c.close()
    log_activity("settings_export", "Settings", "Settings export downloaded.")
    return jsonify(exported_at=time.time(), app="Local Visual Asset Library", version=APP_VERSION, asset_root=str(ROOT), disk=disk_data, permissions={"users":[dict(x) for x in users],"guests":[dict(x) for x in guests]})

@app.get("/api/settings/database-backup")
def database_backup():
    guard=require_perm("manage_settings")
    if guard: return guard
    if not DB.exists(): return jsonify(error="Database file not found."),404
    import tempfile
    tmp=tempfile.NamedTemporaryFile(prefix="home-cloud-db-",suffix=".db",delete=False); tmp.close(); tmp_path=Path(tmp.name)
    try:
        src=sqlite3.connect(DB,timeout=30); dst=sqlite3.connect(tmp_path); src.backup(dst); dst.close(); src.close()
        @after_this_request
        def _cleanup(response):
            try: tmp_path.unlink(missing_ok=True)
            except Exception: pass
            return response
        log_activity("database_backup", "assets.db", "Created a consistent SQLite database snapshot and downloaded it.")
        return send_file(tmp_path, as_attachment=True, download_name=f"local-visual-asset-library-backup-{time.strftime('%Y%m%d-%H%M%S')}.db", mimetype="application/octet-stream", max_age=0)
    except Exception as e:
        tmp_path.unlink(missing_ok=True)
        return jsonify(error=f"Database backup failed: {e}"),500

@app.post("/api/settings")
def save_settings():
    guard=require_perm("manage_settings")
    if guard: return guard
    data=request.get_json(silent=True) or {}
    selected=str(data.get("asset_root", str(ROOT))).strip()
    if not selected: return jsonify(error="Choose an asset folder first."),400
    old_root=str(ROOT)
    try:
        set_asset_root(selected)
    except Exception as e:
        return jsonify(error=str(e)),400
    try:
        port=max(1024,min(int(data.get("port",config_value("port") or 8080)),65535))
        quality=max(50,min(int(data.get("thumbnail_quality",config_value("thumbnail_quality") or 85)),95))
    except (TypeError,ValueError): return jsonify(error="Port or thumbnail quality is invalid."),400
    app_name=re.sub(r"\s+"," ",str(data.get("app_name",config_value("app_name") or "Home Cloud")).strip())[:60] or "Home Cloud"
    ai_mode=str(data.get("ai_mode",config_value("ai_mode") or "full")).lower()
    if ai_mode not in {"standard","full"}: return jsonify(error="AI mode must be Standard or Full."),400
    update_channel=str(data.get("update_channel",config_value("update_channel") or "beta")).lower()
    if update_channel not in {"stable","beta"}: return jsonify(error="Update channel must be Stable or Beta."),400
    update_repo=str(data.get("update_repo",config_value("update_repo") or "")).strip()
    if update_repo:
        try:
            from homecloud_updater import normalize_repo
            update_repo=normalize_repo(update_repo)
        except Exception as e:
            return jsonify(error=str(e)),400
    update_auto_check=bool(data.get("update_auto_check",config_value("update_auto_check")))
    old_port=int(config_value("port") or 8080)
    save_config_values({"app_name":app_name,"port":port,"ai_mode":ai_mode,"thumbnail_quality":quality,"update_repo":update_repo,"update_channel":update_channel,"update_auto_check":update_auto_check})
    log_activity("settings_changed", "Server settings", f"Root: {old_root} -> {ROOT}; port: {old_port} -> {port}; AI mode: {ai_mode}")
    return jsonify(ok=True,asset_root=str(ROOT),requires_scan=old_root!=str(ROOT),restart_required=old_port!=port,app_name=app_name,port=port,ai_mode=ai_mode,thumbnail_quality=quality,update_repo=update_repo,update_channel=update_channel,update_auto_check=update_auto_check)


def _update_admin_guard():
    guard=require_perm("manage_settings")
    if guard: return guard
    user=current_user()
    if not user or user["role"]!="admin": return jsonify(error="Admin only"),403
    return None


def _start_update_runner(*, stage=None, rollback=None):
    """Launch a detached helper, then stop this server after its response leaves."""
    runner_dir=APP_DIR / "updates" / "runner"
    runner_dir.mkdir(parents=True,exist_ok=True)
    runner=runner_dir / "apply_update.py"
    shutil.copy2(APP_DIR / "apply_update.py",runner)
    command=[sys.executable,str(runner),"--app-dir",str(APP_DIR),"--pid",str(os.getpid()),"--python",sys.executable,"--current-version",APP_VERSION]
    if stage: command.extend(["--stage",str(stage)])
    if rollback: command.extend(["--rollback",str(rollback)])
    flags=0
    if os.name=="nt":
        flags=getattr(subprocess,"CREATE_NEW_PROCESS_GROUP",0)|getattr(subprocess,"DETACHED_PROCESS",0)|getattr(subprocess,"CREATE_NO_WINDOW",0)
    subprocess.Popen(command,cwd=str(APP_DIR),stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,close_fds=True,creationflags=flags)
    threading.Timer(1.5,lambda: os._exit(0)).start()


@app.get("/api/admin/update/status")
def update_status():
    guard=_update_admin_guard()
    if guard: return guard
    from homecloud_updater import list_backups,load_state,public_state
    state=public_state(load_state(APP_DIR))
    return jsonify(ok=True,current_version=APP_VERSION,repo=str(config_value("update_repo") or ""),channel=str(config_value("update_channel") or "beta"),auto_check=bool(config_value("update_auto_check")),backups=list_backups(APP_DIR),**state)


@app.post("/api/admin/update/check")
def update_check():
    guard=_update_admin_guard()
    if guard: return guard
    from homecloud_updater import UpdateError,check_github_release,load_state,normalize_repo,public_state,save_state
    data=request.get_json(silent=True) or {}
    repo=str(data.get("repo",config_value("update_repo") or "")).strip()
    channel=str(data.get("channel",config_value("update_channel") or "beta")).lower()
    auto_check=bool(data.get("auto_check",config_value("update_auto_check")))
    if not repo: return jsonify(error="Set the GitHub repository first (for example owner/home-cloud)."),400
    state=load_state(APP_DIR)
    try:
        repo=normalize_repo(repo)
        if channel not in {"stable","beta"}: raise UpdateError("Update channel must be stable or beta.")
        save_config_values({"update_repo":repo,"update_channel":channel,"update_auto_check":auto_check})
        checked=check_github_release(repo,channel,APP_VERSION)
        state.update(status="checked",last_error="",last_check=checked)
        save_state(APP_DIR,state)
        log_activity("update_checked",repo,f"Channel: {channel}; available: {checked.get('available',False)}")
        return jsonify(ok=True,**public_state(state))
    except UpdateError as e:
        state.update(status="check_failed",last_error=str(e),checked_at=time.time())
        save_state(APP_DIR,state)
        return jsonify(error=str(e)),502


@app.post("/api/admin/update/download")
def update_download():
    guard=_update_admin_guard()
    if guard: return guard
    from homecloud_updater import UpdateError,download_file,load_state,public_state,save_state,stage_update
    state=load_state(APP_DIR); checked=state.get("last_check") or {}; manifest=checked.get("manifest") or {}
    if not checked.get("available") or not checked.get("package_url"): return jsonify(error="Check for an available update first."),400
    downloads=APP_DIR / "updates" / "downloads"; downloads.mkdir(parents=True,exist_ok=True)
    archive=downloads / str(manifest.get("asset") or "HomeCloud-v11-update.zip")
    try:
        state.update(status="downloading",last_error=""); save_state(APP_DIR,state)
        digest,size=download_file(str(checked["package_url"]),archive)
        if digest!=str(manifest.get("sha256","")) or size!=int(manifest.get("size",0)):
            archive.unlink(missing_ok=True); raise UpdateError("Downloaded update does not match its GitHub release manifest.")
        staged=stage_update(APP_DIR,archive,manifest)
        state.update(status="ready",staged=staged,last_error="")
        save_state(APP_DIR,state)
        log_activity("update_downloaded",staged["version"],f"Verified and staged {staged['files']} application files.")
        return jsonify(ok=True,**public_state(state))
    except UpdateError as e:
        state.update(status="download_failed",last_error=str(e)); save_state(APP_DIR,state)
        return jsonify(error=str(e)),400


@app.post("/api/admin/update/manual")
def update_manual():
    guard=_update_admin_guard()
    if guard: return guard
    from homecloud_updater import MAX_UPDATE_BYTES,UpdateError,is_newer,load_state,public_state,save_state,stage_update
    upload=request.files.get("update_file")
    if not upload or not upload.filename: return jsonify(error="Choose a Home Cloud update ZIP."),400
    if not upload.filename.lower().endswith(".zip"): return jsonify(error="The manual update must be a ZIP file."),400
    downloads=APP_DIR / "updates" / "downloads"; downloads.mkdir(parents=True,exist_ok=True)
    archive=downloads / "manual-update.zip"; temp=archive.with_suffix(".part"); total=0
    try:
        with temp.open("wb") as output:
            while True:
                chunk=upload.stream.read(1024*1024)
                if not chunk: break
                total+=len(chunk)
                if total>MAX_UPDATE_BYTES: raise UpdateError("Update package exceeds the 512 MB safety limit.")
                output.write(chunk)
        os.replace(temp,archive)
        staged=stage_update(APP_DIR,archive)
        if not is_newer(staged["version"],APP_VERSION):
            shutil.rmtree(staged["stage"],ignore_errors=True)
            raise UpdateError("Manual update must be newer than the installed version.")
        state=load_state(APP_DIR); state.update(status="ready",staged=staged,last_error="",manual=True); save_state(APP_DIR,state)
        log_activity("update_uploaded",staged["version"],f"Verified and staged {staged['files']} application files.")
        return jsonify(ok=True,**public_state(state))
    except (UpdateError,OSError) as e:
        temp.unlink(missing_ok=True)
        return jsonify(error=str(e)),400


@app.post("/api/admin/update/install")
def update_install():
    guard=_update_admin_guard()
    if guard: return guard
    from homecloud_updater import load_state,save_state
    state=load_state(APP_DIR); staged=state.get("staged") or {}; stage=Path(str(staged.get("stage") or ""))
    try:
        stage.resolve().relative_to((APP_DIR/"updates"/"staged").resolve())
    except (ValueError,OSError): return jsonify(error="No verified update is ready to install."),400
    if not (stage/"update-package.json").is_file(): return jsonify(error="The staged update is missing. Download it again."),400
    state.update(status="installing",last_error="",install_started_at=time.time()); save_state(APP_DIR,state)
    log_activity("update_installing",str(staged.get("version") or "Update"),"Application backup and restart started.")
    try: _start_update_runner(stage=stage)
    except Exception as e:
        state.update(status="install_failed",last_error=str(e)); save_state(APP_DIR,state)
        return jsonify(error=f"Could not start update helper: {e}"),500
    return jsonify(ok=True,restarting=True,message="Update is being applied. Home Cloud will restart automatically."),202


@app.post("/api/admin/update/rollback")
def update_rollback():
    guard=_update_admin_guard()
    if guard: return guard
    from homecloud_updater import list_backups,load_state,save_state
    backup_id=str((request.get_json(silent=True) or {}).get("backup_id") or "")
    allowed={item["id"] for item in list_backups(APP_DIR)}
    if backup_id not in allowed: return jsonify(error="That update backup is not available."),404
    backup=APP_DIR/"backups"/backup_id
    state=load_state(APP_DIR); state.update(status="rolling_back",last_error=""); save_state(APP_DIR,state)
    log_activity("update_rollback",backup_id,"Rollback and restart started.")
    try: _start_update_runner(rollback=backup)
    except Exception as e:
        state.update(status="rollback_failed",last_error=str(e)); save_state(APP_DIR,state)
        return jsonify(error=f"Could not start rollback helper: {e}"),500
    return jsonify(ok=True,restarting=True,message="Rollback is being applied. Home Cloud will restart automatically."),202

@app.get("/api/status")
def status():
    guard=require_perm("view")
    if guard: return guard
    c = db()
    scope=current_scope(); scope_clause,scope_params=scope_sql(scope)
    a = c.execute("SELECT COUNT(*) FROM assets WHERE 1=1"+scope_clause, scope_params).fetchone()[0]
    images = c.execute("SELECT COUNT(*) FROM assets WHERE ext IN (?,?,?,?,?,?,?,?,?)"+scope_clause, tuple(sorted(IMAGE_EXTS))+scope_params).fetchone()[0]
    videos = c.execute("SELECT COUNT(*) FROM assets WHERE ext IN (?,?,?,?,?,?,?,?,?,?)"+scope_clause, tuple(sorted(VIDEO_EXTS))+scope_params).fetchone()[0]
    audio = c.execute("SELECT COUNT(*) FROM assets WHERE ext IN (?,?,?,?,?,?,?,?)"+scope_clause, tuple(sorted(AUDIO_EXTS))+scope_params).fetchone()[0]
    e = c.execute("SELECT COUNT(*) FROM embeddings e JOIN assets a ON a.id=e.asset_id WHERE 1=1"+scope_clause, scope_params).fetchone()[0]
    pending = c.execute("SELECT COUNT(*) FROM assets a LEFT JOIN embeddings e ON a.id=e.asset_id WHERE e.asset_id IS NULL"+scope_clause, scope_params).fetchone()[0]
    c.close()
    with scan_lock:
        s = dict(scan_state)
    with ai_lock:
        ai = dict(ai_state)
    
    try:
        disk = shutil.disk_usage(ROOT)
        disk_data = {"total": disk.total, "used": disk.used, "free": disk.free, "percent": round((disk.used / disk.total) * 100, 1) if disk.total else 0}
    except OSError:
        disk_data = {"total": 0, "used": 0, "free": 0, "percent": 0}
    return jsonify(root=(str(ROOT) if (current_user() and current_user()["role"]=="admin") else ""), images=images, videos=videos, audio=audio, media=a, embedded=e, pending=pending, scan=s, ai=ai, disk=disk_data, recycle_count=len(_read_trash_items()), recycle_bytes=recycle_size_bytes())



def _thumbnail_assets():
    c = db()
    try:
        rows = c.execute("SELECT id,path,name,ext,thumb FROM assets WHERE ext IN (?,?,?,?,?,?,?,?,?) ORDER BY id", tuple(sorted(IMAGE_EXTS))).fetchall()
        return rows
    finally:
        c.close()


def _thumbnail_status_data():
    rows = _thumbnail_assets()
    total = len(rows)
    existing = 0
    missing = 0
    db_without_thumb = 0
    for r in rows:
        if r["thumb"] and (THUMBS / str(r["thumb"])).is_file():
            existing += 1
        else:
            missing += 1
            if not r["thumb"]:
                db_without_thumb += 1
    try:
        thumb_files = [p for p in THUMBS.iterdir() if p.is_file()]
        thumb_bytes = sum(p.stat().st_size for p in thumb_files)
    except Exception:
        thumb_files = []
        thumb_bytes = 0
    return {"total": total, "existing": existing, "missing": missing,
            "db_without_thumb": db_without_thumb, "files": len(thumb_files),
            "bytes": thumb_bytes}


def _thumbnail_worker(mode="missing"):
    global thumb_state
    c = None
    run_id, started_at = _job_start("thumbnails")
    with thumb_lock:
        thumb_state["started_at"] = started_at
        thumb_state["finished_at"] = 0
        thumb_state["duration"] = 0
    try:
        rows = _thumbnail_assets()
        if mode == "missing":
            work_rows = [r for r in rows if not r["thumb"] or not (THUMBS / str(r["thumb"])).is_file()]
        else:
            work_rows = rows
        with thumb_lock:
            thumb_state.update(running=True, mode=mode, processed=0, total=len(work_rows),
                               created=0, skipped=0, failed=0, current="",
                               message=("Regenerating missing thumbnails..." if mode == "missing" else "Rebuilding all thumbnails..."))
        c = db()
        for r in work_rows:
            _wait_if_paused(thumb_state, thumb_lock)
            if _should_stop():
                with thumb_lock:
                    thumb_state["stopped"] = True
                    thumb_state["running"] = False
                    thumb_state["message"] = "Thumbnail job stopped. Start it again to continue."
                c.commit()
                summary = {"mode":mode,"processed":thumb_state.get("processed",0),"total":thumb_state.get("total",0),"created":thumb_state.get("created",0),"skipped":thumb_state.get("skipped",0),"failed":thumb_state.get("failed",0)}
                _job_finish(run_id, "thumbnails", "stopped", started_at, summary, thumb_state["message"])
                return
            with thumb_lock:
                thumb_state["current"] = r["path"]
            try:
                p = (ROOT / str(r["path"])).resolve()
                try:
                    p.relative_to(ROOT.resolve())
                except ValueError:
                    raise ValueError("Asset path is outside the library root")
                if not p.is_file():
                    with thumb_lock:
                        thumb_state["failed"] += 1
                else:
                    old_name = str(r["thumb"] or "")
                    old_file = THUMBS / old_name if old_name else None
                    name = make_thumb(p, force=(mode == "rebuild"))
                    if name:
                        c.execute("UPDATE assets SET thumb=? WHERE id=?", (name, r["id"]))
                        if old_name and old_name != name and old_file and old_file.is_file():
                            try: old_file.unlink()
                            except Exception: pass
                        with thumb_lock:
                            if mode == "missing" and old_name and old_file and old_file.is_file():
                                thumb_state["skipped"] += 1
                            else:
                                thumb_state["created"] += 1
                    else:
                        with thumb_lock:
                            thumb_state["failed"] += 1
            except Exception:
                with thumb_lock:
                    thumb_state["failed"] += 1
            with thumb_lock:
                thumb_state["processed"] += 1
            if thumb_state["processed"] % 25 == 0:
                c.commit()
        c.commit()
        with thumb_lock:
            created = thumb_state["created"]
            skipped = thumb_state["skipped"]
            failed = thumb_state["failed"]
            thumb_state["running"] = False
            thumb_state["current"] = ""
            thumb_state["finished_at"] = time.time()
            thumb_state["duration"] = max(0.0, thumb_state["finished_at"] - started_at)
            thumb_state["message"] = f"Thumbnail {('repair' if mode == 'missing' else 'rebuild')} complete: {created} created, {skipped} skipped, {failed} failed."
            summary = {"mode":mode,"processed":thumb_state.get("processed",0),"total":thumb_state.get("total",0),"created":created,"skipped":skipped,"failed":failed}
        _job_finish(run_id, "thumbnails", "completed", started_at, summary, thumb_state["message"])
    except Exception as e:
        with thumb_lock:
            thumb_state["running"] = False
            thumb_state["current"] = ""
            thumb_state["finished_at"] = time.time()
            thumb_state["duration"] = max(0.0, thumb_state["finished_at"] - started_at)
            thumb_state["message"] = f"Thumbnail error: {e}"
            summary = {"mode":mode,"processed":thumb_state.get("processed",0),"total":thumb_state.get("total",0),"created":thumb_state.get("created",0),"skipped":thumb_state.get("skipped",0),"failed":thumb_state.get("failed",0)}
        _job_finish(run_id, "thumbnails", "failed", started_at, summary, thumb_state["message"])
    finally:
        if c:
            c.close()
        _job_release("thumbnails")


@app.get("/api/admin/thumbnails/status")
def admin_thumbnail_status():
    if not current_user() or current_user()["role"] != "admin":
        return jsonify(error="Admin only"), 403
    with thumb_lock:
        state = dict(thumb_state)
    return jsonify(status=_thumbnail_status_data(), job=state)


@app.post("/api/admin/thumbnails/regenerate")
def admin_thumbnail_regenerate():
    if not current_user() or current_user()["role"] != "admin":
        return jsonify(error="Admin only"), 403
    mode = (request.get_json(silent=True) or {}).get("mode", "missing")
    if mode not in {"missing", "rebuild"}:
        return jsonify(error="Invalid thumbnail mode"), 400
    with scan_lock:
        if scan_state["running"]:
            return jsonify(error="Library scan is running. Wait for it to finish before regenerating thumbnails."), 409
    with thumb_lock:
        if thumb_state["running"]:
            return jsonify(error="Thumbnail job is already running."), 409
    ok, active = _job_claim("thumbnails")
    if not ok:
        return jsonify(error=f"{active.title()} job is currently running. Wait for it to finish first.", active_job=active), 409
    with thumb_lock:
        thumb_state.update(running=True, paused=False, stopped=False, mode=mode, processed=0, total=0, created=0, skipped=0, failed=0, current="", message="Starting thumbnail job...")
    threading.Thread(target=_thumbnail_worker, args=(mode,), daemon=True).start()
    log_activity("thumbnail_regeneration", "Thumbnails", f"Thumbnail {mode} job started.")
    return jsonify(started=True, mode=mode)


@app.get("/api/admin/thumbnails/orphans")
def admin_thumbnail_orphans():
    if not current_user() or current_user()["role"] != "admin":
        return jsonify(error="Admin only"), 403
    rows = _thumbnail_assets()
    valid = {str(r["thumb"]) for r in rows if r["thumb"]}
    try:
        files = [p for p in THUMBS.iterdir() if p.is_file()]
    except Exception:
        files = []
    orphans = [p for p in files if p.name not in valid]
    return jsonify(count=len(orphans), bytes=sum(p.stat().st_size for p in orphans))


@app.post("/api/admin/thumbnails/clean-orphans")
def admin_thumbnail_clean_orphans():
    if not current_user() or current_user()["role"] != "admin":
        return jsonify(error="Admin only"), 403
    if not request.get_json(silent=True) or (request.get_json(silent=True) or {}).get("confirm") is not True:
        return jsonify(error="Confirmation required"), 400
    rows = _thumbnail_assets()
    valid = {str(r["thumb"]) for r in rows if r["thumb"]}
    removed = 0
    bytes_removed = 0
    try:
        files = [p for p in THUMBS.iterdir() if p.is_file()]
    except Exception:
        files = []
    for p in files:
        if p.name in valid:
            continue
        try:
            size = p.stat().st_size
            p.unlink()
            removed += 1
            bytes_removed += size
        except Exception:
            pass
    log_activity("thumbnail_orphans_cleaned", "Thumbnails", f"Removed {removed} orphan thumbnail file(s).")
    return jsonify(ok=True, removed=removed, bytes=bytes_removed)


@app.post("/api/scan")
def api_scan():
    guard=require_perm("scan")
    if guard: return guard
    with scan_lock:
        if scan_state["running"]:
            return jsonify({**scan_state, "started": False})
    ok, active = _job_claim("scan")
    if not ok:
        return jsonify(error=f"{active.title()} job is currently running. Wait for it to finish first.", active_job=active), 409
    with scan_lock:
        scan_state.update(running=True, paused=False, stopped=False, processed=0, total=0, indexed=0, skipped=0, new=0, changed=0, unchanged=0, removed=0, current="", message="Starting scan...")
        payload = {**scan_state, "started": True}
    threading.Thread(target=scan_worker, args=(current_scope(),), daemon=True).start()
    server_event("Library scan requested by administrator.", "SCAN")
    log_activity("scan_started", "Library", "Library scan started.")
    return jsonify(payload)


@app.get("/api/scan-status")
def scan_status():
    with scan_lock:
        return jsonify(dict(scan_state))


@app.post("/api/embed")
def api_embed():
    guard=require_perm("scan")
    if guard: return guard
    if config_value("ai_mode")!="full": return jsonify(error="AI indexing is disabled in Standard mode."),409
    with ai_lock:
        if ai_state["running"]:
            return jsonify({**ai_state, "started": False})
    ok, active = _job_claim("ai")
    if not ok:
        return jsonify(error=f"{active.title()} job is currently running. Wait for it to finish first.", active_job=active), 409
    with ai_lock:
        ai_state.update(running=True, paused=False, stopped=False, processed=0, total=0, embedded=0, skipped=0, current="", message="Starting AI index...")
        payload = {**ai_state, "started": True}
    threading.Thread(target=ai_worker, args=(current_scope(),), daemon=True).start()
    server_event("AI index build requested by administrator.", "AI")
    log_activity("ai_index_started", "AI Index", "AI embedding build started.")
    return jsonify(payload)


@app.get("/api/ai-status")
def ai_status():
    with ai_lock:
        return jsonify(dict(ai_state))


@app.post("/api/sync")
def api_sync():
    guard=require_perm("scan")
    if guard: return guard
    # Start scan; frontend then waits for scan and starts AI embedding automatically.
    with scan_lock:
        if scan_state["running"]:
            return jsonify({"started": False, "message": "Scan already running."})
    ok, active = _job_claim("scan")
    if not ok:
        return jsonify(error=f"{active.title()} job is currently running. Wait for it to finish first.", active_job=active), 409
    with scan_lock:
        scan_state.update(running=True, paused=False, stopped=False, processed=0, total=0, indexed=0, skipped=0, new=0, changed=0, unchanged=0, removed=0, current="", message="Starting sync scan...")
    threading.Thread(target=scan_worker, args=(current_scope(),), daemon=True).start()
    log_activity("sync_started", "Library", "Library sync scan started.")
    return jsonify({"started": True})


@app.get("/api/files")
def browse_files():
    """Browse folders and non-media files directly from the configured root.
    Media files remain in the media library; this view is for ordinary files.
    """
    guard = require_perm("view")
    if guard:
        return guard

    relpath = request.args.get("path", "").strip().replace("\\", "/").strip("/")
    include_media = request.args.get("include_media", "0").lower() in {"1", "true", "yes", "on"}
    base = (ROOT / relpath).resolve()
    try:
        base.relative_to(ROOT.resolve())
    except ValueError:
        return jsonify(error="Invalid folder path"), 403
    if not path_in_scope(relpath):
        return jsonify(error="Access denied for this directory."), 403
    if not base.exists() or not base.is_dir():
        return jsonify(error="Folder not found"), 404

    entries = []
    try:
        children = list(base.iterdir())
    except PermissionError:
        return jsonify(error="Access denied for this folder"), 403

    download_allowed = session_has_perm("download")
    preview_allowed = bool(require_perm("preview") is None)
    for child in children:
        try:
            child_rel = rel(child)
            if child.is_dir() and child.name != RECYCLE_NAME:
                entries.append({
                    "kind": "folder",
                    "name": child.name,
                    "path": child_rel,
                })
            elif child.is_file():
                ext = child.suffix.lower()
                is_media = ext in MEDIA_EXTS
                if is_media and not include_media:
                    continue
                st = child.stat()
                if ext in IMAGE_EXTS:
                    media_type = "image"
                elif ext in VIDEO_EXTS:
                    media_type = "video"
                elif ext in AUDIO_EXTS:
                    media_type = "audio"
                else:
                    media_type = "file"
                entries.append({
                    "kind": "file",
                    "name": child.name,
                    "path": child_rel,
                    "ext": ext,
                    "size": st.st_size,
                    "mtime": st.st_mtime,
                    "media": is_media,
                    "media_type": media_type,
                    "download_allowed": download_allowed,
                    "preview_allowed": preview_allowed,
                })
        except (OSError, ValueError):
            continue

    entries.sort(key=lambda x: (0 if x["kind"] == "folder" else 1, x["name"].casefold()))
    parent = ""
    if relpath:
        parts = relpath.split("/")
        parent = "/".join(parts[:-1])

    return jsonify(
        path=relpath,
        name=base.name if relpath else ROOT.name,
        parent=parent,
        entries=entries,
        download_allowed=download_allowed,
        upload_allowed=session_has_perm("upload"),
        create_folder_allowed=session_has_perm("create_folder"),
        rename_allowed=session_has_perm("rename"),
        copy_allowed=session_has_perm("copy"),
        move_allowed=session_has_perm("move"),
        overwrite_allowed=session_has_perm("overwrite"),
        delete_allowed=session_has_perm("delete"),
        include_media=include_media,
    )


def rebase_asset_paths(old_rel, new_rel):
    """Keep indexed media paths consistent after a file/folder rename or move."""
    old_rel = str(old_rel or "").strip("/")
    new_rel = str(new_rel or "").strip("/")
    if not old_rel:
        return 0
    c = db()
    rows = c.execute("SELECT id,path FROM assets WHERE path=? OR path LIKE ?", (old_rel, old_rel + "/%")).fetchall()
    changed = 0
    for row in rows:
        old_path = row["path"]
        suffix = old_path[len(old_rel):]
        new_path = new_rel + suffix
        c.execute("UPDATE assets SET path=?, name=? WHERE id=?", (new_path, Path(new_path).name, row["id"]))
        changed += 1
    c.commit(); c.close()
    return changed


def remove_asset_paths(root_rel):
    """Remove deleted media from the index so the UI does not show dead paths."""
    root_rel = str(root_rel or "").strip("/")
    if not root_rel:
        return 0
    c = db()
    rows = c.execute("SELECT id FROM assets WHERE path=? OR path LIKE ?", (root_rel, root_rel + "/%")).fetchall()
    ids = [r["id"] for r in rows]
    if ids:
        marks = ",".join("?" for _ in ids)
        c.execute(f"DELETE FROM embeddings WHERE asset_id IN ({marks})", ids)
        c.execute(f"DELETE FROM assets WHERE id IN ({marks})", ids)
    c.commit(); c.close()
    return len(ids)


def _trash_manifest_dir(item_id):
    return recycle_root() / item_id

def _trash_payload_path(item_id):
    return _trash_manifest_dir(item_id) / "payload"

def _trash_manifest_path(item_id):
    return _trash_manifest_dir(item_id) / "manifest.json"

def _write_trash_item(source, source_rel):
    import uuid
    item_id = uuid.uuid4().hex
    item_dir = _trash_manifest_dir(item_id)
    item_dir.mkdir(parents=True, exist_ok=False)
    payload = item_dir / "payload"
    if source.is_dir():
        shutil.move(str(source), str(payload))
        kind = "folder"
    else:
        payload.mkdir()
        shutil.move(str(source), str(payload / source.name))
        kind = "file"
    manifest = {"id": item_id, "original_path": source_rel, "name": source.name, "kind": kind, "deleted_at": time.time()}
    _trash_manifest_path(item_id).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest

def _read_trash_items():
    root = recycle_root()
    items=[]
    for d in root.iterdir():
        if not d.is_dir():
            continue
        mp=d / "manifest.json"
        try:
            data=json.loads(mp.read_text(encoding="utf-8"))
            if data.get("id") == d.name and (d / "payload").exists():
                items.append(data)
        except Exception:
            continue
    items.sort(key=lambda x: x.get("deleted_at",0), reverse=True)
    return items

def _restore_trash_item(item_id):
    items={x["id"]:x for x in _read_trash_items()}
    item=items.get(item_id)
    if not item:
        return None, "Recycle Bin item not found."
    payload=_trash_payload_path(item_id)
    target=(ROOT / item["original_path"]).resolve()
    try: target.relative_to(ROOT.resolve())
    except ValueError: return None, "Invalid restore path."
    if is_in_recycle(target): return None, "Invalid restore path."
    if not path_in_scope(item.get("original_path", "")):
        return None, "You do not have access to restore this item."
    if target.exists(): return None, f"The original location already contains '{target.name}'. Move or rename it first."
    target.parent.mkdir(parents=True, exist_ok=True)
    if item.get("kind") == "folder":
        shutil.move(str(payload), str(target))
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        src=payload / item["name"]
        if not src.exists(): return None, "Trashed file payload is missing."
        shutil.move(str(src), str(target))
    shutil.rmtree(_trash_manifest_dir(item_id), ignore_errors=True)
    return item, None

@app.get("/api/files/trash")
def list_trash():
    guard=require_perm("delete")
    if guard: return guard
    scope=current_scope()
    items=[x for x in _read_trash_items() if path_in_scope(x.get("original_path", ""), scope)]
    return jsonify(items=items)

@app.post("/api/files/trash")
def trash_file_entry():
    guard=require_perm("delete")
    if guard: return guard
    data=request.get_json(silent=True) or {}
    target=safe_any(data.get("path", ""))
    target_rel=rel(target)
    try:
        manifest=_write_trash_item(target, target_rel)
        remove_asset_paths(target_rel)
    except OSError as e:
        return jsonify(error=str(e)),400
    return jsonify(ok=True,item=manifest)

@app.post("/api/files/trash-bulk")
def trash_file_entries_bulk():
    guard=require_perm("delete")
    if guard: return guard
    data=request.get_json(silent=True) or {}
    paths=data.get("paths") or []
    if not isinstance(paths,list) or not paths: return jsonify(error="No files or folders selected."),400
    cleaned=[]; seen=set()
    for raw in paths[:200]:
        try: target=safe_any(raw)
        except Exception: continue
        rp=rel(target)
        if not rp or rp in seen or not target.exists(): continue
        seen.add(rp); cleaned.append((target,rp))
    # Avoid trashing a child separately when its parent folder is also selected.
    selected=set(rp for _,rp in cleaned)
    cleaned=[x for x in cleaned if not any(x[1].startswith(parent+"/") for parent in selected if parent!=x[1])]
    trashed=[]; failed=[]
    for target,rp in cleaned:
        try:
            m=_write_trash_item(target,rp); remove_asset_paths(rp); trashed.append(m)
        except OSError as e: failed.append({"path":rp,"error":str(e)})
    return jsonify(ok=True,trashed=trashed,failed=failed)

@app.post("/api/files/trash/<item_id>/restore")
def restore_trash_item(item_id):
    guard=require_perm("delete")
    if guard: return guard
    try: item,error=_restore_trash_item(item_id)
    except OSError as e: return jsonify(error=str(e)),400
    if error: return jsonify(error=error),409 if "already contains" in error else 404
    # Re-index restored media metadata in the background; AI embeddings can be rebuilt with Build AI Index.
    with scan_lock:
        can_start=not scan_state["running"]
        if can_start: scan_state.update(running=True,processed=0,total=0,indexed=0,skipped=0,current="",message="Re-indexing restored files...")
    if can_start: threading.Thread(target=scan_worker,daemon=True).start()
    log_activity("restore", item.get("original_path", ""), "Restored from Recycle Bin.")
    return jsonify(ok=True,item=item,scan_started=can_start)

@app.post("/api/files/trash/<item_id>/permanent")
def permanent_delete_trash_item(item_id):
    guard=require_perm("delete")
    if guard: return guard
    data=request.get_json(silent=True) or {}
    if data.get("confirmation") != PERMANENT_DELETE_PHRASE:
        return jsonify(error=f'Type "{PERMANENT_DELETE_PHRASE}" to permanently delete this item.'),400
    item_dir=_trash_manifest_dir(item_id)
    if not item_dir.exists(): return jsonify(error="Recycle Bin item not found."),404
    item_map={x["id"]:x for x in _read_trash_items()}
    item=item_map.get(item_id)
    if not item or not path_in_scope(item.get("original_path", "")):
        return jsonify(error="Recycle Bin item not found."),404
    try: shutil.rmtree(item_dir)
    except OSError as e: return jsonify(error=str(e)),400
    log_activity("permanent_delete", item.get("original_path", ""), "Permanently deleted from Recycle Bin.")
    return jsonify(ok=True)

@app.post("/api/files/trash/permanent-bulk")
def permanent_delete_trash_bulk():
    guard=require_perm("delete")
    if guard: return guard
    data=request.get_json(silent=True) or {}
    if data.get("confirmation") != PERMANENT_DELETE_PHRASE:
        return jsonify(error=f'Type "{PERMANENT_DELETE_PHRASE}" to permanently delete these items.'),400
    ids=data.get("ids") or []
    if not isinstance(ids,list) or not ids:
        return jsonify(error="No Recycle Bin items selected."),400
    items={x["id"]:x for x in _read_trash_items() if path_in_scope(x.get("original_path", ""))}
    deleted=0; failed=[]
    for raw in ids[:200]:
        item_id=str(raw)
        if item_id not in items:
            failed.append({"id":item_id,"error":"Recycle Bin item not found."})
            continue
        item_dir=_trash_manifest_dir(item_id)
        try:
            item_dir.resolve().relative_to(recycle_root().resolve())
            if item_dir.resolve() == recycle_root().resolve():
                raise OSError("Invalid Recycle Bin path.")
            shutil.rmtree(item_dir)
            deleted+=1
        except OSError as e:
            failed.append({"id":item_id,"error":str(e)})
    if deleted:
        log_activity("permanent_delete_bulk", "Recycle Bin", f"Permanently deleted {deleted} item(s).")
    return jsonify(ok=True,deleted=deleted,failed=failed)

@app.post("/api/files/trash/empty")
def empty_trash():
    guard=require_perm("delete")
    if guard: return guard
    data=request.get_json(silent=True) or {}
    if data.get("confirmation") != EMPTY_RECYCLE_PHRASE:
        return jsonify(error=f'Type "{EMPTY_RECYCLE_PHRASE}" to empty the Recycle Bin.'),400
    root=recycle_root(); deleted=0
    allowed_ids={x["id"] for x in _read_trash_items() if path_in_scope(x.get("original_path", ""))}
    for d in list(root.iterdir()):
        try:
            if d.name not in allowed_ids: continue
            if d.is_dir(): shutil.rmtree(d)
            else: d.unlink()
            deleted+=1
        except OSError:
            pass
    if deleted:
        log_activity("recycle_bin_empty", "Recycle Bin", f"Permanently deleted {deleted} item(s).")
    return jsonify(ok=True,deleted=deleted)

@app.post("/api/files/folder")
def create_file_folder():
    guard = require_perm("create_folder")
    if guard:
        return guard
    data = request.get_json(silent=True) or {}
    parent = str(data.get("path", "")).strip()
    name = valid_new_name(data.get("name"))
    if not name:
        return jsonify(error="Enter a valid folder name."), 400
    base = safe_any(parent, allow_root=True)
    if not base.is_dir():
        return jsonify(error="Destination folder not found."), 404
    target = (base / name).resolve()
    try:
        target.relative_to(ROOT.resolve())
    except ValueError:
        return jsonify(error="Invalid folder path."), 403
    if target.exists():
        return jsonify(error="A file or folder with that name already exists."), 409
    try:
        target.mkdir()
    except OSError as e:
        return jsonify(error=str(e)), 400
    new_rel=rel(target)
    log_activity("folder_created", new_rel, "Folder created.")
    return jsonify(ok=True, path=new_rel)


@app.post("/api/files/upload")
def upload_files():
    guard = require_perm("upload")
    if guard:
        log_activity("upload_failed", "Files", "Upload permission denied.")
        return guard
    user=current_user()
    if user and user["role"]!="admin" and int(user["storage_limit_mb"] or 0)>0:
        limit_bytes=int(user["storage_limit_mb"])*1024*1024
        current_bytes=scoped_storage_usage(user["allowed_dir"] or "")
        incoming_bytes=max(0,int(request.content_length or 0))
        if current_bytes+incoming_bytes>limit_bytes:
            return jsonify(error=f"Upload would exceed this account's {user['storage_limit_mb']} MB storage limit.",storage_used=current_bytes,storage_limit=limit_bytes),413
    destination = str(request.form.get("path", "")).strip()
    base = safe_any(destination, allow_root=True)
    if not base.is_dir():
        return jsonify(error="Destination folder not found."), 404
    uploaded = request.files.getlist("files")
    rel_paths = request.form.getlist("relative_paths")
    conflict = str(request.form.get("conflict", "error")).strip().lower()
    if conflict not in {"error", "skip", "keep_both", "replace"}:
        return jsonify(error="Invalid upload conflict option."), 400
    if conflict == "replace" and not session_has_perm("overwrite"):
        return jsonify(error="Overwrite permission is required to replace existing files."), 403
    if not uploaded:
        log_activity("upload_failed", destination or "Files", "No files were received by the server.")
        return jsonify(error="No files selected."), 400
    created = []
    failed = []
    skipped = []
    replaced = []
    for idx, item in enumerate(uploaded):
        browser_rel = rel_paths[idx] if idx < len(rel_paths) else ""
        name = valid_new_name(item.filename)
        if browser_rel:
            target = safe_relative_upload_path(browser_rel, base)
            if target is None:
                failed.append({"name": item.filename, "error": "Invalid upload path."})
                continue
            name = target.name
        else:
            if not name:
                failed.append({"name": item.filename, "error": "Invalid filename."})
                continue
            target = (base / name).resolve()
        if not name or target.name in {".", ".."}:
            failed.append({"name": item.filename, "error": "Invalid filename."})
            continue
        if target.exists():
            if target.is_dir():
                failed.append({"name": item.filename, "error": f"A folder named '{target.name}' already exists."})
                continue
            if conflict == "skip":
                skipped.append({"name": item.filename, "reason": "An item with that name already exists."})
                continue
            if conflict == "keep_both":
                try:
                    target = available_upload_target(target)
                except OSError as e:
                    failed.append({"name": item.filename, "error": str(e)})
                    continue
            elif conflict != "replace":
                failed.append({"name": item.filename, "error": f"A file or folder named '{target.name}' already exists."})
                continue
        partial = None
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            partial = target.with_name(f".{target.name}.{uuid.uuid4().hex}.uploading")
            item.save(partial)
            if target.exists():
                if conflict == "replace" and target.is_file():
                    os.replace(partial, target)
                    replaced.append(rel(target))
                elif conflict == "keep_both":
                    target = available_upload_target(target)
                    partial.rename(target)
                elif conflict == "skip":
                    partial.unlink(missing_ok=True)
                    skipped.append({"name": item.filename, "reason": "An item with that name already exists."})
                    continue
                else:
                    partial.unlink(missing_ok=True)
                    failed.append({"name": item.filename, "error": f"A file or folder named '{target.name}' already exists."})
                    continue
            else:
                partial.rename(target)
            created.append(rel(target))
        except OSError as e:
            try:
                if partial is not None:
                    partial.unlink(missing_ok=True)
            except Exception:
                pass
            failed.append({"name": item.filename, "error": str(e)})
    if not created and failed:
        log_activity("upload_failed", destination or "Files", f"Upload failed for {len(failed)} item(s): {failed[0]['error']}")
        return jsonify(error=failed[0]["error"], failed=failed), 409
    if created:
        details = f"Uploaded {len(created)} item(s)."
        if replaced:
            details += f" Replaced {len(replaced)} existing file(s)."
        if skipped:
            details += f" Skipped {len(skipped)} item(s)."
        if failed:
            details += f" {len(failed)} failed."
        log_activity("upload", ", ".join(created[:20]), details)
    return jsonify(ok=True, uploaded=created, replaced=replaced, skipped=skipped, failed=failed)


@app.post("/api/files/rename")
def rename_file_entry():
    guard = require_perm("rename")
    if guard:
        return guard
    data = request.get_json(silent=True) or {}
    source = safe_any(data.get("path", ""))
    name = valid_new_name(data.get("name"))
    if not name:
        return jsonify(error="Enter a valid new name."), 400
    target = (source.parent / name).resolve()
    try:
        source.relative_to(ROOT.resolve()); target.relative_to(ROOT.resolve())
    except ValueError:
        return jsonify(error="Invalid path."), 403
    if target.exists():
        return jsonify(error="A file or folder with that name already exists."), 409
    old_rel = rel(source)
    new_rel = rel(target)
    try:
        source.rename(target)
        rebase_asset_paths(old_rel, new_rel)
    except OSError as e:
        return jsonify(error=str(e)), 400
    log_activity("rename", f"{old_rel} → {new_rel}", "File/folder renamed.")
    return jsonify(ok=True, path=new_rel)


@app.post("/api/files/delete")
def delete_file_entry():
    guard = require_perm("delete")
    if guard:
        return guard
    data = request.get_json(silent=True) or {}
    target = safe_any(data.get("path", ""))
    if not target.exists():
        return jsonify(error="File or folder not found."), 404
    target_rel = rel(target)
    try:
        manifest = _write_trash_item(target, target_rel)
        remove_asset_paths(target_rel)
    except OSError as e:
        return jsonify(error=str(e)), 400
    log_activity("delete", target_rel, "Moved to Recycle Bin.")
    return jsonify(ok=True, item=manifest)


@app.post("/api/files/delete-bulk")
def delete_file_entries_bulk():
    guard = require_perm("delete")
    if guard:
        return guard
    data = request.get_json(silent=True) or {}
    paths = data.get("paths") or []
    if not isinstance(paths, list) or not paths:
        return jsonify(error="No files or folders selected."), 400
    cleaned = []
    seen = set()
    for raw in paths[:200]:
        try:
            target = safe_any(raw)
        except Exception:
            continue
        relpath = rel(target)
        if not relpath or relpath in seen or not target.exists():
            continue
        seen.add(relpath)
        cleaned.append((target, relpath))
    if not cleaned:
        return jsonify(error="No valid files or folders selected."), 400

    # Avoid trashing a child separately when its parent folder is also selected.
    selected=set(relpath for _,relpath in cleaned)
    cleaned=[x for x in cleaned if not any(x[1].startswith(parent+"/") for parent in selected if parent!=x[1])]
    trashed = []
    failed = []
    for target, relpath in cleaned:
        try:
            manifest = _write_trash_item(target, relpath)
            remove_asset_paths(relpath)
            trashed.append(manifest)
        except OSError as e:
            failed.append({"path": relpath, "error": str(e)})
    if trashed:
        log_activity("delete_bulk", ", ".join(x.get("original_path", "") for x in trashed[:20]), f"Moved {len(trashed)} item(s) to Recycle Bin.")
    return jsonify(ok=True, trashed=trashed, failed=failed)


@app.post("/api/files/transfer")
def transfer_file_entry():
    data = request.get_json(silent=True) or {}
    destination = str(data.get("destination", "")).strip()
    mode = str(data.get("mode", "move")).lower()
    if mode not in {"move", "copy"}:
        return jsonify(error="Invalid transfer mode."), 400
    guard = require_perm(mode)
    if guard:
        return guard
    source = safe_any(data.get("path", ""))
    if not source.exists():
        return jsonify(error="Source not found."), 404
    dest_dir = safe_any(destination, allow_root=True)
    if not dest_dir.is_dir():
        return jsonify(error="Destination folder not found."), 404
    if source.is_dir():
        try:
            dest_dir.relative_to(source.resolve())
            return jsonify(error="A folder cannot be moved or copied inside itself."), 400
        except ValueError:
            pass
    target = (dest_dir / source.name).resolve()
    try:
        source.relative_to(ROOT.resolve()); target.relative_to(ROOT.resolve())
    except ValueError:
        return jsonify(error="Invalid path."), 403
    if target.exists():
        return jsonify(error="A file or folder with that name already exists at the destination."), 409
    old_rel = rel(source)
    new_rel = rel(target)
    try:
        if mode == "copy":
            if source.is_dir():
                shutil.copytree(source, target)
            else:
                shutil.copy2(source, target)
        else:
            shutil.move(str(source), str(target))
            rebase_asset_paths(old_rel, new_rel)
    except OSError as e:
        return jsonify(error=str(e)), 400
    log_activity(mode, f"{old_rel} → {new_rel}", f"File/folder {mode} operation completed.")
    return jsonify(ok=True, path=new_rel, mode=mode)


@app.post("/api/files/transfer-bulk")
def transfer_file_entries_bulk():
    data = request.get_json(silent=True) or {}
    paths = data.get("paths") or []
    destination = str(data.get("destination", "")).strip()
    mode = str(data.get("mode", "move")).lower()
    if mode not in {"move", "copy"}:
        return jsonify(error="Invalid transfer mode."), 400
    guard = require_perm(mode)
    if guard:
        return guard
    if not isinstance(paths, list) or not paths:
        return jsonify(error="No files or folders selected."), 400
    dest_dir = safe_any(destination, allow_root=True)
    if not dest_dir.is_dir():
        return jsonify(error="Destination folder not found."), 404

    sources = []
    seen = set()
    for raw in paths[:200]:
        try:
            source = safe_any(raw)
        except Exception:
            continue
        source_rel = rel(source)
        if source_rel and source_rel not in seen and source.exists():
            seen.add(source_rel)
            sources.append((source, source_rel))
    # A selected folder already contains its selected descendants.
    sources = [item for item in sources if not any(
        item[1].startswith(parent + "/") for _, parent in sources if parent != item[1]
    )]
    if not sources:
        return jsonify(error="No valid files or folders selected."), 400

    transferred = []
    failed = []
    for source, old_rel in sources:
        if source.is_dir():
            try:
                dest_dir.relative_to(source.resolve())
                failed.append({"path": old_rel, "error": "A folder cannot be moved or copied inside itself."})
                continue
            except ValueError:
                pass
        target = (dest_dir / source.name).resolve()
        try:
            target.relative_to(ROOT.resolve())
        except ValueError:
            failed.append({"path": old_rel, "error": "Invalid destination path."})
            continue
        if target.exists():
            failed.append({"path": old_rel, "error": "An item with that name already exists at the destination."})
            continue
        try:
            if mode == "copy":
                if source.is_dir():
                    shutil.copytree(source, target)
                else:
                    shutil.copy2(source, target)
            else:
                shutil.move(str(source), str(target))
                rebase_asset_paths(old_rel, rel(target))
            transferred.append({"from": old_rel, "to": rel(target)})
        except OSError as e:
            failed.append({"path": old_rel, "error": str(e)})

    if transferred:
        log_activity(f"{mode}_bulk", ", ".join(x["from"] for x in transferred[:20]),
                     f"{mode.title()} completed for {len(transferred)} item(s)." + (f" {len(failed)} failed." if failed else ""))
    elif failed:
        log_activity(f"{mode}_failed", destination or "Files", f"{mode.title()} failed: {failed[0]['error']}")
    return jsonify(ok=bool(transferred), transferred=transferred, failed=failed, mode=mode)


@app.get("/api/dashboard")
def dashboard():
    guard = require_perm("dashboard")
    if guard:
        return guard
    c = db()
    scope=current_scope()
    scope_clause,scope_params=scope_sql(scope)
    rows = c.execute("SELECT id,path,name,ext,size,mtime,thumb FROM assets WHERE 1=1"+scope_clause+" ORDER BY mtime DESC LIMIT 12", scope_params).fetchall()
    counts = {
        "media": c.execute("SELECT COUNT(*) FROM assets WHERE 1=1"+scope_clause, scope_params).fetchone()[0],
        "images": c.execute("SELECT COUNT(*) FROM assets WHERE ext IN (?,?,?,?,?,?,?,?,?)"+scope_clause, tuple(sorted(IMAGE_EXTS))+scope_params).fetchone()[0],
        "videos": c.execute("SELECT COUNT(*) FROM assets WHERE ext IN (?,?,?,?,?,?,?,?,?,?)"+scope_clause, tuple(sorted(VIDEO_EXTS))+scope_params).fetchone()[0],
        "audio": c.execute("SELECT COUNT(*) FROM assets WHERE ext IN (?,?,?,?,?,?,?,?)"+scope_clause, tuple(sorted(AUDIO_EXTS))+scope_params).fetchone()[0],
        "embedded": c.execute("SELECT COUNT(*) FROM embeddings e JOIN assets a ON a.id=e.asset_id WHERE 1=1"+scope_clause, scope_params).fetchone()[0],
    }
    c.close()
    try:
        disk = shutil.disk_usage(ROOT)
        disk_data = {"total": disk.total, "used": disk.used, "free": disk.free, "percent": round((disk.used / disk.total) * 100, 1) if disk.total else 0}
    except OSError:
        disk_data = {"total": 0, "used": 0, "free": 0, "percent": 0}
    recycle_items = _read_trash_items()
    # Dashboard filesystem totals. Recycle storage is deliberately excluded so
    # deleted items do not appear as active files/folders.
    total_files = 0
    total_folders = 0
    try:
        recycle_dir = recycle_root().resolve()
        walk_root = (ROOT / scope).resolve() if scope else ROOT.resolve()
        for current, dirs, files in os.walk(walk_root):
            current_path = Path(current)
            try:
                current_path.resolve().relative_to(recycle_dir)
                dirs[:] = []
                continue
            except ValueError:
                pass
            dirs[:] = [d for d in dirs if d != RECYCLE_NAME]
            total_folders += len(dirs)
            total_files += len(files)
    except OSError:
        total_files = 0
        total_folders = 0
    counts["files"] = total_files
    counts["folders"] = total_folders
    recent = [dict(r) for r in rows]
    is_admin = bool(current_user() and current_user()["role"] == "admin")
    if not is_admin:
        for item in recent:
            item.pop("path", None)
    for item in recent:
        item["kind"] = "image" if item["ext"] in IMAGE_EXTS else "video" if item["ext"] in VIDEO_EXTS else "audio"
    active_count=0
    if is_admin:
        c=db(); active_count=c.execute("SELECT COUNT(*) FROM user_sessions WHERE last_seen>=?",(time.time()-ACTIVE_USER_WINDOW,)).fetchone()[0]; c.close()
    return jsonify(root=(str(ROOT) if is_admin else ""),counts=counts,disk=disk_data,recycle={"count":len(recycle_items),"bytes":recycle_size_bytes()},recent=recent,system={"version":APP_VERSION,"app_name":config_value("app_name"),"uptime":max(0,time.time()-SERVER_STARTED_AT),"active_users":active_count,"lan_url":guest_base_url(),"ai_mode":config_value("ai_mode")})

@app.post("/api/admin/ai-enrichment")
def admin_ai_enrichment_start():
    user=current_user()
    if not user or user["role"] != "admin": return jsonify(error="Admin only"),403
    if config_value("ai_mode")!="full": return jsonify(error="AI enrichment is disabled in Standard mode."),409
    with ai_enrich_lock:
        if ai_enrich_state["running"]: return jsonify({**ai_enrich_state,"started":False})
    ok, active = _job_claim("ai_enrichment")
    if not ok:
        return jsonify(error=f"{active.title()} job is currently running. Wait for it to finish first.", active_job=active), 409
    with ai_enrich_lock:
        ai_enrich_state.update(running=True, processed=0, total=0, tagged=0, failed=0, current="", message="Preparing AI enrichment...")
        payload={**ai_enrich_state,"started":True}
    threading.Thread(target=ai_enrichment_worker,args=(current_scope(),),daemon=True).start()
    log_activity("ai_enrichment_started","AI Enrichment","AI visual tag enrichment started.")
    return jsonify(payload)

@app.get("/api/admin/ai-enrichment-status")
def admin_ai_enrichment_status():
    user=current_user()
    if not user or user["role"] != "admin": return jsonify(error="Admin only"),403
    c=db()
    total=c.execute("SELECT COUNT(*) FROM assets WHERE ext IN (?,?,?,?,?,?,?,?,?)",tuple(sorted(IMAGE_EXTS))).fetchone()[0]
    enriched=c.execute("SELECT COUNT(*) FROM assets WHERE ai_tag_attempted=1").fetchone()[0]
    pending=c.execute("SELECT COUNT(*) FROM assets WHERE ext IN (?,?,?,?,?,?,?,?,?) AND ai_tag_attempted=0",tuple(sorted(IMAGE_EXTS))).fetchone()[0]
    tagged=c.execute("SELECT COUNT(*) FROM assets WHERE ai_tags!=''").fetchone()[0]
    c.close()
    with ai_enrich_lock: state=dict(ai_enrich_state)
    return jsonify(**state,total_assets=total,enriched=enriched,pending=pending,tagged_assets=tagged)

@app.post("/api/admin/ai-enrichment-reset")
def admin_ai_enrichment_reset():
    user=current_user()
    if not user or user["role"] != "admin": return jsonify(error="Admin only"),403
    with ai_enrich_lock:
        if ai_enrich_state["running"]: return jsonify(error="Stop the current enrichment first."),409
    c=db(); c.execute("UPDATE assets SET ai_tags='', ai_caption='', ai_tag_attempted=0"); c.commit(); c.close()
    log_activity("ai_enrichment_reset","AI Enrichment","AI visual enrichment was reset for rebuild.")
    return jsonify(ok=True)


@app.get("/api/search")
def search():
    guard=require_perm("search")
    if guard: return guard
    q = request.args.get("q", "").strip()
    media_type = request.args.get("type", "all").strip().lower()
    if session.get("guest_id"):
        log_activity("guest_search", q or "Library", f"Guest searched the library ({media_type}).")
    sort_mode = request.args.get("sort", "modified-desc").strip().lower()
    try:
        limit = min(max(int(request.args.get("limit", 80)), 1), 200)
        offset = max(int(request.args.get("offset", 0)), 0)
    except ValueError:
        limit, offset = 80, 0
    type_exts = {"image": IMAGE_EXTS, "video": set(VIDEO_EXTS), "audio": set(AUDIO_EXTS)}.get(media_type)
    c = db()
    type_clause = ""
    type_params = ()
    if type_exts:
        type_clause = " AND ext IN ({})".format(",".join("?" for _ in type_exts))
        type_params = tuple(sorted(type_exts))

    filter_clause=""; filter_params=[]
    requested_ext=str(request.args.get("ext","")).strip().lower()
    if requested_ext:
        if not requested_ext.startswith("."): requested_ext="."+requested_ext
        if re.fullmatch(r"\.[a-z0-9]{1,12}",requested_ext):
            filter_clause+=" AND ext=?"; filter_params.append(requested_ext)
    folder_filter=str(request.args.get("folder","")).strip().replace("\\","/").strip("/")
    if folder_filter:
        try: folder_filter=normalize_scope(folder_filter)
        except ValueError: return jsonify(error="Invalid folder filter."),400
        scope_now=current_scope()
        if scope_now and not (folder_filter==scope_now or folder_filter.startswith(scope_now+"/")):
            return jsonify(error="Folder filter is outside your accessible directory."),403
        filter_clause+=" AND (path=? OR path LIKE ?)"; filter_params.extend([folder_filter,folder_filter+"/%"])
    for key,operator,column in (("date_from",">=","mtime"),("date_to","<=","mtime"),("min_size",">=","size"),("max_size","<=","size")):
        raw=request.args.get(key,"")
        if raw not in (None,""):
            try: value=float(raw)
            except (TypeError,ValueError): return jsonify(error=f"Invalid {key} filter."),400
            filter_clause+=f" AND {column}{operator}?"; filter_params.append(value)
    filter_params=tuple(filter_params)

    allowed = session_has_perm("download")
    scope=current_scope()
    scope_clause,scope_params=scope_sql(scope)
    if not q:
        total = c.execute("SELECT COUNT(*) FROM assets WHERE 1=1" + type_clause + filter_clause + scope_clause, type_params + filter_params + scope_params).fetchone()[0]
        order_map = {
            "modified-desc": "mtime DESC, id DESC",
            "modified-asc": "mtime ASC, id ASC",
            "added-desc": "id DESC",
            "added-asc": "id ASC",
            "name-asc": "lower(name) ASC, id DESC",
            "name-desc": "lower(name) DESC, id DESC",
            "size-desc": "size DESC, id DESC",
            "size-asc": "size ASC, id DESC",
        }
        order_sql = order_map.get(sort_mode, order_map["modified-desc"])
        rows = c.execute(
            "SELECT * FROM assets WHERE 1=1" + type_clause + filter_clause + scope_clause + f" ORDER BY {order_sql} LIMIT ? OFFSET ?",
            type_params + filter_params + scope_params + (limit, offset)
        ).fetchall()
        c.close()
        response = jsonify([dict(x, download_allowed=allowed) for x in rows])
        response.headers["X-Total-Count"] = str(total)
        response.headers["X-Offset"] = str(offset)
        response.headers["X-Limit"] = str(limit)
        response.headers["X-Has-More"] = "1" if offset + len(rows) < total else "0"
        return response

    # Hybrid search: semantic AI + filename + folder/path relevance.
    like = f"%{q}%"
    lexical = c.execute(
        "SELECT * FROM assets WHERE (name LIKE ? OR path LIKE ? OR ocr_text LIKE ? OR ai_tags LIKE ? OR ai_caption LIKE ?)" + type_clause + filter_clause + scope_clause + " LIMIT ?",
        (like, like, like, like, like) + type_params + filter_params + scope_params + (min(max(limit * 5, 200), LEXICAL_CANDIDATE_LIMIT),)
    ).fetchall()
    embedded = c.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
    c.close()

    merged = {}
    qf = q.casefold()
    # Multiple query words are useful for filenames/folders such as
    # 'Halloween model', 'pink lipstick', or 'moonlight magic'.
    query_terms = [t for t in re.split(r"[^\w]+", qf) if len(t) > 1]

    def color_score_for_query(query_text, hex_value):
        if not hex_value or not re.fullmatch(r"#[0-9a-fA-F]{6}", str(hex_value)):
            return 0.0
        rgb = tuple(int(hex_value[i:i+2], 16) for i in (1, 3, 5))
        targets = {
            "red": (220, 45, 55), "pink": (235, 120, 165), "purple": (135, 70, 180),
            "violet": (120, 70, 180), "blue": (60, 110, 210), "green": (70, 150, 85),
            "yellow": (225, 195, 55), "orange": (225, 125, 45), "brown": (125, 80, 50),
            "black": (25, 25, 25), "white": (235, 235, 235), "gray": (135, 135, 135),
            "grey": (135, 135, 135), "beige": (210, 190, 155), "gold": (200, 165, 55),
            "silver": (175, 180, 185),
        }
        best = 0.0
        for word, target in targets.items():
            if re.search(r"\b" + re.escape(word) + r"\b", query_text):
                dist = sum((rgb[i] - target[i]) ** 2 for i in range(3)) ** 0.5
                best = max(best, max(0.0, 1.0 - dist / 441.67))
        return best

    def lexical_scores(row):
        name = (row["name"] or "").casefold()
        path = (row["path"] or "").casefold()
        folder_path = path.rsplit("/", 1)[0] if "/" in path else path.rsplit("\\", 1)[0] if "\\" in path else path
        name_exact = 1.0 if name == qf else 0.0
        name_start = 1.0 if name.startswith(qf) else 0.0
        name_contains = 1.0 if qf in name else 0.0
        name_terms = (sum(1 for t in query_terms if t in name) / len(query_terms)) if query_terms else 0.0
        path_terms = (sum(1 for t in query_terms if t in folder_path) / len(query_terms)) if query_terms else 0.0
        ocr = (row["ocr_text"] or "").casefold()
        ai_tags_text = (row["ai_tags"] or "").casefold()
        ai_caption = (row["ai_caption"] or "").casefold()
        ocr_contains = 1.0 if qf in ocr else 0.0
        ocr_terms = (sum(1 for t in query_terms if t in ocr) / len(query_terms)) if query_terms else 0.0
        ocr_score = max(ocr_contains * 0.95, ocr_terms * 0.85)
        tag_terms = (sum(1 for t in query_terms if t in ai_tags_text or t in ai_caption) / len(query_terms)) if query_terms else 0.0
        ai_text_score = max((1.0 if qf in ai_tags_text or qf in ai_caption else 0.0) * 0.95, tag_terms * 0.85)
        color_score = color_score_for_query(qf, row["dominant_color"] or "")
        filename_score = max(name_exact, name_start * 0.95, name_contains * 0.85, name_terms * 0.80)
        path_score = max(path_terms, (1.0 if qf in folder_path else 0.0) * 0.9)
        return filename_score, path_score, ocr_score, color_score, ai_text_score

    for row in lexical:
        item = dict(row, download_allowed=allowed)
        filename_score, path_score, ocr_score, color_score, ai_text_score = lexical_scores(row)
        # Phase 2 combines semantic AI, filename/path, OCR, and lightweight color matching.
        lexical_score = 0.15 * filename_score + 0.10 * path_score + 0.15 * ocr_score + 0.05 * color_score + 0.05 * ai_text_score
        merged[row["id"]] = {
            **item,
            "score": lexical_score,
            "filename_score": filename_score,
            "path_score": path_score,
            "ocr_score": ocr_score,
            "color_score": color_score,
            "ai_text_score": ai_text_score,
            "ai_tags": json.loads(row["ai_tags"] or "[]") if row["ai_tags"] else [],
            "ai_caption": row["ai_caption"] or "",
            "ai_score": None,
            "ai_cosine": None,
            "match_sources": (["filename"] if filename_score > 0 else []) + (["folder"] if path_score > 0 else []) + (["ocr"] if ocr_score > 0 else []) + (["color"] if color_score > 0 else []) + (["ai_tags"] if ai_text_score > 0 else [])
        }

    if embedded and has_perm("ai_search") and config_value("ai_mode")=="full":
        try:
            # Fetch a wider semantic candidate pool so lexical matches and
            # semantic matches have a better chance to be merged.
            ai_items = semantic(q, max(limit * 3, 120), scope)
            for ai in ai_items:
                aid = ai["id"]
                ai_score = float(ai.get("score", 0.0))
                if aid in merged:
                    merged[aid]["ai_score"] = ai_score
                    merged[aid]["ai_cosine"] = float(ai.get("ai_cosine", 0.0))
                    merged[aid]["score"] = (0.50 * ai_score) + (0.15 * float(merged[aid].get("filename_score") or 0.0)) + (0.10 * float(merged[aid].get("path_score") or 0.0)) + (0.15 * float(merged[aid].get("ocr_score") or 0.0)) + (0.05 * float(merged[aid].get("color_score") or 0.0)) + (0.05 * float(merged[aid].get("ai_text_score") or 0.0))
                    if "ai" not in merged[aid]["match_sources"]:
                        merged[aid]["match_sources"].append("ai")
                else:
                    # AI-only result. No filename/path contribution.
                    merged[aid] = {
                        **ai,
                        "download_allowed": allowed,
                        "filename_score": 0.0,
                        "path_score": 0.0,
                        "ocr_score": 0.0,
                        "color_score": 0.0,
                        "ai_text_score": 0.0,
                        "ai_tags": ai.get("ai_tags", []),
                        "ai_caption": ai.get("ai_caption", ""),
                        "ai_score": ai_score,
                        "score": 0.50 * ai_score,
                        "match_sources": ["ai"]
                    }
        except Exception:
            pass

    results = list(merged.values())
    if requested_ext: results=[x for x in results if str(x.get("ext") or "").lower()==requested_ext]
    if folder_filter: results=[x for x in results if str(x.get("path") or "").replace("\\","/")==folder_filter or str(x.get("path") or "").replace("\\","/").startswith(folder_filter+"/")]
    try:
        if request.args.get("date_from","")!="": results=[x for x in results if float(x.get("mtime") or 0)>=float(request.args["date_from"])]
        if request.args.get("date_to","")!="": results=[x for x in results if float(x.get("mtime") or 0)<=float(request.args["date_to"])]
        if request.args.get("min_size","")!="": results=[x for x in results if float(x.get("size") or 0)>=float(request.args["min_size"])]
        if request.args.get("max_size","")!="": results=[x for x in results if float(x.get("size") or 0)<=float(request.args["max_size"])]
    except (TypeError,ValueError):
        pass
    # For a non-empty search, relevance is the useful default. Explicit
    # alphabetical/size/date sorts remain available from the existing UI.
    if sort_mode in {"name-asc", "name-desc", "size-desc", "size-asc", "modified-asc", "modified-desc", "added-asc", "added-desc"} and request.args.get("sort") not in (None, "", "relevance"):
        reverse = sort_mode.endswith("desc")
        if sort_mode.startswith("modified"):
            results.sort(key=lambda x: (float(x.get("mtime") or 0), int(x.get("id") or 0)), reverse=reverse)
        elif sort_mode.startswith("added"):
            results.sort(key=lambda x: int(x.get("id") or 0), reverse=reverse)
        elif sort_mode.startswith("name"):
            results.sort(key=lambda x: (str(x.get("name") or "").casefold(), int(x.get("id") or 0)), reverse=reverse)
        else:
            results.sort(key=lambda x: (int(x.get("size") or 0), int(x.get("id") or 0)), reverse=reverse)
    else:
        results.sort(key=lambda x: (float(x.get("score") or 0.0), float(x.get("ai_score") or 0.0)), reverse=True)
    results = results[:limit]
    response = jsonify(results)
    response.headers["X-Total-Count"] = str(len(merged))
    response.headers["X-Offset"] = "0"
    response.headers["X-Limit"] = str(limit)
    response.headers["X-Has-More"] = "0"
    return response



def ocr_backfill_worker(scope=""):
    global ocr_state
    c = None
    try:
        if not _tesseract_executable():
            with ocr_lock:
                ocr_state.update(running=False, message="Tesseract is not available.", current="")
            return

        scope_clause, scope_params = scope_sql(scope)
        c = db()
        rows = c.execute(
            "SELECT id,path,name FROM assets WHERE ext IN ('.jpg','.jpeg','.png','.webp','.gif','.bmp','.tif','.tiff','.avif') "
            "AND ocr_attempted=0" + scope_clause + " ORDER BY id ASC",
            scope_params,
        ).fetchall()

        with ocr_lock:
            ocr_state.update(
                running=True, processed=0, total=len(rows), ocr_found=0,
                failed=0, current="", message="OCR backfill running..."
            )

        for row in rows:
            with ocr_lock:
                ocr_state["current"] = row["path"]
            try:
                file_path = ROOT / row["path"]
                if not file_path.is_file() or is_in_recycle(file_path):
                    c.execute("UPDATE assets SET ocr_attempted=1, ocr_text='' WHERE id=?", (row["id"],))
                else:
                    _, _, _, _, text_value = image_metadata(file_path)
                    text_value = text_value or ""
                    c.execute(
                        "UPDATE assets SET ocr_text=?, ocr_attempted=1 WHERE id=?",
                        (text_value[:12000], row["id"]),
                    )
                    with ocr_lock:
                        if text_value:
                            ocr_state["ocr_found"] += 1
            except Exception:
                c.execute("UPDATE assets SET ocr_attempted=1 WHERE id=?", (row["id"],))
                with ocr_lock:
                    ocr_state["failed"] += 1

            with ocr_lock:
                ocr_state["processed"] += 1
                _ocr_processed = ocr_state["processed"]
                _ocr_total = ocr_state["total"]
                _ocr_found = ocr_state["ocr_found"]
                _ocr_failed = ocr_state["failed"]
            if _ocr_processed % 10 == 0:
                c.commit()
            if _ocr_processed % 25 == 0 or _ocr_processed == _ocr_total:
                server_event(f"OCR progress: {_ocr_processed}/{_ocr_total} processed · {_ocr_found} text found · {_ocr_failed} failed.", "OCR")

        c.commit()
        with ocr_lock:
            ocr_state.update(running=False, current="", message=f"OCR backfill complete: {ocr_state['processed']} processed, {ocr_state['ocr_found']} with text.")
        log_activity("ocr_backfill_completed", "OCR", f"OCR backfill completed: {len(rows)} asset(s) processed.")
    except Exception as exc:
        try:
            if c:
                c.commit()
        except Exception:
            pass
        with ocr_lock:
            ocr_state.update(running=False, current="", message=f"OCR backfill failed: {exc}")
    finally:
        if c:
            try:
                c.close()
            except Exception:
                pass


@app.get("/api/admin/jobs")
def admin_jobs():
    user = current_user()
    if not user or user["role"] != "admin":
        return jsonify(error="Admin only"), 403
    with scan_lock: scan = dict(scan_state)
    with ai_lock: ai = dict(ai_state)
    with thumb_lock: thumb = dict(thumb_state)
    with ocr_lock: ocr = dict(ocr_state)
    with ai_enrich_lock: ai_enrich = dict(ai_enrich_state)
    with job_manager_lock:
        manager = dict(job_manager)
        manager["queued"] = list(job_manager.get("queued", []))
    running = [manager["active"]] if manager.get("active") else []
    histories = {name:_job_history(name, 3) for name in ("scan","ai","thumbnails","ocr","ai_enrichment")}
    return jsonify(running=running, count=len(running), active_job=manager.get("active",""), manager=manager, jobs={"scan":scan,"ai":ai,"thumbnails":thumb,"ocr":ocr,"ai_enrichment":ai_enrich}, history=histories, performance={"lexical_candidate_limit":LEXICAL_CANDIDATE_LIMIT,"ai_candidate_limit":AI_SEARCH_CANDIDATE_LIMIT,"ai_batch_size":AI_BATCH_SIZE,"max_heavy_jobs":MAX_HEAVY_JOBS})


@app.post("/api/admin/jobs/control")
def admin_jobs_control():
    user = current_user()
    if not user or user["role"] != "admin":
        return jsonify(error="Admin only"), 403
    data=request.get_json(silent=True) or {}
    action=str(data.get("action","")).lower().strip()
    with job_manager_lock:
        active=job_manager.get("active") or ""
    if not active:
        return jsonify(error="No background job is currently running."),409
    if action == "pause":
        job_pause_event.set()
        log_activity("job_paused", active, f"Background job paused: {active}.")
        return jsonify(ok=True, active_job=active, paused=True)
    if action == "resume":
        job_pause_event.clear()
        log_activity("job_resumed", active, f"Background job resumed: {active}.")
        return jsonify(ok=True, active_job=active, paused=False)
    if action == "stop":
        job_stop_event.set()
        job_pause_event.clear()
        log_activity("job_stop_requested", active, f"Stop requested for background job: {active}.")
        return jsonify(ok=True, active_job=active, stop_requested=True)
    return jsonify(error="Invalid action. Use pause, resume or stop."),400


@app.post("/api/admin/ocr-backfill")
def admin_ocr_backfill():
    user = current_user()
    if not user or user["role"] != "admin":
        return jsonify(error="Admin only"), 403
    if not _tesseract_executable():
        return jsonify(ok=False, error="Tesseract is not available."), 400
    with scan_lock:
        if scan_state["running"]:
            return jsonify(ok=False, error="A library scan is currently running. Wait for it to finish before starting OCR backfill."), 409
    with ocr_lock:
        if ocr_state["running"]:
            return jsonify({**ocr_state, "started": False})
    ok, active = _job_claim("ocr")
    if not ok:
        return jsonify(ok=False, error=f"{active.title()} job is currently running. Wait for it to finish first.", active_job=active), 409
    with ocr_lock:
        ocr_state.update(running=True, paused=False, stopped=False, processed=0, total=0, ocr_found=0, failed=0, current="", message="Preparing OCR backfill...")
        payload = {**ocr_state, "started": True}
    threading.Thread(target=ocr_backfill_worker, args=(current_scope(),), daemon=True).start()
    log_activity("ocr_backfill_started", "OCR", "OCR backfill started.")
    return jsonify(payload)


@app.get("/api/admin/ocr-backfill-status")
def admin_ocr_backfill_status():
    user = current_user()
    if not user or user["role"] != "admin":
        return jsonify(error="Admin only"), 403
    with ocr_lock:
        return jsonify(dict(ocr_state))


@app.get("/api/admin/metadata-status")
def metadata_status():
    if not current_user() or current_user()["role"] != "admin":
        return jsonify(error="Admin only"), 403
    c = db()
    total = c.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
    meta = c.execute("SELECT COUNT(*) FROM assets WHERE width>0 AND height>0 AND orientation!=''").fetchone()[0]
    ocr = c.execute("SELECT COUNT(*) FROM assets WHERE ocr_text!=''").fetchone()[0]
    attempted = c.execute("SELECT COUNT(*) FROM assets WHERE ocr_attempted=1").fetchone()[0]
    pending = c.execute("SELECT COUNT(*) FROM assets WHERE ext IN ('.jpg','.jpeg','.png','.webp','.gif','.bmp','.tif','.tiff','.avif') AND ocr_attempted=0").fetchone()[0]
    c.close()
    return jsonify(total=total, metadata=meta, ocr=ocr, ocr_attempted=attempted, ocr_pending=pending, tesseract=bool(_tesseract_executable()))


@app.get("/api/admin/activity-log")
def activity_log_api():
    if not current_user() or current_user()["role"] != "admin":
        return jsonify(error="Admin only"), 403
    try:
        limit=max(10,min(int(request.args.get("limit",50)),200))
    except Exception:
        limit=50
    try:
        offset=max(0,int(request.args.get("offset",0)))
    except Exception:
        offset=0
    action=str(request.args.get("action","")).strip()
    username=str(request.args.get("username","")).strip()
    q=str(request.args.get("q","")).strip()
    try:
        days=max(0,min(int(request.args.get("days",0)),3650))
    except Exception:
        days=0
    where=[]; params=[]
    if action:
        where.append("action=?"); params.append(action)
    if username:
        where.append("username=?"); params.append(username)
    if q:
        like=f"%{q}%"; where.append("(username LIKE ? OR action LIKE ? OR target LIKE ? OR details LIKE ?)"); params.extend([like]*4)
    if days:
        where.append("timestamp>=?"); params.append(time.time()-days*86400)
    clause=(" WHERE "+" AND ".join(where)) if where else ""
    c=db()
    total=c.execute("SELECT COUNT(*) FROM activity_log"+clause,params).fetchone()[0]
    rows=c.execute("SELECT id,timestamp,username,role,action,target,details,ip,user_agent FROM activity_log"+clause+" ORDER BY timestamp DESC LIMIT ? OFFSET ?",params+[limit,offset]).fetchall()
    actions=[r[0] for r in c.execute("SELECT DISTINCT action FROM activity_log ORDER BY action").fetchall()]
    users=[r[0] for r in c.execute("SELECT DISTINCT username FROM activity_log WHERE username<>'' ORDER BY username").fetchall()]
    c.close()
    return jsonify(total=total,offset=offset,limit=limit,has_more=offset+len(rows)<total,actions=actions,users=users,items=[dict(r) for r in rows])

@app.post("/api/admin/activity-log/clear")
def clear_activity_log():
    if not current_user() or current_user()["role"] != "admin":
        return jsonify(error="Admin only"), 403
    c = db()
    cur = c.execute("DELETE FROM activity_log")
    deleted = cur.rowcount
    c.commit(); c.close()
    return jsonify(ok=True, deleted=deleted)

@app.get("/api/admin/activity-log/export")
def export_activity_log():
    if not current_user() or current_user()["role"] != "admin":
        return jsonify(error="Admin only"), 403
    import csv, io
    action=str(request.args.get("action","")).strip()
    username=str(request.args.get("username","")).strip()
    q=str(request.args.get("q","")).strip()
    try: days=max(0,min(int(request.args.get("days",0)),3650))
    except Exception: days=0
    where=[]; params=[]
    if action: where.append("action=?"); params.append(action)
    if username: where.append("username=?"); params.append(username)
    if q:
        like=f"%{q}%"; where.append("(username LIKE ? OR action LIKE ? OR target LIKE ? OR details LIKE ?)"); params.extend([like]*4)
    if days: where.append("timestamp>=?"); params.append(time.time()-days*86400)
    clause=(" WHERE "+" AND ".join(where)) if where else ""
    c=db(); rows=c.execute("SELECT timestamp,username,role,action,target,details,ip,user_agent FROM activity_log"+clause+" ORDER BY timestamp DESC",params).fetchall(); c.close()
    out=io.StringIO(); w=csv.writer(out); w.writerow(["Time","User","Role","Action","Target","Details","IP","User Agent"])
    for r in rows: w.writerow([time.strftime("%Y-%m-%d %H:%M:%S",time.localtime(r[0])),r[1],r[2],r[3],r[4],r[5],r[6],r[7]])
    from flask import Response
    return Response(out.getvalue(), mimetype="text/csv", headers={"Content-Disposition":"attachment; filename=home-cloud-activity-log.csv"})

@app.get("/api/admin/system-health")
def admin_system_health():
    if not current_user() or current_user()["role"] != "admin": return jsonify(error="Admin only"), 403
    checks=[]
    def add(name, ok, detail): checks.append({"name":name,"ok":bool(ok),"detail":str(detail)})
    add("Database", DB.exists(), "assets.db present" if DB.exists() else "Database file missing")
    try:
        c=db(); quick=c.execute("PRAGMA quick_check").fetchone()[0]; c.close(); add("SQLite integrity", quick=="ok", quick)
    except Exception as e: add("SQLite integrity", False, e)
    add("Configuration", CONFIG.exists(), "config.json present" if CONFIG.exists() else "config.json missing")
    add("Thumbnail cache", THUMBS.exists(), "Available" if THUMBS.exists() else "Missing")
    try:
        usage=shutil.disk_usage(ROOT); pct=round((usage.used/usage.total)*100,1) if usage.total else 0; add("Storage", pct < 95, f"{pct}% used · {usage.free} bytes free")
    except Exception as e: add("Storage", False, e)
    add("Asset root", ROOT.exists() and ROOT.is_dir(), "Available" if ROOT.exists() else "Unavailable")
    add("Asset root writable", os.access(ROOT,os.W_OK) if ROOT.exists() else False, "Writable" if ROOT.exists() and os.access(ROOT,os.W_OK) else "Write access unavailable")
    lan_ip=detect_lan_ip(); add("LAN address",bool(lan_ip),lan_ip or "No non-loopback IPv4 address detected")
    add("Server port",1024<=int(config_value("port") or 8080)<=65535,str(config_value("port") or 8080))
    add("Tesseract", bool(_tesseract_executable()), "Available" if _tesseract_executable() else "Not detected")
    ai_mode=str(config_value("ai_mode") or "full")
    if ai_mode=="full":
        try: import open_clip; ai_detail="Full AI packages available"
        except Exception: ai_detail="Full mode selected, but open_clip is unavailable"
        add("AI mode",ai_detail.startswith("Full AI packages"),ai_detail)
    else: add("AI mode",True,"Standard mode; heavy AI packages are optional")
    return jsonify(ok=all(x["ok"] for x in checks),version=APP_VERSION,checked_at=time.time(),checks=checks,lan_url=(f"http://{lan_ip}:{config_value('port')}" if lan_ip else ""),internet_required=False,ai_mode=ai_mode)


@app.get("/api/admin/lan-qr")
def admin_lan_qr():
    user=current_user()
    if not user or user["role"]!="admin": return jsonify(error="Admin only"),403
    try:
        import io, qrcode
        url=guest_base_url()+"/"
        image=qrcode.make(url)
        output=io.BytesIO(); image.save(output,format="PNG"); output.seek(0)
        return send_file(output,mimetype="image/png",max_age=0)
    except Exception as exc:
        return jsonify(error=f"QR generation unavailable: {exc}"),503


@app.get("/api/admin/diagnostics-export")
def admin_diagnostics_export():
    """Export support information without the database, passwords or secret key."""
    user=current_user()
    if not user or user["role"]!="admin": return jsonify(error="Admin only"),403
    import io, platform, zipfile
    report={"generated_at":time.time(),"version":APP_VERSION,"python":sys.version,"platform":platform.platform(),"runtime_offline":True,"lan_ip":detect_lan_ip(),"settings":{"app_name":config_value("app_name"),"port":config_value("port"),"ai_mode":config_value("ai_mode"),"thumbnail_quality":config_value("thumbnail_quality"),"chat_enabled":bool(config_value("chat_enabled")),"asset_root":str(ROOT)}}
    try:
        c=db()
        report["database_integrity"]=c.execute("PRAGMA quick_check").fetchone()[0]
        report["counts"]={"assets":c.execute("SELECT COUNT(*) FROM assets").fetchone()[0],"users":c.execute("SELECT COUNT(*) FROM users").fetchone()[0],"active_guests":c.execute("SELECT COUNT(*) FROM guest_tokens WHERE active=1 AND expires_at>?",(time.time(),)).fetchone()[0],"chat_messages":c.execute("SELECT COUNT(*) FROM chat_messages WHERE deleted_at=0").fetchone()[0]}
        c.close()
    except Exception as exc: report["database_error"]=str(exc)
    try:
        usage=shutil.disk_usage(ROOT); report["storage"]={"total":usage.total,"used":usage.used,"free":usage.free}
    except OSError as exc: report["storage_error"]=str(exc)
    with _server_events_lock:
        event_lines=[f"{time.strftime('%Y-%m-%d %H:%M:%S',time.localtime(x['time']))} [{x['level']}] {x['message']}" for x in _server_events[-300:]]
    payload=io.BytesIO()
    with zipfile.ZipFile(payload,"w",zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("diagnostics.json",json.dumps(report,indent=2,ensure_ascii=False))
        archive.writestr("server-events.txt","\n".join(event_lines)+("\n" if event_lines else ""))
        archive.writestr("README.txt","No database, password hash, session cookie, secret key, thumbnail or original asset is included.\n")
    payload.seek(0)
    log_activity("diagnostics_export","System diagnostics","Exported privacy-safe diagnostic information.")
    return send_file(payload,as_attachment=True,download_name=f"home-cloud-diagnostics-{time.strftime('%Y%m%d-%H%M%S')}.zip",mimetype="application/zip",max_age=0)


@app.post("/api/admin/sessions/cleanup")
def admin_cleanup_sessions():
    if not current_user() or current_user()["role"] != "admin": return jsonify(error="Admin only"), 403
    cutoff=time.time()-ACTIVE_USER_WINDOW; c=db(); cur=c.execute("DELETE FROM user_sessions WHERE last_seen<?",(cutoff,)); deleted=cur.rowcount; c.commit(); c.close()
    log_activity("sessions_cleanup","User sessions",f"Removed {deleted} stale session record(s)."); server_event(f"Removed {deleted} stale session record(s).")
    return jsonify(ok=True,deleted=deleted)


@app.delete("/api/admin/sessions/<int:sid>")
def admin_revoke_session(sid):
    actor=current_user()
    if not actor or actor["role"]!="admin": return jsonify(error="Admin only"),403
    c=db(); row=c.execute("SELECT s.id,s.session_key,u.username FROM user_sessions s JOIN users u ON u.id=s.user_id WHERE s.id=?",(sid,)).fetchone()
    if not row: c.close(); return jsonify(error="Session not found"),404
    if row["session_key"]==session.get("activity_sid"):
        c.close(); return jsonify(error="You cannot revoke the session you are currently using."),400
    c.execute("INSERT OR REPLACE INTO revoked_sessions(session_key,revoked_at,revoked_by) VALUES(?,?,?)",(row["session_key"],time.time(),actor["id"]))
    c.execute("DELETE FROM user_sessions WHERE id=?",(sid,)); c.commit(); c.close()
    log_activity("session_revoked",row["username"],"Administrator revoked an active session.")
    return jsonify(ok=True)


@app.get("/api/admin/server-console")
def admin_server_console():
    if not current_user() or current_user()["role"] != "admin": return jsonify(error="Admin only"), 403
    try: limit=max(20,min(int(request.args.get("limit",200)),500))
    except Exception: limit=200
    with _server_events_lock: events=list(_server_events[-limit:])
    with job_manager_lock:
        active_job = job_manager.get("active") or ""
        job_started_at = job_manager.get("started_at") or 0
    return jsonify(version=APP_VERSION,events=events,server_time=time.time(),active_job=active_job,job_started_at=job_started_at)


@app.post("/api/admin/server-console/clear")
def clear_server_console():
    if not current_user() or current_user()["role"] != "admin": return jsonify(error="Admin only"), 403
    with _server_events_lock: _server_events.clear()
    server_event("Server console cleared by administrator."); log_activity("server_console_cleared","Server Console","In-memory console cleared.")
    return jsonify(ok=True)


@app.get("/api/admin/app-backup")
def admin_app_backup():
    if not current_user() or current_user()["role"] != "admin": return jsonify(error="Admin only"), 403
    import tempfile, zipfile
    tmp=tempfile.NamedTemporaryFile(prefix="home-cloud-backup-",suffix=".zip",delete=False); tmp.close(); db_copy=Path(tmp.name).with_suffix(".db")
    try:
        src=sqlite3.connect(DB,timeout=30); dst=sqlite3.connect(db_copy); src.backup(dst); dst.close(); src.close()
        manifest={"app":"Local Visual Asset Library","version":APP_VERSION,"created_at":time.time(),"includes":["consistent SQLite database snapshot","config.json","README.md if present"],"excludes":["asset library originals","thumbnail cache"]}
        with zipfile.ZipFile(tmp.name,"w",zipfile.ZIP_DEFLATED) as z:
            z.write(db_copy,"assets.db")
            if CONFIG.exists(): z.write(CONFIG,"config.json")
            if (APP_DIR/"README.md").exists(): z.write(APP_DIR/"README.md","README.md")
            z.writestr("backup-manifest.json",json.dumps(manifest,indent=2))
        db_copy.unlink(missing_ok=True)
        backup_path=Path(tmp.name)
        @after_this_request
        def _cleanup_backup(response):
            try: backup_path.unlink(missing_ok=True)
            except Exception: pass
            return response
        log_activity("app_backup","Home Cloud","Created consistent application backup archive."); server_event("Application backup created.")
        return send_file(backup_path,as_attachment=True,download_name=f"home-cloud-backup-{time.strftime('%Y%m%d-%H%M%S')}.zip",mimetype="application/zip",max_age=0)
    except Exception as e:
        db_copy.unlink(missing_ok=True); Path(tmp.name).unlink(missing_ok=True); return jsonify(error=f"Backup failed: {e}"),500


@app.post("/api/admin/database-restore")
def admin_database_restore():
    user=current_user()
    if not user or user["role"]!="admin": return jsonify(error="Admin only"),403
    uploaded=request.files.get("database")
    if not uploaded or not str(uploaded.filename or "").lower().endswith((".db",".sqlite",".sqlite3")):
        return jsonify(error="Choose a Home Cloud SQLite database backup."),400
    import tempfile
    temp_fd,temp_name=tempfile.mkstemp(prefix="home-cloud-restore-",suffix=".db"); os.close(temp_fd); temp=Path(temp_name)
    safety=DB.with_name(f"{DB.stem}.pre-restore-{time.strftime('%Y%m%d-%H%M%S')}{DB.suffix}")
    try:
        uploaded.save(temp)
        probe=sqlite3.connect(temp); quick=probe.execute("PRAGMA quick_check").fetchone()[0]
        required={row[0] for row in probe.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}; probe.close()
        if quick!="ok" or not {"users","assets"}.issubset(required):
            return jsonify(error="The selected database is invalid or is not a Home Cloud backup."),400
        src=sqlite3.connect(DB); dst=sqlite3.connect(safety); src.backup(dst); dst.close(); src.close()
        os.replace(temp,DB); init_db(); session.clear()
        server_event(f"Database restored. Safety backup: {safety.name}","WARN")
        return jsonify(ok=True,safety_backup=safety.name,relogin_required=True)
    except Exception as exc:
        return jsonify(error=f"Database restore failed: {exc}"),500
    finally:
        temp.unlink(missing_ok=True)


@app.post("/api/chat/join")
def chat_join():
    if not is_lan_request():
        return jsonify(error="LAN chat is available only on the local network."), 403
    if not config_value("chat_enabled"):
        return jsonify(error="LAN chat is disabled by the administrator."), 403
    if session.get("guest_id") and not config_value("chat_allow_guests"):
        return jsonify(error="Guest chat access is disabled."),403
    if current_user() or session.get("guest_id"):
        identity = chat_identity()
        if identity:
            touch_chat_presence(identity)
            return jsonify(ok=True, identity={**identity, "avatar": chat_avatar(identity)})
    if not config_value("chat_allow_anonymous"):
        return jsonify(error="Anonymous chat access is disabled. Sign in to continue."),403
    data = request.get_json(silent=True) or {}
    name = re.sub(r"\s+", " ", str(data.get("name") or "").strip())
    if not name:
        identity = ensure_anonymous_chat_identity()
        touch_chat_presence(identity)
        return jsonify(ok=True, identity={**identity, "avatar": chat_avatar(identity)})
    if not 2 <= len(name) <= 30:
        return jsonify(error="Enter a chat name between 2 and 30 characters, or leave it blank for a random name."), 400
    if any(ord(ch) < 32 for ch in name) or name.casefold() in {"admin", "administrator", "system", "home cloud", "guest"}:
        return jsonify(error="Choose a different chat name."), 400
    session["chat_name"] = name
    session["chat_session_id"] = session.get("chat_session_id") or uuid.uuid4().hex
    identity = chat_identity()
    touch_chat_presence(identity)
    return jsonify(ok=True, identity={**identity, "avatar": chat_avatar(identity)})


@app.get("/api/chat/state")
def chat_state():
    if not is_lan_request():
        return jsonify(error="LAN chat is available only on the local network."), 403
    if not config_value("chat_enabled"):
        return jsonify(error="LAN chat is disabled by the administrator."),403
    if session.get("guest_id") and not config_value("chat_allow_guests"):
        return jsonify(error="Guest chat access is disabled."),403
    if not current_user() and not session.get("guest_id") and not config_value("chat_allow_anonymous"):
        return jsonify(error="Anonymous chat access is disabled. Sign in to continue."),403
    identity = chat_identity()
    if not identity:
        identity = ensure_anonymous_chat_identity()
    touch_chat_presence(identity)
    try:
        after_id = max(0, int(request.args.get("after", 0)))
    except (TypeError, ValueError):
        after_id = 0
    c = db()
    retention_days=max(1,min(int(config_value("chat_retention_days") or 30),3650))
    c.execute("DELETE FROM chat_messages WHERE created_at<?",(time.time()-retention_days*86400,))
    c.commit()
    if after_id:
        rows = c.execute("""SELECT id,sender_type,sender_key,display_name,message,created_at,deleted_at
                            FROM chat_messages WHERE id>? ORDER BY id LIMIT 200""", (after_id,)).fetchall()
    else:
        rows = c.execute("""SELECT * FROM (SELECT id,sender_type,sender_key,display_name,message,created_at,deleted_at
                            FROM chat_messages ORDER BY id DESC LIMIT 100) ORDER BY id""").fetchall()
    participants = c.execute("""SELECT sender_type,display_name,last_seen FROM chat_presence
                                 WHERE last_seen>=? ORDER BY display_name COLLATE NOCASE""", (time.time() - 45,)).fetchall()
    c.close()
    messages = []
    for row in rows:
        item = dict(row)
        item["mine"] = item["sender_key"] == identity["key"]
        item["can_delete"] = bool(identity["admin"] or item["mine"])
        item["avatar"] = chat_avatar_for_message(item["sender_type"], item["display_name"])
        item.pop("sender_key", None)
        messages.append(item)
    return jsonify(identity={**identity, "avatar": chat_avatar(identity)}, join_required=False, messages=messages, participants=[dict(row) for row in participants], server_time=time.time())


@app.post("/api/chat/messages")
def chat_send_message():
    if not is_lan_request():
        return jsonify(error="LAN chat is available only on the local network."), 403
    if not config_value("chat_enabled"):
        return jsonify(error="LAN chat is disabled by the administrator."),403
    if session.get("guest_id") and not config_value("chat_allow_guests"):
        return jsonify(error="Guest chat access is disabled."),403
    if not current_user() and not session.get("guest_id") and not config_value("chat_allow_anonymous"):
        return jsonify(error="Anonymous chat access is disabled."),403
    identity = chat_identity()
    if not identity:
        return jsonify(error="Choose a chat name first."), 401
    if request.mimetype != "application/json":
        return jsonify(error="LAN Chat accepts text messages only."), 415
    if not chat_rate_allowed(identity["key"]):
        return jsonify(error="You are sending messages too quickly. Please wait a moment."), 429
    data = request.get_json(silent=True) or {}
    message = str(data.get("message") or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not message:
        return jsonify(error="Enter a message."), 400
    if len(message) > 1000:
        return jsonify(error="Messages can be up to 1000 characters."), 400
    if any(ord(ch) == 0 for ch in message):
        return jsonify(error="Message contains unsupported characters."), 400
    c = db()
    cur = c.execute("""INSERT INTO chat_messages(sender_type,sender_key,display_name,message,created_at)
                       VALUES(?,?,?,?,?)""", (identity["type"], identity["key"], identity["name"], message, time.time()))
    message_id = cur.lastrowid
    c.commit()
    c.close()
    touch_chat_presence(identity)
    return jsonify(ok=True, id=message_id)


@app.delete("/api/chat/messages/<int:message_id>")
def chat_delete_message(message_id):
    if not is_lan_request():
        return jsonify(error="LAN chat is available only on the local network."), 403
    identity = chat_identity()
    if not identity:
        return jsonify(error="Chat identity required."), 401
    c = db()
    row = c.execute("SELECT sender_key,deleted_at FROM chat_messages WHERE id=?", (message_id,)).fetchone()
    if not row:
        c.close()
        return jsonify(error="Message not found."), 404
    if not identity["admin"] and row["sender_key"] != identity["key"]:
        c.close()
        return jsonify(error="You can delete only your own messages."), 403
    c.execute("UPDATE chat_messages SET message='',deleted_at=? WHERE id=?", (time.time(), message_id))
    c.commit()
    c.close()
    return jsonify(ok=True)


@app.get("/api/admin/chat-settings")
def admin_get_chat_settings():
    user=current_user()
    if not user or user["role"]!="admin": return jsonify(error="Admin only"),403
    return jsonify(enabled=bool(config_value("chat_enabled")),allow_anonymous=bool(config_value("chat_allow_anonymous")),allow_guests=bool(config_value("chat_allow_guests")),retention_days=int(config_value("chat_retention_days") or 30))


@app.post("/api/admin/chat-settings")
def admin_save_chat_settings():
    user=current_user()
    if not user or user["role"]!="admin": return jsonify(error="Admin only"),403
    data=request.get_json(silent=True) or {}
    try: retention=max(1,min(int(data.get("retention_days",30)),3650))
    except (TypeError,ValueError): return jsonify(error="Retention must be a number of days."),400
    values={"chat_enabled":bool(data.get("enabled",True)),"chat_allow_anonymous":bool(data.get("allow_anonymous",True)),"chat_allow_guests":bool(data.get("allow_guests",True)),"chat_retention_days":retention}
    save_config_values(values); log_activity("chat_settings_changed","LAN Chat",json.dumps(values))
    return jsonify(ok=True,**values)


@app.delete("/api/admin/chat-messages")
def admin_clear_chat_messages():
    user=current_user()
    if not user or user["role"]!="admin": return jsonify(error="Admin only"),403
    c=db(); cur=c.execute("DELETE FROM chat_messages"); deleted=cur.rowcount; c.execute("DELETE FROM chat_presence"); c.commit(); c.close()
    log_activity("chat_history_cleared","LAN Chat",f"Deleted {deleted} message(s).")
    return jsonify(ok=True,deleted=deleted)


@app.get("/api/admin/temp-shares")
def admin_list_temp_shares():
    user = current_user()
    if not user or user["role"] != "admin": return jsonify(error="Admin only"), 403
    c = db()
    rows = c.execute("SELECT * FROM temp_share_channels ORDER BY created_at DESC LIMIT 100").fetchall()
    c.close()
    return jsonify(share_root=str(config_value("temp_share_root") or DEFAULT_SHARE_ROOT),
                   library_inbox=str((ROOT / "Quick Share Inbox").resolve()),
                   channels=[temp_share_public_data(row, True) for row in rows])


@app.get("/api/admin/temp-shares/<int:channel_id>/files")
def admin_temp_share_files(channel_id):
    user = current_user()
    if not user or user["role"] != "admin": return jsonify(error="Admin only"), 403
    c = db(); row = c.execute("SELECT * FROM temp_share_channels WHERE id=?", (channel_id,)).fetchone(); c.close()
    if not row: return jsonify(error="Share not found."), 404
    return jsonify(channel=temp_share_public_data(row, True),
                   files=temp_share_files(row, f"/api/admin/temp-shares/{channel_id}/download"))


@app.get("/api/admin/temp-shares/<int:channel_id>/download/<path:relative_path>")
def admin_temp_share_download(channel_id, relative_path):
    user = current_user()
    if not user or user["role"] != "admin": return jsonify(error="Admin only"), 403
    c = db(); row = c.execute("SELECT * FROM temp_share_channels WHERE id=?", (channel_id,)).fetchone(); c.close()
    if not row: abort(404)
    path = temp_share_safe_path(row, relative_path, True)
    return send_file(path, as_attachment=True, conditional=True, download_name=path.name)


@app.post("/api/admin/temp-shares/choose-directory")
def admin_choose_temp_share_directory():
    user = current_user()
    if not user or user["role"] != "admin": return jsonify(error="Admin only"), 403
    try:
        initial = str(config_value("temp_share_root") or DEFAULT_SHARE_ROOT)
        Path(initial).expanduser().mkdir(parents=True, exist_ok=True)
        selected = choose_folder_native(initial)
        if not selected: return jsonify(cancelled=True)
        selected_path = Path(selected).expanduser().resolve()
        if not selected_path.is_dir(): return jsonify(error="Selected share folder does not exist."), 400
        save_config_values({"temp_share_root": str(selected_path)})
        return jsonify(cancelled=False, directory=str(selected_path))
    except Exception as exc:
        return jsonify(error=str(exc)), 500


@app.post("/api/admin/temp-shares")
def admin_create_temp_share():
    user = current_user()
    if not user or user["role"] != "admin": return jsonify(error="Admin only"), 403
    data = request.get_json(silent=True) or {}
    name = re.sub(r"\s+", " ", str(data.get("name") or "Quick Share").strip())[:80] or "Quick Share"
    mode = str(data.get("mode") or "upload").lower()
    if mode not in {"upload", "download", "two_way"}: return jsonify(error="Invalid share mode."), 400
    try:
        minutes = max(5, min(int(data.get("minutes", 60)), 10080))
        max_file_bytes = max(1024 * 1024, min(int(float(data.get("max_file_mb", 1024)) * 1024 * 1024), 10 * 1024**3))
        max_total_bytes = max(max_file_bytes, min(int(float(data.get("max_total_mb", 5120)) * 1024 * 1024), 50 * 1024**3))
        max_devices = max(1, min(int(data.get("max_devices", 10)), 100))
    except (TypeError, ValueError):
        return jsonify(error="Expiry and transfer limits must be valid numbers."), 400
    directory = Path(str(data.get("directory") or config_value("temp_share_root") or DEFAULT_SHARE_ROOT)).expanduser().resolve()
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return jsonify(error=f"Share folder could not be created: {exc}"), 400
    if not directory.is_dir(): return jsonify(error="Choose a valid share folder."), 400
    token = secrets.token_urlsafe(32)
    now = time.time()
    c = db()
    cur = c.execute("""INSERT INTO temp_share_channels(token,name,mode,directory,expires_at,max_file_bytes,max_total_bytes,max_devices,created_at,created_by)
                       VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (token, name, mode, str(directory), now + minutes * 60, max_file_bytes, max_total_bytes, max_devices, now, user["id"]))
    channel_id = cur.lastrowid
    c.commit()
    row = c.execute("SELECT * FROM temp_share_channels WHERE id=?", (channel_id,)).fetchone()
    c.close()
    save_config_values({"temp_share_root": str(directory)})
    log_activity("temp_share_created", name, f"Mode {mode}; expires in {minutes} minutes; folder {directory}")
    return jsonify(ok=True, channel=temp_share_public_data(row, True))


@app.post("/api/admin/temp-shares/<int:channel_id>/extend")
def admin_extend_temp_share(channel_id):
    user = current_user()
    if not user or user["role"] != "admin": return jsonify(error="Admin only"), 403
    data = request.get_json(silent=True) or {}
    try: minutes = max(5, min(int(data.get("minutes", 60)), 10080))
    except (TypeError, ValueError): return jsonify(error="Invalid extension."), 400
    c = db()
    row = c.execute("SELECT * FROM temp_share_channels WHERE id=?", (channel_id,)).fetchone()
    if not row: c.close(); return jsonify(error="Share not found."), 404
    expires_at = max(time.time(), float(row["expires_at"])) + minutes * 60
    c.execute("UPDATE temp_share_channels SET expires_at=?,active=1 WHERE id=?", (expires_at, channel_id)); c.commit()
    row = c.execute("SELECT * FROM temp_share_channels WHERE id=?", (channel_id,)).fetchone(); c.close()
    log_activity("temp_share_extended", row["name"], f"Extended by {minutes} minutes.")
    return jsonify(ok=True, channel=temp_share_public_data(row, True))


@app.delete("/api/admin/temp-shares/<int:channel_id>")
def admin_revoke_temp_share(channel_id):
    user = current_user()
    if not user or user["role"] != "admin": return jsonify(error="Admin only"), 403
    c = db(); row = c.execute("SELECT name FROM temp_share_channels WHERE id=?", (channel_id,)).fetchone()
    if not row: c.close(); return jsonify(error="Share not found."), 404
    c.execute("UPDATE temp_share_channels SET active=0 WHERE id=?", (channel_id,)); c.commit(); c.close()
    log_activity("temp_share_revoked", row["name"], "Temporary share link revoked.")
    return jsonify(ok=True)


@app.get("/api/admin/temp-shares/<int:channel_id>/qr")
def admin_temp_share_qr(channel_id):
    user = current_user()
    if not user or user["role"] != "admin": return jsonify(error="Admin only"), 403
    c = db(); row = c.execute("SELECT * FROM temp_share_channels WHERE id=?", (channel_id,)).fetchone(); c.close()
    if not row: return jsonify(error="Share not found."), 404
    try:
        import io, qrcode
        output = io.BytesIO()
        qrcode.make(f"{guest_base_url()}/share/{row['token']}").save(output, format="PNG")
        output.seek(0)
        return send_file(output, mimetype="image/png", max_age=0)
    except Exception as exc:
        return jsonify(error=f"QR generation is unavailable: {exc}"), 500


@app.get("/share/<token>")
def temp_share_page(token):
    channel = temp_share_channel(token)
    if not channel: return render_template("share.html", token="", initial_error="This share link is invalid, revoked, or expired."), 404
    denied = temp_share_access(channel)
    if denied: return render_template("share.html", token="", initial_error=denied), 403
    return render_template("share.html", token=token, initial_error="")


@app.get("/share/<token>/state")
def temp_share_state(token):
    channel = temp_share_channel(token)
    if not channel: return jsonify(error="This share link is invalid, revoked, or expired."), 404
    denied = temp_share_access(channel)
    if denied: return jsonify(error=denied), 403
    files = []
    if channel["mode"] in {"download", "two_way"}:
        files = temp_share_files(channel, f"/share/{token}/download")
    return jsonify(channel=temp_share_public_data(channel), files=files)


@app.post("/share/<token>/upload")
def temp_share_upload(token):
    channel = temp_share_channel(token)
    if not channel: return jsonify(error="This share link is invalid, revoked, or expired."), 404
    denied = temp_share_access(channel)
    if denied: return jsonify(error=denied), 403
    if channel["mode"] not in {"upload", "two_way"}: return jsonify(error="Uploads are disabled for this share."), 403
    uploads = request.files.getlist("files")
    if not uploads: return jsonify(error="Choose at least one file."), 400
    root = temp_share_safe_path(channel)
    saved, failed, batch_bytes = [], [], 0
    for upload in uploads[:100]:
        name = valid_new_name(Path(upload.filename or "").name)
        if not name:
            failed.append({"name": upload.filename or "", "error": "Invalid filename"}); continue
        target = root / name
        if target.exists(): target = available_upload_target(target)
        partial = root / f".{target.name}.{secrets.token_hex(6)}.part"
        try:
            upload.save(partial)
            size = partial.stat().st_size
            if size > int(channel["max_file_bytes"]): raise ValueError("File exceeds this share's per-file limit")
            if int(channel["uploaded_bytes"]) + batch_bytes + size > int(channel["max_total_bytes"]): raise ValueError("Share upload quota exceeded")
            os.replace(partial, target)
            saved.append({"name": target.name, "size": size}); batch_bytes += size
            c = db(); c.execute("INSERT INTO temp_share_events(channel_id,event_type,filename,size,ip,created_at) VALUES(?,?,?,?,?,?)",
                                (channel["id"], "upload", target.name, size, client_ip(), time.time())); c.commit(); c.close()
        except Exception as exc:
            partial.unlink(missing_ok=True)
            failed.append({"name": name, "error": str(exc)})
    if batch_bytes:
        c = db(); c.execute("UPDATE temp_share_channels SET uploaded_bytes=uploaded_bytes+?,transfer_count=transfer_count+? WHERE id=?", (batch_bytes, len(saved), channel["id"])); c.commit(); c.close()
    return jsonify(ok=bool(saved), uploaded=saved, failed=failed), (200 if saved else 400)


@app.get("/share/<token>/download/<path:relative_path>")
def temp_share_download(token, relative_path):
    channel = temp_share_channel(token)
    if not channel: abort(404)
    denied = temp_share_access(channel)
    if denied or channel["mode"] not in {"download", "two_way"}: abort(403)
    path = temp_share_safe_path(channel, relative_path, True)
    c = db(); c.execute("INSERT INTO temp_share_events(channel_id,event_type,filename,size,ip,created_at) VALUES(?,?,?,?,?,?)",
                        (channel["id"], "download", path.name, path.stat().st_size, client_ip(), time.time()))
    c.execute("UPDATE temp_share_channels SET transfer_count=transfer_count+1 WHERE id=?", (channel["id"],)); c.commit(); c.close()
    return send_file(path, as_attachment=True, conditional=True, download_name=path.name)


@app.get("/api/health")
def public_health():
    lan_ip = detect_lan_ip()
    port = request.environ.get("SERVER_PORT", "8080")
    return jsonify(ok=True,app=str(config_value("app_name") or "Home Cloud"),version=APP_VERSION,status="online",time=time.time(),
                   lan_ip=lan_ip,lan_url=(f"http://{lan_ip}:{port}" if lan_ip else ""),internet_required=False,ai_mode=config_value("ai_mode"),chat_enabled=bool(config_value("chat_enabled")),chat_allow_anonymous=bool(config_value("chat_allow_anonymous")))


@app.get("/api/server-info")
def server_info():
    guard = require_perm("view")
    if guard:
        return guard
    try:
        usage = shutil.disk_usage(ROOT)
        disk = {"total": usage.total, "used": usage.used, "free": usage.free, "percent": round((usage.used / usage.total) * 100, 1) if usage.total else 0}
    except OSError:
        disk = {"total": 0, "used": 0, "free": 0, "percent": 0}
    return jsonify(app="Local Visual Asset Library", version=APP_VERSION, python=sys.version.split()[0], root=(str(ROOT) if current_user() and current_user()["role"]=="admin" else ""), disk=disk, recycle_count=len(_read_trash_items()), recycle_bytes=recycle_size_bytes())

@app.get("/api/media-info/<path:p>")
def media_info(p):
    guard=require_perm("view")
    if guard: return guard
    f = safe(p)
    ext = f.suffix.lower()
    if ext in IMAGE_EXTS: kind = "image"
    elif ext in VIDEO_EXTS: kind = "video"
    elif ext in AUDIO_EXTS: kind = "audio"
    else: kind = "file"
    return jsonify(kind=kind, ext=ext, name=f.name, size=f.stat().st_size)


@app.get("/thumb/<path:n>")
def getthumb(n):
    guard=require_perm("view")
    if guard: return guard
    p = (THUMBS / n).resolve()
    try:
        p.relative_to(THUMBS.resolve())
    except ValueError:
        abort(403)
    if not p.is_file():
        abort(404)
    return send_file(p)


@app.get("/api/assets/<int:asset_id>/details")
def asset_details(asset_id):
    guard = require_perm("view")
    if guard: return guard
    scope = current_scope()
    scope_clause, scope_params = scope_sql(scope)
    c = db()
    row = c.execute("SELECT * FROM assets WHERE id=?" + scope_clause, (asset_id,) + scope_params).fetchone()
    c.close()
    if not row:
        return jsonify(error="Asset not found"), 404
    item = dict(row)
    try:
        item["ai_tags"] = json.loads(item.get("ai_tags") or "[]")
    except Exception:
        item["ai_tags"] = []
    item["ai_caption"] = item.get("ai_caption") or ""
    # Never expose the server's absolute filesystem root to non-admin users.
    if not session.get("user_id") or not has_perm("manage_settings"):
        item["path"] = item.get("path", "")
    item["download_allowed"] = session_has_perm("download")
    item["preview_allowed"] = session_has_perm("preview")
    c2 = db()
    item["has_embedding"] = bool(c2.execute("SELECT 1 FROM embeddings WHERE asset_id=?", (asset_id,)).fetchone())
    c2.close()
    return jsonify(item)


def media_cache_target(source):
    stat = source.stat()
    identity = f"{rel(source)}|{stat.st_size}|{stat.st_mtime_ns}".encode("utf-8", "surrogatepass")
    return hashlib.sha256(identity).hexdigest(), MEDIA_CACHE / f"{hashlib.sha256(identity).hexdigest()}.mp4"


def ffmpeg_executable():
    configured = os.environ.get("HOME_CLOUD_FFMPEG", "").strip()
    if configured and Path(configured).is_file():
        return configured
    discovered = shutil.which("ffmpeg")
    if discovered:
        return discovered
    try:
        import imageio_ffmpeg
        candidate = imageio_ffmpeg.get_ffmpeg_exe()
        return candidate if candidate and Path(candidate).is_file() else ""
    except Exception:
        return ""


def media_hub_viewer_key():
    user = current_user()
    if user:
        return f"user:{user['id']}"
    guest_id = session.get("guest_id")
    return f"guest:{guest_id}" if guest_id else ""


def media_hub_guess(name):
    """Create useful offline metadata from a filename without web services."""
    stem = Path(str(name or "")).stem
    match = re.search(r"(?i)(.*?)[ ._\-]+S(\d{1,2})E(\d{1,3})(?:[ ._\-]+(.*))?$", stem)
    if match:
        series = re.sub(r"[._]+", " ", match.group(1)).strip(" -") or stem
        episode_title = re.sub(r"[._]+", " ", match.group(4) or "").strip(" -")
        season, episode = int(match.group(2)), int(match.group(3))
        title = episode_title or f"Episode {episode}"
        return {"title": title, "media_kind": "episode", "series_title": series,
                "season": season, "episode": episode, "category": "Series"}
    title = re.sub(r"[._]+", " ", stem)
    title = re.sub(r"\s+", " ", title).strip(" -") or stem
    return {"title": title, "media_kind": "movie", "series_title": "",
            "season": 0, "episode": 0, "category": "Movies"}


def make_video_thumb(source, force=False):
    """Extract a local poster frame. Failure leaves a safe generic card."""
    try:
        stat = source.stat()
        identity = f"video-poster|{rel(source)}|{stat.st_size}|{stat.st_mtime_ns}".encode("utf-8", "surrogatepass")
        name = hashlib.sha256(identity).hexdigest() + ".jpg"
        output = THUMBS / name
        if output.is_file() and output.stat().st_size and not force:
            return name
        executable = ffmpeg_executable()
        if not executable:
            return None
        THUMBS.mkdir(parents=True, exist_ok=True)
        temp = output.with_suffix(".part.jpg")
        command = [executable, "-hide_banner", "-loglevel", "error", "-y", "-ss", "3",
                   "-i", str(source), "-frames:v", "1", "-vf",
                   "scale=720:-2:force_original_aspect_ratio=decrease", "-q:v", "3", str(temp)]
        completed = subprocess.run(command, capture_output=True, text=True, timeout=90,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if completed.returncode == 0 and temp.is_file() and temp.stat().st_size:
            os.replace(temp, output)
            return name
        temp.unlink(missing_ok=True)
    except Exception as exc:
        server_event(f"Video poster generation failed: {exc}", "WARNING")
    return None


def _media_hub_generate_poster(asset_id, path):
    try:
        source = safe(path)
        poster = make_video_thumb(source)
        if poster:
            connection = db()
            connection.execute("UPDATE assets SET thumb=? WHERE id=?", (poster, asset_id))
            connection.execute("UPDATE media_hub_items SET poster=?,updated_at=? WHERE asset_id=?", (poster, time.time(), asset_id))
            connection.commit()
            connection.close()
    except Exception as exc:
        server_event(f"Media Hub poster task failed for {path}: {exc}", "WARNING")


def _media_hub_scope(scope, prefix="a."):
    scope = normalize_scope(scope)
    if not scope:
        return "", ()
    return f" AND ({prefix}path=? OR {prefix}path LIKE ?)", (scope, scope + "/%")


def _media_hub_payload(row):
    item = dict(row)
    item["path"] = item.get("path") or ""
    item["poster"] = item.get("poster") or item.get("asset_thumb") or ""
    item["poster_url"] = url_for("getthumb", n=item["poster"]) if item["poster"] else ""
    item["watchlisted"] = bool(item.get("watchlisted"))
    item["featured"] = bool(item.get("featured"))
    item["completed"] = bool(item.get("completed"))
    item["preview_allowed"] = session_has_perm("preview")
    item["download_allowed"] = session_has_perm("download")
    item["kind"] = "video"
    return item


@app.get("/api/media-hub")
def media_hub_home():
    guard = require_perm("preview")
    if guard: return guard
    viewer = media_hub_viewer_key()
    scope_clause, scope_params = _media_hub_scope(current_scope())
    query = """SELECT m.*,a.id,a.path,a.name,a.ext,a.size,a.mtime,a.thumb AS asset_thumb,
               COALESCE(p.position,0) AS position,COALESCE(p.duration,0) AS duration,
               COALESCE(p.completed,0) AS completed,COALESCE(p.updated_at,0) AS progress_updated,
               CASE WHEN w.asset_id IS NULL THEN 0 ELSE 1 END AS watchlisted
               FROM media_hub_items m JOIN assets a ON a.id=m.asset_id
               LEFT JOIN media_hub_progress p ON p.asset_id=a.id AND p.viewer_key=?
               LEFT JOIN media_hub_watchlist w ON w.asset_id=a.id AND w.viewer_key=?
               WHERE a.ext IN ({}){} ORDER BY m.added_at DESC""".format(
                   ",".join("?" for _ in VIDEO_EXTS), scope_clause)
    connection = db()
    rows = connection.execute(query, (viewer, viewer) + tuple(sorted(VIDEO_EXTS)) + scope_params).fetchall()
    connection.close()
    items = [_media_hub_payload(row) for row in rows]
    search = str(request.args.get("q") or "").strip().casefold()
    if search:
        items = [x for x in items if search in " ".join((x.get("title", ""), x.get("series_title", ""),
                                                         x.get("description", ""), x.get("category", ""))).casefold()]
    continue_watching = sorted(
        [x for x in items if x["position"] > 10 and not x["completed"] and x["duration"] > 0],
        key=lambda x: x["progress_updated"], reverse=True)[:20]
    watchlist = [x for x in items if x["watchlisted"]][:30]
    featured = next((x for x in items if x["featured"]), items[0] if items else None)
    movies = [x for x in items if x["media_kind"] != "episode"]
    series = {}
    for item in items:
        if item["media_kind"] == "episode":
            series.setdefault(item["series_title"] or "Series", []).append(item)
    series_rows = []
    for title, episodes in series.items():
        episodes.sort(key=lambda x: (x["season"], x["episode"], x["title"]))
        lead = dict(episodes[0])
        lead.update({"title": title, "media_kind": "series", "episode_count": len(episodes), "episodes": episodes})
        series_rows.append(lead)
    categories = {}
    for item in movies:
        categories.setdefault(item["category"] or "Movies", []).append(item)
    return jsonify(ok=True, featured=featured, continue_watching=continue_watching,
                   watchlist=watchlist, recently_added=items[:30], movies=movies,
                   series=series_rows, categories=categories, total=len(items))


@app.get("/api/media-hub/items/<int:asset_id>")
def media_hub_details(asset_id):
    guard = require_perm("preview")
    if guard: return guard
    viewer = media_hub_viewer_key()
    scope_clause, scope_params = _media_hub_scope(current_scope())
    connection = db()
    row = connection.execute("""SELECT m.*,a.id,a.path,a.name,a.ext,a.size,a.mtime,a.thumb AS asset_thumb,
            COALESCE(p.position,0) AS position,COALESCE(p.duration,0) AS duration,
            COALESCE(p.completed,0) AS completed,CASE WHEN w.asset_id IS NULL THEN 0 ELSE 1 END AS watchlisted
            FROM media_hub_items m JOIN assets a ON a.id=m.asset_id
            LEFT JOIN media_hub_progress p ON p.asset_id=a.id AND p.viewer_key=?
            LEFT JOIN media_hub_watchlist w ON w.asset_id=a.id AND w.viewer_key=?
            WHERE a.id=?""" + scope_clause, (viewer, viewer, asset_id) + scope_params).fetchone()
    if not row:
        connection.close(); return jsonify(error="Media item not found"), 404
    item = _media_hub_payload(row)
    episodes = []
    if item["media_kind"] == "episode" and item["series_title"]:
        episode_rows = connection.execute("""SELECT m.*,a.id,a.path,a.name,a.ext,a.size,a.mtime,a.thumb AS asset_thumb,
                COALESCE(p.position,0) AS position,COALESCE(p.duration,0) AS duration,
                COALESCE(p.completed,0) AS completed,CASE WHEN w.asset_id IS NULL THEN 0 ELSE 1 END AS watchlisted
                FROM media_hub_items m JOIN assets a ON a.id=m.asset_id
                LEFT JOIN media_hub_progress p ON p.asset_id=a.id AND p.viewer_key=?
                LEFT JOIN media_hub_watchlist w ON w.asset_id=a.id AND w.viewer_key=?
                WHERE m.series_title=?""" + scope_clause + " ORDER BY m.season,m.episode,m.title",
                (viewer, viewer, item["series_title"]) + scope_params).fetchall()
        episodes = [_media_hub_payload(x) for x in episode_rows]
    connection.close()
    return jsonify(item=item, episodes=episodes)


@app.post("/api/media-hub/progress/<int:asset_id>")
def media_hub_save_progress(asset_id):
    guard = require_perm("preview")
    if guard: return guard
    viewer = media_hub_viewer_key()
    if not viewer: return jsonify(error="Authentication required"), 401
    data = request.get_json(silent=True) or {}
    try:
        position = max(0.0, float(data.get("position") or 0))
        duration = max(0.0, float(data.get("duration") or 0))
    except (TypeError, ValueError):
        return jsonify(error="Invalid playback progress"), 400
    if duration:
        position = min(position, duration)
    completed = 1 if duration > 0 and (position >= duration - 20 or position / duration >= .94) else 0
    scope_clause, scope_params = _media_hub_scope(current_scope())
    connection = db()
    exists = connection.execute("SELECT 1 FROM media_hub_items m JOIN assets a ON a.id=m.asset_id WHERE a.id=?" + scope_clause,
                                (asset_id,) + scope_params).fetchone()
    if not exists:
        connection.close(); return jsonify(error="Media item not found"), 404
    connection.execute("""INSERT INTO media_hub_progress(viewer_key,asset_id,position,duration,completed,updated_at)
        VALUES(?,?,?,?,?,?) ON CONFLICT(viewer_key,asset_id) DO UPDATE SET position=excluded.position,
        duration=excluded.duration,completed=excluded.completed,updated_at=excluded.updated_at""",
        (viewer, asset_id, position, duration, completed, time.time()))
    connection.commit(); connection.close()
    return jsonify(ok=True, completed=bool(completed))


@app.post("/api/media-hub/watchlist/<int:asset_id>")
def media_hub_toggle_watchlist(asset_id):
    guard = require_perm("preview")
    if guard: return guard
    viewer = media_hub_viewer_key()
    data = request.get_json(silent=True) or {}
    enabled = bool(data.get("enabled", True))
    scope_clause, scope_params = _media_hub_scope(current_scope())
    connection = db()
    exists = connection.execute("SELECT 1 FROM media_hub_items m JOIN assets a ON a.id=m.asset_id WHERE a.id=?" + scope_clause,
                                (asset_id,) + scope_params).fetchone()
    if not exists:
        connection.close(); return jsonify(error="Media item not found"), 404
    if enabled:
        connection.execute("INSERT OR IGNORE INTO media_hub_watchlist(viewer_key,asset_id,added_at) VALUES(?,?,?)",
                           (viewer, asset_id, time.time()))
    else:
        connection.execute("DELETE FROM media_hub_watchlist WHERE viewer_key=? AND asset_id=?", (viewer, asset_id))
    connection.commit(); connection.close()
    return jsonify(ok=True, watchlisted=enabled)


@app.get("/api/admin/media-hub/candidates")
def media_hub_candidates():
    guard = require_perm("manage_settings")
    if guard: return guard
    search = str(request.args.get("q") or "").strip()
    where, params = "", []
    if search:
        where = " AND (a.name LIKE ? OR a.path LIKE ?)"
        params.extend((f"%{search}%", f"%{search}%"))
    connection = db()
    rows = connection.execute("""SELECT a.id,a.path,a.name,a.ext,a.size,a.mtime,a.thumb,
        CASE WHEN m.asset_id IS NULL THEN 0 ELSE 1 END AS selected,m.title,m.category,m.media_kind,
        m.series_title,m.season,m.episode,m.featured,m.description
        FROM assets a LEFT JOIN media_hub_items m ON m.asset_id=a.id
        WHERE a.ext IN ({}){} ORDER BY selected DESC,a.mtime DESC LIMIT 500""".format(
            ",".join("?" for _ in VIDEO_EXTS), where), tuple(sorted(VIDEO_EXTS)) + tuple(params)).fetchall()
    connection.close()
    return jsonify(items=[dict(row) for row in rows])


@app.post("/api/admin/media-hub/items")
def media_hub_add_items():
    guard = require_perm("manage_settings")
    if guard: return guard
    data = request.get_json(silent=True) or {}
    raw_ids = data.get("asset_ids") if isinstance(data.get("asset_ids"), list) else [data.get("asset_id")]
    try:
        asset_ids = sorted({int(x) for x in raw_ids if x is not None})
    except (TypeError, ValueError):
        return jsonify(error="Invalid media selection"), 400
    if not asset_ids:
        return jsonify(error="Select at least one video"), 400
    connection = db(); added = []
    for asset_id in asset_ids:
        row = connection.execute("SELECT id,path,name,ext FROM assets WHERE id=?", (asset_id,)).fetchone()
        if not row or row["ext"] not in VIDEO_EXTS:
            continue
        guessed = media_hub_guess(row["name"])
        now = time.time()
        cursor = connection.execute("""INSERT OR IGNORE INTO media_hub_items(
            asset_id,title,category,media_kind,series_title,season,episode,added_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?)""", (asset_id, guessed["title"], guessed["category"], guessed["media_kind"],
            guessed["series_title"], guessed["season"], guessed["episode"], now, now))
        if cursor.rowcount:
            added.append(asset_id)
            threading.Thread(target=_media_hub_generate_poster, args=(asset_id, row["path"]), daemon=True).start()
    connection.commit(); connection.close()
    log_activity("media_hub_add", ",".join(map(str, added)), f"Added {len(added)} video(s) to Media Hub.")
    return jsonify(ok=True, added=added)


@app.post("/api/admin/media-hub/items/<int:asset_id>")
def media_hub_update_item(asset_id):
    guard = require_perm("manage_settings")
    if guard: return guard
    data = request.get_json(silent=True) or {}
    media_kind = str(data.get("media_kind") or "movie").lower()
    if media_kind not in {"movie", "episode"}: media_kind = "movie"
    try:
        season = max(0, int(data.get("season") or 0)); episode = max(0, int(data.get("episode") or 0))
    except (TypeError, ValueError):
        return jsonify(error="Season and episode must be numbers"), 400
    values = (str(data.get("title") or "").strip()[:240], str(data.get("description") or "").strip()[:4000],
              str(data.get("category") or "Movies").strip()[:100], media_kind,
              str(data.get("series_title") or "").strip()[:240], season, episode,
              1 if data.get("featured") else 0, time.time(), asset_id)
    connection = db()
    cursor = connection.execute("""UPDATE media_hub_items SET title=?,description=?,category=?,media_kind=?,
        series_title=?,season=?,episode=?,featured=?,updated_at=? WHERE asset_id=?""", values)
    connection.commit(); connection.close()
    if not cursor.rowcount: return jsonify(error="Media item not found"), 404
    log_activity("media_hub_edit", str(asset_id), "Updated Media Hub metadata.")
    return jsonify(ok=True)


@app.delete("/api/admin/media-hub/items/<int:asset_id>")
def media_hub_remove_item(asset_id):
    guard = require_perm("manage_settings")
    if guard: return guard
    connection = db()
    connection.execute("DELETE FROM media_hub_items WHERE asset_id=?", (asset_id,))
    connection.execute("DELETE FROM media_hub_progress WHERE asset_id=?", (asset_id,))
    connection.execute("DELETE FROM media_hub_watchlist WHERE asset_id=?", (asset_id,))
    connection.commit(); connection.close()
    log_activity("media_hub_remove", str(asset_id), "Removed item from Media Hub; original file was kept.")
    return jsonify(ok=True)


@app.get("/api/media-hub/subtitles/<int:asset_id>")
def media_hub_subtitles(asset_id):
    guard = require_perm("preview")
    if guard: return guard
    scope_clause, scope_params = _media_hub_scope(current_scope())
    connection = db()
    row = connection.execute("SELECT a.path FROM media_hub_items m JOIN assets a ON a.id=m.asset_id WHERE a.id=?" + scope_clause,
                             (asset_id,) + scope_params).fetchone()
    connection.close()
    if not row: return jsonify(error="Media item not found"), 404
    source = safe(row["path"])
    tracks = []
    try:
        # Supports Movie.en.srt, Movie.bn.vtt and Movie.srt naming styles.
        for candidate in source.parent.glob(source.stem + "*"):
            if candidate.is_file() and candidate.suffix.lower() in {".srt", ".vtt"}:
                tail = candidate.stem[len(source.stem):].strip(" ._- ")
                label = tail.upper() if tail else "Subtitle"
                tracks.append({"label": label, "srclang": (tail.split(".")[0].lower() if tail else "und")[:12],
                               "url": url_for("subtitle_preview", p=rel(candidate))})
    except OSError:
        pass
    return jsonify(tracks=tracks)


@app.get("/subtitle/<path:p>")
def subtitle_preview(p):
    guard = require_perm("preview")
    if guard: return guard
    target = safe(p)
    if target.suffix.lower() not in {".srt", ".vtt"}: abort(404)
    try:
        content = target.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        abort(404)
    if target.suffix.lower() == ".srt":
        content = "WEBVTT\n\n" + re.sub(r"(?m)(\d{2}:\d{2}:\d{2}),(\d{3})\s+-->\s+(\d{2}:\d{2}:\d{2}),(\d{3})",
                                           r"\1.\2 --> \3.\4", content)
    elif not content.lstrip().startswith("WEBVTT"):
        content = "WEBVTT\n\n" + content
    return Response(content, mimetype="text/vtt", headers={"Cache-Control": "private, max-age=300"})


def build_compatible_video(source, key, output):
    temp = output.with_suffix(".part.mp4")
    try:
        MEDIA_CACHE.mkdir(parents=True, exist_ok=True)
        executable = ffmpeg_executable()
        if not executable:
            raise RuntimeError("The local video compatibility runtime is not installed.")
        command = [
            executable, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
            "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "23", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
            "-movflags", "+faststart", str(temp),
        ]
        completed = subprocess.run(command, capture_output=True, text=True, timeout=7200, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if completed.returncode != 0 or not temp.is_file() or temp.stat().st_size == 0:
            detail = (completed.stderr or completed.stdout or "Video conversion failed.").strip().splitlines()
            raise RuntimeError(detail[-1][:500] if detail else "Video conversion failed.")
        os.replace(temp, output)
        with _media_jobs_lock:
            _media_jobs[key] = {"status": "ready", "updated_at": time.time()}
        server_event(f"Compatible video cache created: {rel(source)}")
    except Exception as exc:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        with _media_jobs_lock:
            _media_jobs[key] = {"status": "error", "error": str(exc)[:500], "updated_at": time.time()}
        server_event(f"Compatible video conversion failed for {rel(source)}: {exc}", "ERROR")


@app.route("/api/media/compatibility/<path:p>", methods=["GET", "POST"])
def media_compatibility(p):
    guard = require_perm("preview")
    if guard:
        return guard
    source = safe(p)
    if source.suffix.lower() not in set(VIDEO_EXTS):
        return jsonify(error="This file is not a supported video."), 400
    key, output = media_cache_target(source)
    if output.is_file() and output.stat().st_size:
        return jsonify(status="ready", url=url_for("compatible_preview", p=rel(source)))
    with _media_jobs_lock:
        job = dict(_media_jobs.get(key) or {})
        if request.method == "POST" and job.get("status") != "running":
            if not ffmpeg_executable():
                return jsonify(error="The local video compatibility runtime is not installed."), 503
            _media_jobs[key] = {"status": "running", "started_at": time.time(), "updated_at": time.time()}
            threading.Thread(target=build_compatible_video, args=(source, key, output), daemon=True).start()
            job = dict(_media_jobs[key])
    return jsonify(status=job.get("status", "idle"), error=job.get("error", ""))


@app.get("/compatible-preview/<path:p>")
def compatible_preview(p):
    guard = require_perm("preview")
    if guard:
        return guard
    source = safe(p)
    _, output = media_cache_target(source)
    if not output.is_file():
        return jsonify(error="Compatible stream is not ready."), 404
    response = send_file(output, mimetype="video/mp4", as_attachment=False, conditional=True, max_age=0)
    response.headers["Content-Disposition"] = "inline"
    response.headers.setdefault("Accept-Ranges", "bytes")
    return response


@app.get("/preview/<path:p>")
def preview(p):
    guard=require_perm("preview")
    if guard: return guard
    target=safe(p)
    if session.get("guest_id"):
        log_activity("guest_preview", rel(target), "Guest previewed an asset.")
    mime = PREVIEW_MIME_TYPES.get(target.suffix.lower()) or mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    response = send_file(target, mimetype=mime, as_attachment=False, download_name=target.name, conditional=True, max_age=0)
    response.headers["Content-Disposition"] = f"inline; filename*=UTF-8''{quote(target.name, safe='')}"
    response.headers.setdefault("Accept-Ranges", "bytes")
    return response


@app.get("/file/<path:p>")
def getfile(p):
    guard=require_perm("download")
    if guard: return guard
    target=safe(p)
    log_activity("download", rel(target), "File downloaded.")
    return send_file(target, as_attachment=True)


if __name__ == "__main__":
    init_db()
    server_event(f"Home Cloud {APP_VERSION} starting on port 8080.")
    print("Asset root:", ROOT)
    print("Open http://127.0.0.1:8080")
    print("LAN access: http://<HOST-PC-IP>:8080")
    app.run(host="0.0.0.0", port=8080, debug=False)

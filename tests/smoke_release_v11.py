import io
import json
import shutil
import sqlite3
import sys
import tempfile
import time
import zipfile
from pathlib import Path

from werkzeug.security import generate_password_hash


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))
import app as home_cloud


def table_columns(database, table):
    connection = sqlite3.connect(database)
    columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
    connection.close()
    return columns


def run():
    temp_dir = Path(tempfile.mkdtemp(prefix="home-cloud-v11-release-"))
    try:
        home_cloud.ROOT = temp_dir / "library"
        home_cloud.ROOT.mkdir()
        home_cloud.THUMBS = temp_dir / "thumbnails"
        home_cloud.THUMBS.mkdir()
        home_cloud.MEDIA_CACHE = temp_dir / "media_cache"
        home_cloud.CONFIG = temp_dir / "config.json"
        generated = home_cloud.load_config()
        assert generated["asset_root"].endswith("Home Cloud Library")
        home_cloud.CFG = {**generated, **home_cloud.CONFIG_DEFAULTS, "asset_root": str(home_cloud.ROOT)}

        # Brand-new installations create every v11 table and migration column.
        home_cloud.DB = temp_dir / "fresh.db"
        home_cloud.init_db()
        assert {"last_login", "storage_limit_mb", "allowed_dir"} <= table_columns(home_cloud.DB, "users")
        assert {"chat_messages", "chat_presence", "revoked_sessions"} <= {
            row[0] for row in sqlite3.connect(home_cloud.DB).execute("SELECT name FROM sqlite_master WHERE type='table'")
        }

        # A legacy database keeps its data while v11 columns and permissions are added.
        home_cloud.DB = temp_dir / "legacy.db"
        connection = sqlite3.connect(home_cloud.DB)
        connection.execute("CREATE TABLE assets(id INTEGER PRIMARY KEY,path TEXT UNIQUE,name TEXT,ext TEXT,size INTEGER,mtime REAL,ocr_attempted INTEGER NOT NULL DEFAULT 0)")
        connection.execute("CREATE TABLE users(id INTEGER PRIMARY KEY,username TEXT UNIQUE NOT NULL,password_hash TEXT NOT NULL,role TEXT NOT NULL DEFAULT 'client',permissions TEXT NOT NULL DEFAULT '{}',active INTEGER NOT NULL DEFAULT 1,created_at REAL NOT NULL)")
        connection.execute("CREATE TABLE guest_tokens(id INTEGER PRIMARY KEY,token TEXT UNIQUE NOT NULL,permissions TEXT NOT NULL DEFAULT '{}',expires_at REAL NOT NULL,active INTEGER NOT NULL DEFAULT 1,created_at REAL NOT NULL)")
        connection.execute("INSERT INTO users(username,password_hash,role,permissions,active,created_at) VALUES(?,?,?,?,1,?)", ("legacy-client", generate_password_hash("temporary-test"), "client", "{}", time.time()))
        connection.commit()
        connection.close()
        home_cloud.init_db()
        assert {"last_login", "storage_limit_mb", "allowed_dir"} <= table_columns(home_cloud.DB, "users")
        connection = home_cloud.db()
        legacy = connection.execute("SELECT permissions FROM users WHERE username='legacy-client'").fetchone()
        assert json.loads(legacy["permissions"])["dashboard"] is True
        admin_id = connection.execute("INSERT INTO users(username,password_hash,role,permissions,active,created_at,allowed_dir,last_login,storage_limit_mb) VALUES(?,?,?,?,1,?,?,0,0)", ("release-admin", generate_password_hash("temporary-test"), "admin", json.dumps(home_cloud.DEFAULT_ADMIN_PERMS), time.time(), "")).lastrowid
        connection.commit()
        connection.close()

        # Diagnostics are useful for support but must not contain the DB or secret material.
        client = home_cloud.app.test_client()
        with client.session_transaction() as saved:
            saved["user_id"] = admin_id
            saved["activity_sid"] = "release-test-session"

        # Browser previews must stay inline and support byte ranges for video seeking.
        preview_video = home_cloud.ROOT / "preview sample.mp4"
        preview_video.write_bytes(b"0" * 4096)
        response = client.get("/preview/preview%20sample.mp4", headers={"Range": "bytes=0-1023"})
        assert response.status_code == 206
        assert response.headers["Content-Type"] == "video/mp4"
        assert response.headers["Content-Disposition"].startswith("inline;")
        assert response.headers["Content-Range"] == "bytes 0-1023/4096"
        response.close()
        preview_mov = home_cloud.ROOT / "phone clip.MOV"
        preview_mov.write_bytes(b"0" * 4096)
        response = client.get("/preview/phone%20clip.MOV", headers={"Range": "bytes=1024-2047"})
        assert response.status_code == 206
        assert response.headers["Content-Type"] == "video/quicktime"
        assert response.headers["Content-Range"] == "bytes 1024-2047/4096"
        response.close()
        _, compatible_file = home_cloud.media_cache_target(preview_mov)
        compatible_file.parent.mkdir()
        compatible_file.write_bytes(b"1" * 4096)
        response = client.get("/api/media/compatibility/phone%20clip.MOV")
        assert response.status_code == 200 and response.get_json()["status"] == "ready"
        response = client.get("/compatible-preview/phone%20clip.MOV", headers={"Range": "bytes=0-511"})
        assert response.status_code == 206
        assert response.headers["Content-Type"] == "video/mp4"
        assert response.headers["Content-Range"] == "bytes 0-511/4096"
        response.close()

        response = client.get("/api/admin/diagnostics-export")
        assert response.status_code == 200
        with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
            assert set(archive.namelist()) == {"diagnostics.json", "server-events.txt", "README.txt"}
            combined = b"\n".join(archive.read(name) for name in archive.namelist())
            assert b"password_hash" not in combined and b"secret.key" not in combined

        source = (PACKAGE_ROOT / "app.py").read_text(encoding="utf-8")
        assert "ipwho.is" not in source and "1.1.1.1" not in source
        frontend = (PACKAGE_ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        assert "pushFileHistory" in frontend and "addEventListener('popstate'" in frontend
        assert 'id="viewerSeek"' in frontend and 'id="imageTools"' in frontend
        assert "video.controls=false" in frontend and "requestPictureInPicture" in frontend
        assert "openFilePreview(x)" in frontend and "homeCloudViewer:true" in frontend
        assert "preparing a local H.264 stream automatically" in frontend and "prepareCompatibleVideo" in frontend
        assert "/api/media/compatibility/" in frontend
        assert "viewerCollection(fileEntries,x)" in frontend and "viewerItems.length" in frontend
        assert "toggleViewerSlideshow" in frontend and 'id="viewerSlideshow"' in frontend
        assert "fileIconType(x)" in frontend and "homecloud-polish-styles" in frontend
        assert "screen.orientation?.lock" in frontend and "webkitEnterFullscreen" in frontend
        assert "['.mov','.m4v','.mkv','.avi'" in frontend and "height:100dvh" in frontend
        assert "uploadConflictChoices" in frontend and "setUploadConflict" in frontend
        assert "hc_upload_conflict" in frontend and 'id="fileHintText"' in frontend
        bom_config = temp_dir / "bom-config.json"
        bom_config.write_bytes(b"\xef\xbb\xbf" + json.dumps({"asset_root": str(home_cloud.ROOT), "ai_mode": "standard"}).encode("utf-8"))
        home_cloud.CONFIG = bom_config
        assert home_cloud.load_config()["ai_mode"] == "standard"
        print("Home Cloud v11 release and migration smoke tests passed.")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    run()

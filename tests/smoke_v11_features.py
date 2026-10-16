import io
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

from werkzeug.security import generate_password_hash


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))
import app as home_cloud


HEADERS = {"X-HomeCloud-Client": "1"}


def login_client(user_id):
    client = home_cloud.app.test_client()
    with client.session_transaction() as saved:
        saved["user_id"] = user_id
        saved["activity_sid"] = f"test-{user_id}-{time.time_ns()}"
    return client


def add_user(username, role="client", storage_limit_mb=0):
    permissions = home_cloud.DEFAULT_ADMIN_PERMS if role == "admin" else {
        **home_cloud.DEFAULT_CLIENT_PERMS,
        "upload": True,
        "create_folder": True,
    }
    connection = home_cloud.db()
    user_id = connection.execute(
        "INSERT INTO users(username,password_hash,role,permissions,active,created_at,allowed_dir,storage_limit_mb) VALUES(?,?,?,?,1,?,?,?)",
        (username, generate_password_hash("testing-only"), role, json.dumps(permissions), time.time(), "", storage_limit_mb),
    ).lastrowid
    connection.commit()
    connection.close()
    return user_id


def run():
    temp_dir = Path(tempfile.mkdtemp(prefix="home-cloud-v11-features-"))
    try:
        home_cloud.ROOT = temp_dir / "library"
        (home_cloud.ROOT / "Designs").mkdir(parents=True)
        home_cloud.DB = temp_dir / "features.db"
        home_cloud.THUMBS = temp_dir / "thumbnails"
        home_cloud.THUMBS.mkdir()
        home_cloud.CONFIG = temp_dir / "config.json"
        home_cloud.CFG = {"asset_root": str(home_cloud.ROOT), **home_cloud.CONFIG_DEFAULTS}
        home_cloud.save_config_values({})
        home_cloud.init_db()

        admin_id = add_user("feature-admin", "admin")
        client_id = add_user("quota-client", storage_limit_mb=1)
        admin = login_client(admin_id)
        limited = login_client(client_id)

        connection = home_cloud.db()
        now = time.time()
        connection.execute(
            "INSERT INTO assets(path,name,ext,size,mtime) VALUES(?,?,?,?,?)",
            ("Designs/poster.psd", "poster.psd", ".psd", 5_000_000, now),
        )
        connection.execute(
            "INSERT INTO assets(path,name,ext,size,mtime) VALUES(?,?,?,?,?)",
            ("Designs/icon.png", "icon.png", ".png", 200_000, now - 86400),
        )
        connection.commit()
        connection.close()

        filtered = admin.get("/api/search?ext=psd&folder=Designs&min_size=1000000").get_json()
        assert len(filtered) == 1 and filtered[0]["name"] == "poster.psd"
        assert admin.get("/api/search?folder=../outside").status_code == 400

        quota = limited.post(
            "/api/files/upload",
            data={"path": "", "files": (io.BytesIO(b"x" * (1024 * 1024 + 1)), "large.zip")},
            headers=HEADERS,
            content_type="multipart/form-data",
        )
        assert quota.status_code == 413

        chat_settings = admin.post(
            "/api/admin/chat-settings",
            json={"enabled": True, "allow_anonymous": False, "allow_guests": True, "retention_days": 7},
            headers=HEADERS,
        )
        assert chat_settings.status_code == 200
        anonymous = home_cloud.app.test_client()
        assert anonymous.get("/api/chat/state").status_code == 403

        reset = admin.post(
            f"/api/users/{client_id}/password",
            json={"password": "new-testing-password"},
            headers=HEADERS,
        )
        assert reset.status_code == 200
        assert limited.get("/api/me").get_json()["authenticated"] is False

        removed = admin.delete(f"/api/users/{client_id}", headers=HEADERS)
        assert removed.status_code == 200
        assert all(user["id"] != client_id for user in admin.get("/api/users").get_json())

        settings = admin.post(
            "/api/settings",
            json={"asset_root": str(home_cloud.ROOT), "app_name": "Studio Cloud", "port": 8099, "ai_mode": "standard", "thumbnail_quality": 78},
            headers=HEADERS,
        )
        assert settings.status_code == 200 and settings.get_json()["restart_required"] is True
        saved = json.loads(home_cloud.CONFIG.read_text(encoding="utf-8"))
        assert saved["port"] == 8099 and saved["ai_mode"] == "standard"
        assert admin.post("/api/embed", headers=HEADERS).status_code == 409

        health = admin.get("/api/admin/system-health").get_json()
        assert health["internet_required"] is False and health["ai_mode"] == "standard"
        qr = admin.get("/api/admin/lan-qr")
        assert qr.status_code == 200 and qr.mimetype == "image/png"
        print("Home Cloud v11 feature smoke tests passed.")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    run()

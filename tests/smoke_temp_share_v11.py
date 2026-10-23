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


def run():
    temp_dir = Path(tempfile.mkdtemp(prefix="home-cloud-v11-share-"))
    try:
        library = temp_dir / "library"
        share_folder = temp_dir / "phone-drop"
        library.mkdir(); share_folder.mkdir()
        home_cloud.ROOT = library
        home_cloud.DB = temp_dir / "share.db"
        home_cloud.THUMBS = temp_dir / "thumbnails"; home_cloud.THUMBS.mkdir()
        home_cloud.CONFIG = temp_dir / "config.json"
        home_cloud.CFG = {"asset_root": str(library), **home_cloud.CONFIG_DEFAULTS, "temp_share_root": str(share_folder)}
        home_cloud.init_db()
        c = home_cloud.db()
        admin_id = c.execute(
            "INSERT INTO users(username,password_hash,role,permissions,active,created_at,allowed_dir) VALUES(?,?,?,?,1,?,?)",
            ("share-admin", generate_password_hash("testing-only"), "admin", json.dumps(home_cloud.DEFAULT_ADMIN_PERMS), time.time(), ""),
        ).lastrowid
        c.commit(); c.close()
        admin = home_cloud.app.test_client()
        with admin.session_transaction() as saved:
            saved["user_id"] = admin_id

        created = admin.post("/api/admin/temp-shares", headers=HEADERS, json={
            "name": "Phone Drop", "mode": "two_way", "directory": str(share_folder),
            "minutes": 15, "max_devices": 3, "max_file_mb": 2, "max_total_mb": 4,
        })
        assert created.status_code == 200, created.get_data(as_text=True)
        channel = created.get_json()["channel"]
        token = channel["token"]
        channel_id = channel["id"]
        assert admin.get(f"/api/admin/temp-shares/{channel_id}/qr").status_code == 200

        phone = home_cloud.app.test_client()
        assert phone.get(f"/share/{token}").status_code == 200
        uploaded = phone.post(f"/share/{token}/upload", data={"files": (io.BytesIO(b"hello phone"), "hello.txt")}, content_type="multipart/form-data")
        assert uploaded.status_code == 200, uploaded.get_data(as_text=True)
        duplicate = phone.post(f"/share/{token}/upload", data={"files": (io.BytesIO(b"second"), "hello.txt")}, content_type="multipart/form-data")
        assert duplicate.status_code == 200
        assert (share_folder / "hello.txt").read_bytes() == b"hello phone"
        assert (share_folder / "hello (2).txt").read_bytes() == b"second"

        admin_files = admin.get(f"/api/admin/temp-shares/{channel_id}/files")
        assert admin_files.status_code == 200
        admin_payload = admin_files.get_json()
        assert {item["name"] for item in admin_payload["files"]} == {"hello.txt", "hello (2).txt"}
        assert admin_payload["channel"]["directory"] == str(share_folder)
        assert admin_payload["channel"]["in_library"] is False
        listed = admin.get("/api/admin/temp-shares").get_json()
        assert listed["library_inbox"] == str((library / "Quick Share Inbox").resolve())

        state = phone.get(f"/share/{token}/state").get_json()
        assert len(state["files"]) == 2
        download_url = next(item["download_url"] for item in state["files"] if item["name"] == "hello.txt")
        response = phone.get(download_url)
        assert response.status_code == 200 and response.data == b"hello phone"

        assert admin.post(f"/api/admin/temp-shares/{channel_id}/extend", headers=HEADERS, json={"minutes": 60}).status_code == 200
        assert admin.delete(f"/api/admin/temp-shares/{channel_id}", headers=HEADERS).status_code == 200
        assert phone.get(f"/share/{token}/state").status_code == 404
        print("Home Cloud v11 temporary share smoke tests passed.")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    run()

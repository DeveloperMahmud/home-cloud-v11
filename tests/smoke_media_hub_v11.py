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


def add_user(username, role="client", allowed_dir=""):
    permissions = dict(home_cloud.DEFAULT_ADMIN_PERMS if role == "admin" else home_cloud.DEFAULT_CLIENT_PERMS)
    permissions.update({"view": True, "preview": True})
    connection = home_cloud.db()
    user_id = connection.execute(
        "INSERT INTO users(username,password_hash,role,permissions,active,created_at,allowed_dir) VALUES(?,?,?,?,1,?,?)",
        (username, generate_password_hash("testing-only"), role, json.dumps(permissions), time.time(), allowed_dir),
    ).lastrowid
    connection.commit()
    connection.close()
    return user_id


def client_for(user_id):
    client = home_cloud.app.test_client()
    with client.session_transaction() as saved:
        saved["user_id"] = user_id
        saved["activity_sid"] = f"ott-test-{user_id}-{time.time_ns()}"
    return client


def run():
    temp_dir = Path(tempfile.mkdtemp(prefix="home-cloud-v11-ott-"))
    try:
        home_cloud.ROOT = temp_dir / "library"
        movies = home_cloud.ROOT / "Movies"
        allowed = home_cloud.ROOT / "Allowed"
        movies.mkdir(parents=True)
        allowed.mkdir()
        video = movies / "Studio.Show.S01E02.Pilot.mp4"
        video.write_bytes(b"not-a-real-video")
        subtitle = movies / "Studio.Show.S01E02.Pilot.en.srt"
        subtitle.write_text("1\n00:00:01,000 --> 00:00:03,000\nHello from Home Cloud\n", encoding="utf-8")

        home_cloud.DB = temp_dir / "ott.db"
        home_cloud.THUMBS = temp_dir / "thumbnails"
        home_cloud.THUMBS.mkdir()
        home_cloud.MEDIA_CACHE = temp_dir / "media_cache"
        home_cloud.CONFIG = temp_dir / "config.json"
        home_cloud.CFG = {"asset_root": str(home_cloud.ROOT), **home_cloud.CONFIG_DEFAULTS}
        home_cloud.init_db()

        admin_id = add_user("ott-admin", "admin")
        scoped_id = add_user("scoped-viewer", allowed_dir="Allowed")
        admin = client_for(admin_id)
        scoped = client_for(scoped_id)

        connection = home_cloud.db()
        asset_id = connection.execute(
            "INSERT INTO assets(path,name,ext,size,mtime) VALUES(?,?,?,?,?)",
            ("Movies/Studio.Show.S01E02.Pilot.mp4", video.name, ".mp4", video.stat().st_size, time.time()),
        ).lastrowid
        connection.commit()
        connection.close()

        candidates = admin.get("/api/admin/media-hub/candidates").get_json()["items"]
        assert len(candidates) == 1 and candidates[0]["id"] == asset_id
        added = admin.post("/api/admin/media-hub/items", json={"asset_ids": [asset_id]}, headers=HEADERS)
        assert added.status_code == 200 and added.get_json()["added"] == [asset_id]

        hub = admin.get("/api/media-hub").get_json()
        assert hub["total"] == 1 and hub["series"][0]["title"] == "Studio Show"
        assert scoped.get("/api/media-hub").get_json()["total"] == 0

        progress = admin.post(
            f"/api/media-hub/progress/{asset_id}",
            json={"position": 120, "duration": 900}, headers=HEADERS,
        )
        assert progress.status_code == 200
        assert admin.get("/api/media-hub").get_json()["continue_watching"][0]["position"] == 120

        watchlist = admin.post(
            f"/api/media-hub/watchlist/{asset_id}", json={"enabled": True}, headers=HEADERS,
        )
        assert watchlist.status_code == 200
        assert admin.get("/api/media-hub").get_json()["watchlist"][0]["id"] == asset_id

        tracks = admin.get(f"/api/media-hub/subtitles/{asset_id}").get_json()["tracks"]
        assert len(tracks) == 1 and tracks[0]["label"] == "EN"
        converted = admin.get(tracks[0]["url"])
        assert converted.status_code == 200 and b"WEBVTT" in converted.data and b"00:00:01.000" in converted.data

        updated = admin.post(
            f"/api/admin/media-hub/items/{asset_id}",
            json={"title": "The Pilot", "description": "Local test", "category": "Drama",
                  "media_kind": "episode", "series_title": "Studio Show", "season": 1,
                  "episode": 2, "featured": True}, headers=HEADERS,
        )
        assert updated.status_code == 200
        details = admin.get(f"/api/media-hub/items/{asset_id}").get_json()
        assert details["item"]["title"] == "The Pilot" and details["item"]["featured"] is True

        removed = admin.delete(f"/api/admin/media-hub/items/{asset_id}", headers=HEADERS)
        assert removed.status_code == 200 and video.exists()
        assert admin.get("/api/media-hub").get_json()["total"] == 0
        print("Home Cloud v11 Media Hub smoke tests passed.")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    run()

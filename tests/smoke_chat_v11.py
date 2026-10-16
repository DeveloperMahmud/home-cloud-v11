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


def run():
    temp_dir = Path(tempfile.mkdtemp(prefix="home-cloud-v11-chat-"))
    try:
        home_cloud.ROOT = temp_dir / "library"
        home_cloud.ROOT.mkdir()
        home_cloud.DB = temp_dir / "chat.db"
        home_cloud.THUMBS = temp_dir / "thumbnails"
        home_cloud.THUMBS.mkdir()
        home_cloud.init_db()

        alice = home_cloud.app.test_client()
        bob = home_cloud.app.test_client()
        headers = {"X-HomeCloud-Client": "1"}

        assert alice.get("/api/chat/state").get_json()["join_required"] is True
        assert alice.post("/api/chat/join", json={"name": "Alice"}, headers=headers).status_code == 200
        assert bob.post("/api/chat/join", json={"name": "Bob"}, headers=headers).status_code == 200

        sent = alice.post("/api/chat/messages", json={"message": "Hello from the LAN"}, headers=headers)
        assert sent.status_code == 200
        message_id = sent.get_json()["id"]

        state = bob.get("/api/chat/state").get_json()
        assert state["messages"][-1]["message"] == "Hello from the LAN"
        assert state["messages"][-1]["can_delete"] is False
        assert len(state["participants"]) == 2

        assert bob.delete(f"/api/chat/messages/{message_id}", headers=headers).status_code == 403
        assert alice.post("/api/chat/messages", data={"file": "blocked"}, headers=headers).status_code == 415
        assert alice.post("/api/chat/messages", json={"message": "x" * 1001}, headers=headers).status_code == 400
        assert alice.delete(f"/api/chat/messages/{message_id}", headers=headers).status_code == 200
        assert alice.get("/api/chat/state").get_json()["messages"][-1]["deleted_at"] is not None

        connection = home_cloud.db()
        user_id = connection.execute(
            "INSERT INTO users(username,password_hash,role,permissions,active,created_at,allowed_dir) VALUES(?,?,?,?,1,?,?)",
            ("registered", generate_password_hash("testing-only"), "client", json.dumps(home_cloud.DEFAULT_CLIENT_PERMS), time.time(), ""),
        ).lastrowid
        connection.commit()
        connection.close()
        registered = home_cloud.app.test_client()
        with registered.session_transaction() as session:
            session["user_id"] = user_id
        joined = registered.post("/api/chat/join", json={}, headers=headers).get_json()
        assert joined["identity"]["name"] == "registered"

        health = alice.get("/api/health").get_json()
        assert health["internet_required"] is False
        assert health["version"] == "v11.0-dev"
        print("Home Cloud v11 LAN chat smoke tests passed.")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    run()

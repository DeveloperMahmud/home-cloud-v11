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


def add_user(username, permissions, scope="Scoped"):
    connection = home_cloud.db()
    user_id = connection.execute(
        "INSERT INTO users(username,password_hash,role,permissions,active,created_at,allowed_dir) VALUES(?,?,?,?,1,?,?)",
        (username, generate_password_hash("testing-only"), "client", json.dumps(permissions), time.time(), scope),
    ).lastrowid
    connection.commit()
    connection.close()
    return user_id


def client_for(user_id):
    client = home_cloud.app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = user_id
    return client


def run():
    temp_dir = Path(tempfile.mkdtemp(prefix="home-cloud-v101-smoke-"))
    try:
        root = temp_dir / "library"
        (root / "Scoped").mkdir(parents=True)
        home_cloud.ROOT = root
        home_cloud.DB = temp_dir / "test.db"
        home_cloud.THUMBS = temp_dir / "thumbnails"
        home_cloud.THUMBS.mkdir()
        home_cloud.init_db()

        limited = dict(home_cloud.DEFAULT_CLIENT_PERMS)
        limited["upload"] = True
        limited_id = add_user("limited-client", limited)
        replace = dict(limited)
        replace["overwrite"] = True
        replace_id = add_user("replace-client", replace)
        headers = {"X-HomeCloud-Client": "1"}

        limited_client = client_for(limited_id)
        first = limited_client.post(
            "/api/files/upload",
            data={"path": "Scoped", "conflict": "keep_both", "relative_paths": "", "files": (io.BytesIO(b"one"), "same.pdf")},
            headers=headers,
            content_type="multipart/form-data",
        )
        second = limited_client.post(
            "/api/files/upload",
            data={"path": "Scoped", "conflict": "keep_both", "relative_paths": "", "files": (io.BytesIO(b"two"), "same.pdf")},
            headers=headers,
            content_type="multipart/form-data",
        )
        denied = limited_client.post(
            "/api/files/upload",
            data={"path": "Scoped", "conflict": "replace", "relative_paths": "", "files": (io.BytesIO(b"bad"), "same.pdf")},
            headers=headers,
            content_type="multipart/form-data",
        )
        replace_client = client_for(replace_id)
        replaced = replace_client.post(
            "/api/files/upload",
            data={"path": "Scoped", "conflict": "replace", "relative_paths": "", "files": (io.BytesIO(b"replaced"), "same.pdf")},
            headers=headers,
            content_type="multipart/form-data",
        )

        assert first.status_code == 200
        assert second.status_code == 200 and (root / "Scoped" / "same (2).pdf").is_file()
        assert denied.status_code == 403
        assert replaced.status_code == 200 and (root / "Scoped" / "same.pdf").read_bytes() == b"replaced"
        print("Home Cloud v10.1 smoke tests passed.")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    run()


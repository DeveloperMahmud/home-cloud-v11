import json
import sys
import tempfile
import time
from pathlib import Path

from waitress import serve
from werkzeug.security import generate_password_hash

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as home_cloud


runtime = Path(tempfile.mkdtemp(prefix="home-cloud-v11-ui-"))
home_cloud.ROOT = runtime / "library"
home_cloud.ROOT.mkdir()
home_cloud.DB = runtime / "ui.db"
home_cloud.THUMBS = runtime / "thumbnails"
home_cloud.THUMBS.mkdir()
home_cloud.init_db()

connection = home_cloud.db()
connection.execute(
    "INSERT INTO users(username,password_hash,role,permissions,active,created_at,allowed_dir) VALUES(?,?,?,?,1,?,?)",
    (
        "codex-ui",
        generate_password_hash("home-cloud-v11"),
        "admin",
        json.dumps(home_cloud.DEFAULT_ADMIN_PERMS),
        time.time(),
        "",
    ),
)
connection.commit()
connection.close()

serve(home_cloud.app, host="127.0.0.1", port=8811, threads=4)

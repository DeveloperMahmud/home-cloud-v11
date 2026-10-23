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
home_cloud.CONFIG = runtime / "config.json"
share_root = runtime / "quick-share"
share_root.mkdir()
home_cloud.CFG = {"asset_root": str(home_cloud.ROOT), **home_cloud.CONFIG_DEFAULTS, "temp_share_root": str(share_root)}
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
samples = [
    ("Midnight Atlas", "Movies", "movie", "", 0, 0, 1, "A quiet journey through a private collection of distant places."),
    ("Neon District", "Thriller", "movie", "", 0, 0, 0, "A fast local-network premiere with no cloud required."),
    ("The Workshop", "Documentary", "movie", "", 0, 0, 0, "Behind the scenes of a creative studio."),
    ("Arrival", "Series", "episode", "Home Stories", 1, 1, 0, "The first episode."),
    ("New Rooms", "Series", "episode", "Home Stories", 1, 2, 0, "The story continues."),
    ("After Hours", "Series", "episode", "Home Stories", 1, 3, 0, "A late-night finale."),
]
from PIL import Image, ImageDraw
for index, (title, category, kind, series, season, episode, featured, description) in enumerate(samples, 1):
    filename = (f"{series}.S{season:02d}E{episode:02d}.{title}" if kind == "episode" else title).replace(" ", ".") + ".mp4"
    source = home_cloud.ROOT / filename
    source.write_bytes(b"home-cloud-ui-preview")
    poster_name = f"ui-poster-{index}.jpg"
    image = Image.new("RGB", (720, 420), (18 + index * 12, 30 + index * 8, 48 + index * 10))
    draw = ImageDraw.Draw(image)
    draw.rectangle((32, 32, 688, 388), outline=(120, 220, 190), width=3)
    draw.text((55, 320), series or title, fill=(235, 242, 248))
    image.save(home_cloud.THUMBS / poster_name, "JPEG", quality=88)
    asset_id = connection.execute(
        "INSERT INTO assets(path,name,ext,size,mtime,thumb) VALUES(?,?,?,?,?,?)",
        (filename, filename, ".mp4", source.stat().st_size, time.time() - index * 100, poster_name),
    ).lastrowid
    connection.execute(
        """INSERT INTO media_hub_items(asset_id,title,description,category,media_kind,series_title,season,episode,featured,poster,added_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (asset_id, title, description, category, kind, series, season, episode, featured, poster_name, time.time() - index * 60, time.time()),
    )
    if index == 2:
        connection.execute(
            "INSERT INTO media_hub_progress(viewer_key,asset_id,position,duration,completed,updated_at) VALUES(?,?,?,?,0,?)",
            ("user:1", asset_id, 780, 5400, time.time()),
        )
    if index == 3:
        connection.execute(
            "INSERT INTO media_hub_watchlist(viewer_key,asset_id,added_at) VALUES(?,?,?)",
            ("user:1", asset_id, time.time()),
        )
connection.commit()
connection.close()

serve(home_cloud.app, host="127.0.0.1", port=8811, threads=4)

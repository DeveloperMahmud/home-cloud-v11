import argparse
import json
import sqlite3
import tempfile
import time
import zipfile
from pathlib import Path


APP_DIR = Path(__file__).resolve().parent
DB = APP_DIR / "assets.db"


def create_backup(output_dir, keep):
    if not DB.is_file():
        raise SystemExit("assets.db was not found. Run this script from an installed Home Cloud folder.")
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    archive = output_dir / f"HomeCloud_Backup_{stamp}.zip"
    with tempfile.TemporaryDirectory(prefix="home-cloud-backup-") as temp_dir:
        snapshot = Path(temp_dir) / "assets.db"
        source = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=30)
        destination = sqlite3.connect(snapshot)
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()
        manifest = {
            "app": "Home Cloud",
            "created_at": time.time(),
            "database": "consistent SQLite snapshot",
            "includes": ["assets.db", "config.json when present", "secret.key when present"],
            "excludes": ["original asset files", "thumbnail cache"],
        }
        manifest_path = Path(temp_dir) / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
            bundle.write(snapshot, "assets.db")
            bundle.write(manifest_path, "manifest.json")
            for name in ("config.json", "secret.key"):
                item = APP_DIR / name
                if item.is_file():
                    bundle.write(item, name)
    backups = sorted(output_dir.glob("HomeCloud_Backup_*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in backups[max(1, keep):]:
        old.unlink(missing_ok=True)
    return archive


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create a consistent local Home Cloud backup.")
    parser.add_argument("--output", type=Path, default=APP_DIR / "backups")
    parser.add_argument("--keep", type=int, default=10)
    args = parser.parse_args()
    result = create_backup(args.output.resolve(), max(1, args.keep))
    print(f"Backup created: {result}")


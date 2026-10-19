import hashlib
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apply_update import apply_staged_update, rollback_backup
from homecloud_updater import UpdateError, check_github_release, inspect_update_archive, is_newer, stage_update
import app as home_cloud


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_update(path: Path, version="11.0.1", files=None):
    files = files or {"app.py": b"from pathlib import Path\ndef init_db():\n    Path('migration-ok').write_text('ok')\n"}
    package = {
        "schema": 1,
        "version": version,
        "channel": "stable",
        "install_dependencies": False,
        "files": {name: digest(data) for name, data in files.items()},
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("update-package.json", json.dumps(package))
        for name, data in files.items():
            bundle.writestr(f"payload/{name}", data)
    return package


class UpdaterSmokeTests(unittest.TestCase):
    def test_semantic_version_order(self):
        self.assertTrue(is_newer("11.0.0-beta.2", "11.0.0-beta.1"))
        self.assertTrue(is_newer("11.0.0", "11.0.0-beta.20"))
        self.assertTrue(is_newer("11.1.0", "11.0.99"))
        self.assertFalse(is_newer("11.0.0-beta.1", "11.0.0"))

    def test_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / "bad.zip"
            data = b"do not replace configuration"
            package = {"schema": 1, "version": "11.0.1", "files": {"../config.json": digest(data)}}
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("update-package.json", json.dumps(package))
                bundle.writestr("payload/../config.json", data)
            with self.assertRaises(UpdateError):
                inspect_update_archive(archive)

    def test_github_channel_selection(self):
        releases = [
            {"tag_name": "v11.0.1-beta.1", "prerelease": True, "draft": False, "published_at": "2026-01-02", "assets": [{"name": "update-manifest.json", "browser_download_url": "https://github.com/example/home/releases/download/beta/update-manifest.json"}, {"name": "HomeCloud-v11-update.zip", "browser_download_url": "https://github.com/example/home/releases/download/beta/HomeCloud-v11-update.zip"}]},
            {"tag_name": "v11.0.0", "prerelease": False, "draft": False, "published_at": "2026-01-01", "assets": [{"name": "update-manifest.json", "browser_download_url": "https://github.com/example/home/releases/download/stable/update-manifest.json"}, {"name": "HomeCloud-v11-update.zip", "browser_download_url": "https://github.com/example/home/releases/download/stable/HomeCloud-v11-update.zip"}]},
        ]
        manifests = {
            "beta": {"schema": 1, "version": "11.0.1-beta.1", "channel": "beta", "asset": "HomeCloud-v11-update.zip", "sha256": "a" * 64, "size": 100},
            "stable": {"schema": 1, "version": "11.0.0", "channel": "stable", "asset": "HomeCloud-v11-update.zip", "sha256": "b" * 64, "size": 100},
        }
        def fake_fetch(url, max_bytes=0):
            if "/releases?" in url:
                return releases
            return manifests["beta" if "/beta/" in url else "stable"]
        with patch("homecloud_updater.fetch_json", side_effect=fake_fetch):
            self.assertEqual(check_github_release("example/home", "stable", "10.9.0")["version"], "11.0.0")
            self.assertEqual(check_github_release("example/home", "beta", "11.0.0")["version"], "11.0.1-beta.1")

    def test_stage_apply_preserve_and_rollback(self):
        with tempfile.TemporaryDirectory() as temp:
            app_dir = Path(temp) / "HomeCloud"
            app_dir.mkdir()
            old_source = b"def init_db():\n    pass\n"
            (app_dir / "app.py").write_bytes(old_source)
            protected = {
                "assets.db": b"database",
                "config.json": b"configuration",
                "secret.key": b"secret",
            }
            for name, data in protected.items():
                (app_dir / name).write_bytes(data)
            archive = Path(temp) / "HomeCloud-v11-update.zip"
            make_update(archive)
            staged = stage_update(app_dir, archive)
            backup = apply_staged_update(app_dir, Path(staged["stage"]), "11.0.0")
            self.assertTrue((app_dir / "migration-ok").is_file())
            self.assertNotEqual((app_dir / "app.py").read_bytes(), old_source)
            for name, data in protected.items():
                self.assertEqual((app_dir / name).read_bytes(), data)
            rollback_backup(app_dir, backup)
            self.assertEqual((app_dir / "app.py").read_bytes(), old_source)
            for name, data in protected.items():
                self.assertEqual((app_dir / name).read_bytes(), data)

    def test_admin_update_api_and_repository_defaults(self):
        with tempfile.TemporaryDirectory() as temp:
            temp_dir = Path(temp)
            previous = {
                "APP_DIR": home_cloud.APP_DIR,
                "DB": home_cloud.DB,
                "CONFIG": home_cloud.CONFIG,
                "CFG": home_cloud.CFG,
            }
            try:
                home_cloud.APP_DIR = temp_dir
                home_cloud.DB = temp_dir / "api.db"
                home_cloud.CONFIG = temp_dir / "config.json"
                home_cloud.CFG = dict(home_cloud.CONFIG_DEFAULTS, asset_root=str(temp_dir / "library"))
                home_cloud.init_db()
                connection = home_cloud.db()
                user_id = connection.execute(
                    "INSERT INTO users(username,password_hash,role,permissions,active,created_at) VALUES(?,?,?,?,1,?)",
                    ("update-admin", "unused", "admin", json.dumps(home_cloud.DEFAULT_ADMIN_PERMS), 1),
                ).lastrowid
                connection.commit()
                connection.close()
                anonymous = home_cloud.app.test_client()
                self.assertEqual(anonymous.get("/api/admin/update/status").status_code, 401)
                admin = home_cloud.app.test_client()
                with admin.session_transaction() as session:
                    session["user_id"] = user_id
                status = admin.get("/api/admin/update/status")
                self.assertEqual(status.status_code, 200)
                self.assertEqual(status.get_json()["repo"], "DeveloperMahmud/home-cloud-v11")
                fake = {
                    "available": True,
                    "current_version": home_cloud.APP_VERSION,
                    "version": "11.0.0-beta.3",
                    "channel": "beta",
                    "package_url": "https://github.com/DeveloperMahmud/home-cloud-v11/releases/download/v11.0.0-beta.3/HomeCloud-v11-update.zip",
                    "manifest": {"schema": 1, "version": "11.0.0-beta.3", "channel": "beta", "asset": "HomeCloud-v11-update.zip", "sha256": "c" * 64, "size": 100},
                    "checked_at": 1,
                }
                with patch("homecloud_updater.check_github_release", return_value=fake):
                    checked = admin.post("/api/admin/update/check", json={"repo": "DeveloperMahmud/home-cloud-v11", "channel": "beta", "auto_check": True}, headers={"X-HomeCloud-Client": "1"})
                self.assertEqual(checked.status_code, 200)
                self.assertTrue(checked.get_json()["last_check"]["available"])
                self.assertNotIn("package_url", checked.get_json()["last_check"])
            finally:
                home_cloud.APP_DIR = previous["APP_DIR"]
                home_cloud.DB = previous["DB"]
                home_cloud.CONFIG = previous["CONFIG"]
                home_cloud.CFG = previous["CFG"]


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""Secure GitHub Release update helpers for Home Cloud.

The main application never contacts the internet unless an administrator asks
for an update check or download. Runtime data (database, configuration, secret,
assets, thumbnails and media caches) is deliberately outside the update format.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import ssl
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath


UPDATE_SCHEMA = 1
MAX_MANIFEST_BYTES = 512 * 1024
MAX_UPDATE_BYTES = 512 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 1_500 * 1024 * 1024
MAX_ARCHIVE_FILES = 10_000
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
VERSION_RE = re.compile(
    r"^v?(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)"
    r"(?:-(?P<label>[0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$"
)
TRUSTED_DOWNLOAD_HOSTS = {
    "api.github.com",
    "github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
}

ALLOWED_EXACT = {
    ".gitignore",
    "README.md",
    "V11_RELEASE_NOTES.md",
    "app.py",
    "apply_update.py",
    "backup_local.py",
    "config.example.json",
    "homecloud_updater.py",
    "requirements-ai.lock.txt",
    "requirements-ai.txt",
    "requirements.lock.txt",
    "requirements.txt",
    "serve.py",
    "HomeCloud_Backup.cmd",
    "HomeCloud_Start.cmd",
    "Configure_HomeCloud_Firewall_v11.cmd",
    "Configure_HomeCloud_Firewall_v11.ps1",
}
ALLOWED_PREFIXES = ("static/", "templates/", "update-wheelhouse/")
FORBIDDEN_NAMES = {
    "assets.db",
    "config.json",
    "secret.key",
    "deployment-state.json",
    "deployment-state-v11.json",
}


class UpdateError(RuntimeError):
    pass


def normalize_version(value: str) -> str:
    value = str(value or "").strip()
    match = VERSION_RE.fullmatch(value)
    if not match:
        raise UpdateError(f"Invalid version: {value or '(empty)'}")
    core = f"{int(match.group('major'))}.{int(match.group('minor'))}.{int(match.group('patch'))}"
    return core + (f"-{match.group('label').lower()}" if match.group("label") else "")


def _prerelease_key(label: str | None):
    if label is None:
        return (1,)
    parts = []
    for item in label.lower().split("."):
        parts.append((0, int(item)) if item.isdigit() else (1, item))
    return (0, *parts)


def version_key(value: str):
    match = VERSION_RE.fullmatch(str(value or "").strip())
    if not match:
        raise UpdateError(f"Invalid version: {value or '(empty)'}")
    return (
        int(match.group("major")),
        int(match.group("minor")),
        int(match.group("patch")),
        _prerelease_key(match.group("label")),
    )


def is_newer(candidate: str, current: str) -> bool:
    return version_key(candidate) > version_key(current)


def normalize_repo(value: str) -> str:
    value = str(value or "").strip()
    if value.startswith("https://github.com/"):
        value = value[len("https://github.com/") :].strip("/")
    if value.endswith(".git"):
        value = value[:-4]
    if not REPO_RE.fullmatch(value):
        raise UpdateError("Repository must look like owner/repository.")
    return value


def _trusted_https(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in TRUSTED_DOWNLOAD_HOSTS:
        raise UpdateError("Update URL is not a trusted GitHub HTTPS address.")


class _TrustedRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _trusted_https(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _opener():
    context = ssl.create_default_context()
    return urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=context), _TrustedRedirectHandler()
    )


def _request(url: str, accept: str = "application/vnd.github+json"):
    _trusted_https(url)
    return urllib.request.Request(
        url,
        headers={
            "Accept": accept,
            "User-Agent": "HomeCloud-v11-Updater",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )


def fetch_json(url: str, max_bytes: int = MAX_MANIFEST_BYTES):
    try:
        with _opener().open(_request(url), timeout=20) as response:
            final_url = response.geturl()
            _trusted_https(final_url)
            length = int(response.headers.get("Content-Length") or 0)
            if length > max_bytes:
                raise UpdateError("GitHub response is larger than expected.")
            raw = response.read(max_bytes + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise UpdateError(f"Could not reach GitHub: {exc}") from exc
    if len(raw) > max_bytes:
        raise UpdateError("GitHub response is larger than expected.")
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise UpdateError("GitHub returned invalid update metadata.") from exc


def download_file(url: str, destination: Path, max_bytes: int = MAX_UPDATE_BYTES) -> tuple[str, int]:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_path = destination.with_suffix(destination.suffix + ".part")
    digest = hashlib.sha256()
    total = 0
    try:
        with _opener().open(_request(url, "application/octet-stream"), timeout=60) as response:
            _trusted_https(response.geturl())
            length = int(response.headers.get("Content-Length") or 0)
            if length > max_bytes:
                raise UpdateError("Update package exceeds the 512 MB safety limit.")
            with temp_path.open("wb") as output:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise UpdateError("Update package exceeds the 512 MB safety limit.")
                    output.write(chunk)
                    digest.update(chunk)
        os.replace(temp_path, destination)
    except UpdateError:
        temp_path.unlink(missing_ok=True)
        raise
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        temp_path.unlink(missing_ok=True)
        raise UpdateError(f"Update download failed: {exc}") from exc
    return digest.hexdigest(), total


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_release_manifest(value: object) -> dict:
    if not isinstance(value, dict) or int(value.get("schema", 0)) != UPDATE_SCHEMA:
        raise UpdateError("Unsupported update manifest format.")
    version = normalize_version(value.get("version", ""))
    channel = str(value.get("channel", "")).lower()
    if channel not in {"stable", "beta"}:
        raise UpdateError("Update manifest has an invalid channel.")
    asset = str(value.get("asset", ""))
    if not re.fullmatch(r"HomeCloud-v11-update(?:-[0-9A-Za-z.-]+)?\.zip", asset):
        raise UpdateError("Update manifest has an invalid package name.")
    checksum = str(value.get("sha256", "")).lower()
    if not re.fullmatch(r"[0-9a-f]{64}", checksum):
        raise UpdateError("Update manifest has an invalid SHA-256 checksum.")
    size = int(value.get("size", 0))
    if size <= 0 or size > MAX_UPDATE_BYTES:
        raise UpdateError("Update manifest has an invalid package size.")
    result = dict(value)
    result.update(version=version, channel=channel, asset=asset, sha256=checksum, size=size)
    return result


def check_github_release(repo: str, channel: str, current_version: str) -> dict:
    repo = normalize_repo(repo)
    channel = str(channel or "stable").lower()
    if channel not in {"stable", "beta"}:
        raise UpdateError("Update channel must be stable or beta.")
    releases = fetch_json(f"https://api.github.com/repos/{repo}/releases?per_page=30", 2 * 1024 * 1024)
    if not isinstance(releases, list):
        raise UpdateError("GitHub did not return a release list.")
    eligible = [
        release for release in releases
        if isinstance(release, dict)
        and not release.get("draft")
        and (channel == "beta" or not release.get("prerelease"))
    ]
    eligible.sort(key=lambda item: str(item.get("published_at") or item.get("created_at") or ""), reverse=True)
    for release in eligible:
        assets = {str(item.get("name")): item for item in release.get("assets", []) if isinstance(item, dict)}
        manifest_asset = assets.get("update-manifest.json")
        if not manifest_asset:
            continue
        manifest_url = str(manifest_asset.get("browser_download_url") or "")
        manifest = _validate_release_manifest(fetch_json(manifest_url))
        if channel == "stable" and "-" in manifest["version"]:
            continue
        package_asset = assets.get(manifest["asset"])
        if not package_asset:
            raise UpdateError(f"Release {release.get('tag_name', '')} is missing {manifest['asset']}.")
        package_url = str(package_asset.get("browser_download_url") or "")
        _trusted_https(package_url)
        available = is_newer(manifest["version"], current_version)
        return {
            "available": available,
            "current_version": normalize_version(current_version),
            "version": manifest["version"],
            "channel": channel,
            "release_name": str(release.get("name") or release.get("tag_name") or manifest["version"]),
            "release_tag": str(release.get("tag_name") or ""),
            "release_url": str(release.get("html_url") or ""),
            "notes": str(release.get("body") or "")[:12000],
            "published_at": str(release.get("published_at") or ""),
            "package_url": package_url,
            "manifest": manifest,
            "checked_at": time.time(),
        }
    return {
        "available": False,
        "current_version": normalize_version(current_version),
        "channel": channel,
        "message": "No compatible Home Cloud update release was found.",
        "checked_at": time.time(),
    }


def _safe_relative(name: str) -> str:
    name = str(name).replace("\\", "/")
    pure = PurePosixPath(name)
    if not name or pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise UpdateError(f"Unsafe path in update package: {name}")
    return str(pure)


def _allowed_payload_path(relative: str) -> bool:
    if Path(relative).name.lower() in FORBIDDEN_NAMES:
        return False
    if relative in ALLOWED_EXACT:
        return True
    if relative.startswith(ALLOWED_PREFIXES):
        if relative.startswith("update-wheelhouse/") and not relative.lower().endswith(".whl"):
            return False
        return True
    return False


def inspect_update_archive(archive: Path, expected_manifest: dict | None = None) -> dict:
    archive = Path(archive)
    if not archive.is_file() or archive.stat().st_size > MAX_UPDATE_BYTES:
        raise UpdateError("Update package is missing or too large.")
    if expected_manifest:
        expected = _validate_release_manifest(expected_manifest)
        if archive.stat().st_size != expected["size"]:
            raise UpdateError("Downloaded update size does not match the release manifest.")
        if sha256_file(archive) != expected["sha256"]:
            raise UpdateError("Downloaded update failed SHA-256 verification.")
    try:
        with zipfile.ZipFile(archive) as bundle:
            infos = bundle.infolist()
            if len(infos) > MAX_ARCHIVE_FILES:
                raise UpdateError("Update package contains too many files.")
            if sum(item.file_size for item in infos) > MAX_UNCOMPRESSED_BYTES:
                raise UpdateError("Expanded update package is too large.")
            safe_names = [_safe_relative(item.filename.rstrip("/")) for item in infos if item.filename.rstrip("/")]
            if len(safe_names) != len(set(safe_names)):
                raise UpdateError("Update package contains duplicate paths.")
            names = set(safe_names)
            if "update-package.json" not in names:
                raise UpdateError("Update package metadata is missing.")
            package = json.loads(bundle.read("update-package.json").decode("utf-8-sig"))
            if not isinstance(package, dict) or int(package.get("schema", 0)) != UPDATE_SCHEMA:
                raise UpdateError("Unsupported update package format.")
            package["version"] = normalize_version(package.get("version", ""))
            files = package.get("files")
            if not isinstance(files, dict) or not files:
                raise UpdateError("Update package file manifest is missing.")
            payload_names = set()
            for info in infos:
                raw = info.filename.rstrip("/")
                if not raw or raw == "update-package.json" or info.is_dir():
                    continue
                safe_name = _safe_relative(raw)
                if not safe_name.startswith("payload/"):
                    raise UpdateError(f"Unexpected file in update package: {safe_name}")
                relative = safe_name[len("payload/") :]
                if not _allowed_payload_path(relative):
                    raise UpdateError(f"Update is not allowed to replace: {relative}")
                # Unix symlink file type in ZIP external attributes.
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise UpdateError("Symbolic links are not allowed in updates.")
                payload_names.add(relative)
            manifest_names = {_safe_relative(name) for name in files}
            if payload_names != manifest_names:
                raise UpdateError("Update file list does not match the package manifest.")
            for relative, expected_hash in files.items():
                if not re.fullmatch(r"[0-9a-f]{64}", str(expected_hash).lower()):
                    raise UpdateError(f"Invalid checksum for {relative}.")
                digest = hashlib.sha256(bundle.read(f"payload/{relative}")).hexdigest()
                if digest != str(expected_hash).lower():
                    raise UpdateError(f"Checksum verification failed for {relative}.")
            return package
    except (zipfile.BadZipFile, KeyError, UnicodeError, json.JSONDecodeError) as exc:
        raise UpdateError(f"Invalid update ZIP: {exc}") from exc


def stage_update(app_dir: Path, archive: Path, expected_manifest: dict | None = None) -> dict:
    app_dir = Path(app_dir).resolve()
    archive = Path(archive).resolve()
    package = inspect_update_archive(archive, expected_manifest)
    stage_root = app_dir / "updates" / "staged"
    stage_root.mkdir(parents=True, exist_ok=True)
    final_stage = stage_root / package["version"]
    temp_stage = Path(tempfile.mkdtemp(prefix="stage-", dir=stage_root))
    try:
        payload = temp_stage / "payload"
        with zipfile.ZipFile(archive) as bundle:
            for info in bundle.infolist():
                raw = info.filename.rstrip("/")
                if not raw or info.is_dir() or raw == "update-package.json":
                    continue
                relative = _safe_relative(raw)[len("payload/") :]
                target = (payload / relative).resolve()
                try:
                    target.relative_to(payload.resolve())
                except ValueError as exc:
                    raise UpdateError("Update extraction escaped the staging directory.") from exc
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(info) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
        (temp_stage / "update-package.json").write_text(json.dumps(package, indent=2), encoding="utf-8")
        if final_stage.exists():
            shutil.rmtree(final_stage)
        os.replace(temp_stage, final_stage)
    except Exception:
        shutil.rmtree(temp_stage, ignore_errors=True)
        raise
    return {
        "version": package["version"],
        "stage": str(final_stage),
        "files": len(package["files"]),
        "install_dependencies": bool(package.get("install_dependencies", False)),
        "staged_at": time.time(),
    }


def state_path(app_dir: Path) -> Path:
    return Path(app_dir) / "updates" / "update-state.json"


def load_state(app_dir: Path) -> dict:
    try:
        value = json.loads(state_path(app_dir).read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(app_dir: Path, state: dict) -> None:
    path = state_path(app_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(temp, path)


def public_state(state: dict) -> dict:
    safe = dict(state or {})
    checked = dict(safe.get("last_check") or {})
    checked.pop("package_url", None)
    safe["last_check"] = checked
    return safe


def list_backups(app_dir: Path) -> list[dict]:
    root = Path(app_dir) / "backups"
    result = []
    if not root.exists():
        return result
    for path in sorted(root.glob("update-*"), reverse=True):
        manifest_path = path / "backup-manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
            result.append({
                "id": path.name,
                "created_at": manifest.get("created_at"),
                "from_version": manifest.get("from_version", ""),
                "to_version": manifest.get("to_version", ""),
                "files": len(manifest.get("files", [])),
            })
        except (OSError, json.JSONDecodeError):
            continue
    return result[:5]

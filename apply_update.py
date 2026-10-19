"""Out-of-process Home Cloud update applier with backup and rollback."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path, PurePosixPath


def _log(app_dir: Path, message: str) -> None:
    path = app_dir / "updates" / "update.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")


def _relative(value: str) -> str:
    pure = PurePosixPath(str(value).replace("\\", "/"))
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise RuntimeError(f"Unsafe update path: {value}")
    return str(pure)


def _wait_for_process(pid: int, timeout: float = 45.0) -> None:
    if pid <= 0 or pid == os.getpid():
        return
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.25)


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_name(destination.name + ".update-new")
    shutil.copy2(source, temp)
    os.replace(temp, destination)


def _load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise RuntimeError(f"Invalid metadata: {path.name}")
    return value


def _write_state(app_dir: Path, values: dict) -> None:
    path = app_dir / "updates" / "update-state.json"
    try:
        current = _load_json(path)
    except Exception:
        current = {}
    current.update(values)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(current, indent=2), encoding="utf-8")
    os.replace(temp, path)


def _restart(app_dir: Path, python_exe: str) -> None:
    flags = 0
    if os.name == "nt":
        flags = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
    subprocess.Popen(
        [python_exe, str(app_dir / "serve.py")],
        cwd=str(app_dir),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=flags,
    )


def rollback_backup(app_dir: Path, backup_dir: Path) -> dict:
    app_dir = app_dir.resolve()
    backup_dir = backup_dir.resolve()
    try:
        backup_dir.relative_to((app_dir / "backups").resolve())
    except ValueError as exc:
        raise RuntimeError("Backup is outside the Home Cloud backup folder.") from exc
    manifest = _load_json(backup_dir / "backup-manifest.json")
    files = manifest.get("files")
    if not isinstance(files, list):
        raise RuntimeError("Backup manifest has no file list.")
    for item in reversed(files):
        relative = _relative(item["path"])
        target = app_dir / relative
        if item.get("existed"):
            source = backup_dir / "files" / relative
            if not source.is_file():
                raise RuntimeError(f"Backup file is missing: {relative}")
            _atomic_copy(source, target)
        elif target.exists() and target.is_file():
            target.unlink()
    return manifest


def apply_staged_update(app_dir: Path, stage_dir: Path, current_version: str) -> Path:
    app_dir = app_dir.resolve()
    stage_dir = stage_dir.resolve()
    try:
        stage_dir.relative_to((app_dir / "updates" / "staged").resolve())
    except ValueError as exc:
        raise RuntimeError("Update stage is outside the Home Cloud update folder.") from exc
    package = _load_json(stage_dir / "update-package.json")
    version = str(package.get("version") or "unknown")
    files = package.get("files")
    if not isinstance(files, dict) or not files:
        raise RuntimeError("Staged update contains no files.")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup_dir = app_dir / "backups" / f"update-{stamp}-{current_version}-to-{version}"
    backup_files = backup_dir / "files"
    backup_dir.mkdir(parents=True, exist_ok=False)
    records = []
    for value in files:
        relative = _relative(value)
        source = stage_dir / "payload" / relative
        if not source.is_file():
            raise RuntimeError(f"Staged file is missing: {relative}")
        target = app_dir / relative
        existed = target.is_file()
        records.append({"path": relative, "existed": existed})
        if existed:
            backup_target = backup_files / relative
            backup_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup_target)
    backup_manifest = {
        "schema": 1,
        "created_at": time.time(),
        "from_version": current_version,
        "to_version": version,
        "files": records,
    }
    (backup_dir / "backup-manifest.json").write_text(json.dumps(backup_manifest, indent=2), encoding="utf-8")
    try:
        if package.get("install_dependencies"):
            wheelhouse = stage_dir / "payload" / "update-wheelhouse"
            requirements = stage_dir / "payload" / "requirements.lock.txt"
            if not wheelhouse.is_dir() or not requirements.is_file():
                raise RuntimeError("Offline dependency payload is incomplete.")
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "--no-index", "--find-links", str(wheelhouse), "-r", str(requirements)],
                check=True,
                cwd=str(app_dir),
                stdout=subprocess.DEVNULL,
            )
        for value in files:
            relative = _relative(value)
            if relative.startswith("update-wheelhouse/"):
                continue
            _atomic_copy(stage_dir / "payload" / relative, app_dir / relative)
        subprocess.run(
            [sys.executable, "-c", "import app; app.init_db()"],
            cwd=str(app_dir),
            check=True,
            stdout=subprocess.DEVNULL,
        )
    except Exception:
        rollback_backup(app_dir, backup_dir)
        raise
    return backup_dir


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-dir", required=True)
    parser.add_argument("--stage")
    parser.add_argument("--rollback")
    parser.add_argument("--pid", type=int, default=0)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--current-version", default="unknown")
    parser.add_argument("--no-restart", action="store_true")
    args = parser.parse_args()
    app_dir = Path(args.app_dir)
    _wait_for_process(args.pid)
    try:
        if args.rollback:
            manifest = rollback_backup(app_dir, Path(args.rollback))
            _write_state(app_dir, {"status": "rolled_back", "last_error": "", "installed_version": manifest.get("from_version"), "completed_at": time.time()})
            _log(app_dir, f"Rolled back to {manifest.get('from_version', 'previous version')}.")
        elif args.stage:
            package = _load_json(Path(args.stage) / "update-package.json")
            backup = apply_staged_update(app_dir, Path(args.stage), args.current_version)
            _write_state(app_dir, {"status": "installed", "last_error": "", "installed_version": package.get("version"), "backup": backup.name, "completed_at": time.time(), "staged": None})
            _log(app_dir, f"Installed Home Cloud {package.get('version')} successfully.")
        else:
            raise RuntimeError("No update or rollback action was selected.")
    except Exception as exc:
        _write_state(app_dir, {"status": "failed", "last_error": str(exc), "completed_at": time.time()})
        _log(app_dir, f"Update failed: {exc}")
        if not args.no_restart:
            _restart(app_dir, args.python)
        return 1
    if not args.no_restart:
        _restart(app_dir, args.python)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

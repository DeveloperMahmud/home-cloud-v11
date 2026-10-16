# Home Cloud v10.1

## Security and permissions

- The legacy Upload capability is split into Upload Files, Create Folders, Rename, Copy, Move, and Replace Files.
- Existing accounts with Upload enabled retain their previous Create/Rename/Copy/Move behavior after migration; Replace Files remains off unless explicitly granted.
- A persistent random `secret.key` replaces the predictable development session secret. `VAL_SECRET` remains available as an environment override.
- The upload-dropbox guest preset disables search, preview, download, rename, copy, move, overwrite, and delete. Folder and filename visibility is retained so the guest can choose an upload destination.

## Upload and file management

- Upload conflicts can Stop with error, Skip existing, Keep both, or Replace existing.
- Keep both uses names such as `document (2).pdf`.
- Replace requires the dedicated Replace Files permission.
- File actions and bulk actions now follow their individual permissions.

## Reliability

- `serve.py` runs Home Cloud through Waitress with eight threads and a long channel timeout suitable for large uploads.
- The launcher falls back to Flask if Waitress is unavailable.
- The installer attempts to install Waitress and preserves the previous launcher and secret during backup.
- `HomeCloud_Backup.cmd` creates a consistent SQLite/config/secret ZIP and retains the newest ten backups.
- Optional scripts create or remove a Windows daily backup task at 2:00 AM.

## Upgrade

1. Stop Home Cloud.
2. Run `Install_HomeCloud_v10.cmd` from this folder.
3. Start Home Cloud normally.
4. Review existing users and guests in Admin because permissions are now more granular.

The installer creates a timestamped backup before changing Production. Live assets, thumbnails, users, configuration, and activity history are preserved.


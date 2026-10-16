# Home Cloud v10

## Included fixes

- Registered and guest accounts can upload ordinary files inside their assigned directory.
- Scoped folder uploads preserve their nested structure without being rejected as outside the assigned directory.
- Guest upload and delete capabilities are reported correctly to the Files interface.
- Uploads show percentage, transferred size, total size, and a cancel action.
- Failed and partial uploads return useful messages and are recorded in the activity log.
- Files are first written to a temporary `.uploading` file and renamed only after a complete save.
- Copy and Move use a scoped visual destination picker instead of asking users to type paths.
- Selected files and folders can be copied or moved together.
- Fresh database initialization creates migrated columns before related indexes.
- Administrators can permanently remove another client/admin account after typing its username; active sessions are cleared and activity history is preserved.

## Safe installation

1. Close the Home Cloud server window.
2. Run `Install_HomeCloud_v10.cmd`.
3. Start Home Cloud normally.
4. Test a small file, a folder upload, Copy, and Move with a scoped client account.

The installer creates a timestamped sibling backup before changing Production. It preserves the live configuration, users, database, thumbnails, and original asset library. `Rollback_HomeCloud_v10.cmd` restores the previous application code while keeping current user data.

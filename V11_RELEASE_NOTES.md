# Home Cloud v11 Preview

## Beta 2 — local-first update system and media polish

- Added an Admin Update Center with configurable GitHub repository and Stable/Beta channels.
- Added verified small update ZIP downloads, manual offline update upload, application-file backup, automatic failure rollback and restart.
- Runtime data is protected: updates cannot replace the SQLite database, machine configuration, secret key, original assets, thumbnails, media cache or AI cache.
- Added `Build_Update_Package_v11.ps1`; normal code/UI releases no longer require rebuilding all three installer packages.
- Added the advanced image viewer with previous/next navigation and slideshow, browser-compatible local video cache, polished mobile controls/fullscreen behavior, and file-type icons.
- Improved uploads with explicit conflict choices and kept folder uploads, copy/move destination picking and guest/client permissions consistent.

## Release hardening update

- Runtime networking is now strictly local-only; public-IP geolocation and public-address LAN probes were removed.
- Added pinned Standard and Full AI dependency manifests.
- Added offline wheelhouse/model preparation and an offline-only installer entry point.
- Added a clean release ZIP builder and private/generated-file verification.
- Added a privacy-safe diagnostics ZIP that excludes the database, credentials, secret key, thumbnails and original assets.
- Added fresh-database and legacy-schema migration regression coverage.
- Replaced the old v10 README with v11 fresh-install, upgrade, offline and security documentation.
- Added Online Installer, Standard Offline and Full AI Offline release variants with manifests and SHA-256 checksums.
- Full AI online installation now pre-caches its OpenCLIP model during setup for offline runtime afterward.
- The Online Installer can bootstrap a missing official Python runtime after verifying its SHA-256 checksum.
- Offline variants can bootstrap the bundled official Python 3.14.7 installer when Python is not already available.

Home Cloud v11 remains a local-first LAN application. Original assets, thumbnails, search data, accounts, chat messages, and backups stay on the host PC. Internet access is not required for normal operation.

## Highlights

- Responsive sidebar workspace and mobile navigation.
- Text-only LAN Chat for registered, guest, and optional anonymous users.
- Chat access policy, retention, moderation, unread badge, and sound preference.
- Advanced asset filters for extension, folder, date, and size plus local recent searches.
- Upload retry, conflict handling, folder uploads, visual copy/move destination picker, and recycle bin.
- User permission presets, scoped directories, upload storage limits, password reset, removal, and session revocation.
- Configurable app name, server port, thumbnail quality, and Standard or Full AI mode.
- Dashboard server/LAN status, uptime, active-user count, storage, and recent assets.
- Health diagnostics, activity log, safe app backup, database backup, and validated database restore with an automatic safety copy.
- A new v11 installer supporting lightweight `Standard` and optional `FullAI` package modes.

## Install

Run `Install_HomeCloud_v11.cmd`.

- Standard mode installs the core local server, file manager, OCR-ready metadata features, thumbnails, and LAN Chat without forcing large AI packages.
- FullAI mode additionally installs PyTorch and OpenCLIP packages for semantic search and AI visual enrichment.

The installer preserves an existing `assets.db`, `config.json`, and `secret.key`. Before an upgrade it also creates a timestamped rollback copy.

## Start

Run `HomeCloud_Start.cmd`. The console prints both the local URL and detected LAN URL. The configured port is read from `config.json`.

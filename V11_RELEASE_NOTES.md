# Home Cloud v11 Preview

## Beta 3 - Home OTT / Media Hub

- Added an offline-first Media Hub that presents admin-selected local videos in an OTT-style interface without copying or changing original files.
- Added featured titles, Movies, Series, categories, Recently Added, per-user Continue Watching and Watchlist rows.
- Added automatic `S01E02` filename detection, editable local metadata, series/season/episode grouping and next-episode autoplay.
- Added local FFmpeg poster generation, custom responsive playback, Direct Play/HTTP Range delivery and the existing browser-compatibility cache fallback.
- Added per-user resume positions and watched state plus automatic sidecar `.srt`/`.vtt` subtitle discovery and local SRT-to-WebVTT delivery.
- Added an admin catalogue manager for adding/removing indexed videos, editing metadata and choosing the featured title. Removing a title never deletes its video.
- Added keyboard focus treatment and responsive layouts validated at desktop, phone and TV-sized viewports.
- Added Media Hub database migrations and isolated regression coverage; existing databases upgrade automatically.
- Changed LAN Chat to a default-collapsed floating chat head with unread count, automatic anonymous random names, and generated avatars; visitors no longer have to type a name.
- Added admin-created QR Quick Share channels with a separately selected folder, upload/download/two-way modes, expiry, file/total quotas, device limits, activity, extension, and immediate revoke.
- Added a mobile-first, no-login LAN transfer page with upload progress, safe duplicate naming, atomic file completion, and direct downloads.
- Added automatic SQLite migrations and isolated regression coverage for temporary shares; no cloud service or internet connection is used.

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
- Floating text-only LAN Chat for registered, guest, and optional auto-named anonymous users.
- Expiring QR Quick Share channels for account-free phone-to-PC and PC-to-phone LAN transfer.
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

# Home Cloud v11

Home Cloud is a local-first visual asset library and file-transfer server for a trusted LAN. It browses and searches files already stored on the host PC; original assets are not uploaded to a cloud service.

## What works offline

After dependencies are installed, file browsing, upload/download, folders, copy/move, metadata search, thumbnails, custom image/video viewing, Home OTT / Media Hub, users, guest links, QR Quick Share, dashboard, backups, diagnostics and text-only LAN chat work without internet access. Unsupported MOV/HEVC videos can be converted locally into a browser-compatible H.264 cache; originals are never modified. Runtime LAN detection uses local host information only, and public-IP geolocation is disabled.

## Floating LAN Chat and QR Quick Share

LAN Chat starts as a small floating chat head instead of occupying the workspace. Registered users keep their account name; anonymous LAN visitors receive a private random name and avatar automatically, so typing a name is optional. Messages remain text-only and are stored in the local SQLite database.

Administrators can open the floating **Quick Share** button, choose a separate PC folder, select upload-only, download-only, or two-way mode, and set expiry, per-file, total-transfer, and device limits. Home Cloud produces a QR code and temporary LAN link that a phone can open without an account. Incoming files are staged and atomically completed, duplicate names are preserved with a numbered suffix, activity is visible to the administrator, and revoke takes effect immediately. Quick Share does not expose the main asset library unless the administrator explicitly selects that folder.

## Home OTT / Media Hub

Administrators can open **Media Hub -> Manage library** and publish selected indexed videos into a private OTT-style catalogue. Media Hub includes featured content, Movies, Series, categories, Recently Added, per-user Continue Watching and Watchlist rows. Video files stay in the configured asset library; catalogue removal never deletes the original.

Series filenames such as `Show.Name.S01E02.Title.mkv` are grouped automatically and metadata remains editable locally. Posters are extracted by the bundled/local FFmpeg runtime. Direct Play uses HTTP Range requests for fast seeking and concurrent LAN viewing; unsupported formats can use the existing local H.264 compatibility cache. Sidecar subtitles named like `Movie.en.srt` or `Movie.bn.vtt` are discovered automatically. Watch progress, favorites and metadata stay in SQLite on the host PC.

Standard mode is the recommended lightweight setup. Full AI mode adds OpenCLIP semantic image search. Full AI packages and model weights must be prepared once on an internet-connected build PC or already exist in the local offline bundle. OCR is local and optional; install Tesseract separately if OCR is required.

## Fresh Windows installation

Requirements:

- Windows 10/11
- Python 3.11 or newer available in `PATH`

Run:

```bat
Install_HomeCloud_v11.cmd
```

Choose Standard or Full AI. The installer uses `%LOCALAPPDATA%\HomeCloud`, creates a new configuration and database when none exists, and creates `Documents\Home Cloud Library` as the initial asset folder. It does not copy a development `assets.db`, `config.json` or `secret.key` from the source package.

Dependencies are installed into an application-owned `.venv`; Home Cloud does not modify or depend on another project's Python environment after installation.

Start the server with:

```bat
%LOCALAPPDATA%\HomeCloud\HomeCloud_Start.cmd
```

The console prints both the local URL and detected LAN URL. If another LAN device cannot connect, run `Configure_HomeCloud_Firewall_v11.cmd` as Administrator and allow only the Private network profile.

## Fully offline dependency package

On an internet-connected Windows build PC:

```powershell
powershell -ExecutionPolicy Bypass -File .\Prepare_Offline_Package_v11.ps1 -Mode Standard
```

For Full AI, including the OpenCLIP model cache:

```powershell
powershell -ExecutionPolicy Bypass -File .\Prepare_Offline_Package_v11.ps1 -Mode FullAI
```

Copy the entire folder to the offline PC and run `Install_HomeCloud_v11_Offline.cmd`. The prepared offline release includes the checksum-verified official Python installer and uses it automatically only when Python is not already available.

## Release package choices

`Build_Installer_Packages_v11.ps1` produces three packages from the same source tree:

- `HomeCloud-v11-Online-Installer.zip` — small download; Python (when missing), Standard or Full AI dependencies are downloaded during installation. Downloads are verified where an upstream checksum is available. A Full AI online installation also downloads the OpenCLIP model once and stores it locally, so normal runtime remains offline afterward.
- `HomeCloud-v11-Standard-Offline.zip` — includes the official 64-bit Python installer and all Standard dependency wheels. No internet is needed on a fresh Windows PC.
- `HomeCloud-v11-FullAI-Offline.zip` — includes Python, Standard dependencies, Full AI dependencies and the local OpenCLIP model cache. No internet is needed on a fresh Windows PC.

Build all prepared variants with:

```powershell
powershell -ExecutionPolicy Bypass -File .\Build_Installer_Packages_v11.ps1 -Package All
```

Each package contains a `package-manifest.json`; release ZIP checksums are written to `dist\SHA256SUMS.txt`. OCR remains an optional local Tesseract feature and is not silently installed.

## Upgrade behavior

When `%LOCALAPPDATA%\HomeCloud` already exists, the installer creates a timestamped safety backup and preserves:

- `assets.db`
- `config.json`
- `secret.key`

Database migrations run automatically and retain existing users, assets, embeddings and settings.

### Small GitHub Release updates

For the complete public release-only repository workflow, Bengali web/CLI instructions, auto-update setup and troubleshooting, see [`GITHUB_RELEASE_AUTO_UPDATE_GUIDE_BN.md`](GITHUB_RELEASE_AUTO_UPDATE_GUIDE_BN.md).

After the first v11 installation, an administrator can open **Settings → Update Center** and select Stable or Beta. The official `DeveloperMahmud/home-cloud-v11` repository is preconfigured and can be changed there if the project is forked. Checking/downloading an update is the only part that needs internet; file browsing, transfer, search, viewers, users and LAN Chat remain fully offline.

For each new version, change the application version and build these two small release assets:

```powershell
powershell -ExecutionPolicy Bypass -File .\Build_Update_Package_v11.ps1 -Version 11.0.1 -Channel stable
```

Upload `dist\HomeCloud-v11-update.zip` and `dist\update-manifest.json` to the matching GitHub Release. The app verifies the release SHA-256 plus every internal file checksum, rejects unsafe paths, backs up only changed application files, preserves all runtime data, installs, and restarts. A manual offline update ZIP can be uploaded in the same Update Center. The most recent application backup can also be rolled back there.

Use `-IncludeDependencies` only when a release changes Python dependencies; this adds the Standard offline wheelhouse to the update ZIP. Regular code/UI updates stay small. Full installer packages only need rebuilding for new users or when you want refreshed all-in-one installers.

## Private files that must not be committed

The included `.gitignore` excludes the runtime database, secret key, machine configuration, thumbnails, generated video compatibility cache, deployment state, offline payloads and build output. If any of these files were committed by an older repository, remove them from Git tracking before publishing; `.gitignore` does not untrack files already committed.

## Verification

Run the automated checks:

```powershell
python tests\smoke_v101.py
python tests\smoke_chat_v11.py
python tests\smoke_v11_features.py
python tests\smoke_media_hub_v11.py
python tests\smoke_temp_share_v11.py
python tests\smoke_release_v11.py
python tests\smoke_updater_v11.py
powershell -ExecutionPolicy Bypass -File .\Verify_Release_v11.ps1
```

Create a clean source ZIP with no runtime database or secrets:

```powershell
powershell -ExecutionPolicy Bypass -File .\Build_Release_v11.ps1
```

Use `-IncludeOfflinePayload` only after preparing an offline wheelhouse. Offline AI packages can be very large and are better distributed as a separate release asset.

## Security scope

Home Cloud is intended for a trusted private LAN. It binds to the host network so other local devices can connect. Do not expose its port directly to the public internet. Use strong account passwords and keep guest and Quick Share links short-lived. A Quick Share QR/link is a temporary bearer credential: anyone on the LAN who has it can use the channel until it expires or is revoked.

# Home Cloud v11 - GitHub Release ও Auto-Update Guide

এই setup-এ দুটি repository আলাদা থাকবে:

1. **Source repository** - private থাকতে পারে; Home Cloud-এর code এখানে থাকবে।
2. **Release-only repository** - public থাকবে; installer এবং update package শুধু GitHub Releases-এ থাকবে।

প্রস্তাবিত release repository:

```text
DeveloperMahmud/home-cloud-v11-releases
```

> Public release repository-তে source code push করা বাধ্যতামূলক নয়। একটি README commit থাকলেই release/tag তৈরি করা যাবে।

---

## Part 1 - নতুন release-only repository তৈরি

1. GitHub-এ sign in করুন।
2. `https://github.com/new` খুলুন।
3. Owner হিসেবে নিজের account নির্বাচন করুন।
4. Repository name দিন:

   ```text
   home-cloud-v11-releases
   ```

5. Description দিতে পারেন:

   ```text
   Official installers and verified updates for Home Cloud v11
   ```

6. Visibility অবশ্যই **Public** করুন।
7. **Add a README file** চালু রাখুন।
8. `.gitignore` ও License এখন প্রয়োজন নেই।
9. **Create repository** চাপুন।

Repository তৈরি হলে exact নামটি পাঠাতে হবে:

```text
OWNER/REPOSITORY
```

উদাহরণ:

```text
DeveloperMahmud/home-cloud-v11-releases
```

এরপর Home Cloud-এর default updater address এই নতুন repository-তে পরিবর্তন করে beta.3 package rebuild করতে হবে।

---

## Part 2 - Repository নাম পাওয়ার পর code-এ যা বদলাতে হবে

এই তিন জায়গায় একই release repository বসাতে হবে:

```text
app.py
config.example.json
Install_HomeCloud_v11.ps1
```

Installed পুরোনো copy-র `config.json` preserve হয়। তাই প্রথমবার beta.3 install/update করার পরে Admin Settings-এর Update Center-এও নতুন repository নাম save করতে হবে। চাইলে beta.3 code-এ পুরোনো default repository থেকে নতুন repository-তে one-time migration যোগ করা যাবে।

এই ধাপটি Codex দিয়ে করানো সবচেয়ে নিরাপদ। Repository তৈরি করে শুধু exact `OWNER/REPOSITORY` পাঠান।

---

## Part 3 - beta.3 release files build করা

PowerShell অথবা Windows Terminal খুলে চালান:

```powershell
cd "D:\Web Works\home cloud v11"
```

প্রথমে verification:

```powershell
python tests\smoke_media_hub_v11.py
python tests\smoke_v101.py
python tests\smoke_v11_features.py
python tests\smoke_chat_v11.py
python tests\smoke_updater_v11.py
python tests\smoke_release_v11.py
powershell -ExecutionPolicy Bypass -File .\Verify_Release_v11.ps1
```

### শুধু ছোট auto-update package

```powershell
powershell -ExecutionPolicy Bypass -File .\Build_Update_Package_v11.ps1 -Version 11.0.0-beta.3 -Channel beta
```

এতে তৈরি হবে:

```text
dist\HomeCloud-v11-update.zip
dist\update-manifest.json
dist\SHA256SUMS.txt
```

### নতুন user-এর জন্য Online Installer

```powershell
powershell -ExecutionPolicy Bypass -File .\Build_Installer_Packages_v11.ps1 -Package Online
```

### তিনটি installer একসঙ্গে

Offline payload আগে থেকেই প্রস্তুত থাকলে:

```powershell
powershell -ExecutionPolicy Bypass -File .\Build_Installer_Packages_v11.ps1 -Package All
```

এতে পাওয়া যাবে:

```text
dist\HomeCloud-v11-Online-Installer.zip
dist\HomeCloud-v11-Standard-Offline.zip
dist\HomeCloud-v11-FullAI-Offline.zip
dist\HomeCloud-v11-update.zip
dist\update-manifest.json
dist\SHA256SUMS.txt
```

Clean source archive প্রয়োজন হলে:

```powershell
powershell -ExecutionPolicy Bypass -File .\Build_Release_v11.ps1
```

এটি তৈরি করে:

```text
dist\HomeCloud-v11-source.zip
```

Source private রাখতে চাইলে `HomeCloud-v11-source.zip` public Release-এ upload করবেন না।

---

## Part 4 - GitHub Web UI দিয়ে beta.3 Release তৈরি

1. নতুন public release repository খুলুন।
2. ডান পাশের **Releases** চাপুন।
3. **Draft a new release** চাপুন।
4. Tag লিখুন:

   ```text
   v11.0.0-beta.3
   ```

5. **Create new tag on publish** নির্বাচন করুন।
6. Target branch হিসেবে `main` রাখুন।
7. Release title দিন:

   ```text
   Home Cloud v11.0.0 Beta 3 - Home OTT
   ```

8. এটি beta হওয়ায় **Set as a pre-release** নির্বাচন করুন।
9. Release notes box-এ `V11_RELEASE_NOTES.md`-এর Beta 3 অংশ paste করতে পারেন।
10. Notes box-এর ভিতরে ZIP paste করবেন না। নিচের **Attach binaries by dropping them here or selecting them** area-তে files upload করবেন।

Auto-update চালাতে বাধ্যতামূলক assets:

```text
HomeCloud-v11-update.zip
update-manifest.json
```

Strongly recommended:

```text
SHA256SUMS.txt
HomeCloud-v11-Online-Installer.zip
```

Offline distribution প্রয়োজন হলে আরও দিন:

```text
HomeCloud-v11-Standard-Offline.zip
HomeCloud-v11-FullAI-Offline.zip
```

সব upload সম্পূর্ণ হওয়ার পরে **Publish release** চাপুন। অসম্পূর্ণ অবস্থায় Draft রাখাই ভালো।

> GitHub নিজে যে “Source code (zip)” দেখায় সেটি release-only repository-র README/source snapshot। সেটি Home Cloud-এর আসল source package নয়। আসল source public করতে চাইলে আলাদা `HomeCloud-v11-source.zip` upload করতে হবে।

---

## Part 5 - GitHub CLI দিয়ে একই Release তৈরি (Optional)

GitHub CLI install ও login করা থাকলে PowerShell-এ:

```powershell
cd "D:\Web Works\home cloud v11"
gh auth login
```

শুধু updater ও online installer দিয়ে beta release:

```powershell
gh release create v11.0.0-beta.3 `
  ".\dist\HomeCloud-v11-update.zip" `
  ".\dist\update-manifest.json" `
  ".\dist\SHA256SUMS.txt" `
  ".\dist\HomeCloud-v11-Online-Installer.zip" `
  --repo "DeveloperMahmud/home-cloud-v11-releases" `
  --title "Home Cloud v11.0.0 Beta 3 - Home OTT" `
  --notes-file ".\V11_RELEASE_NOTES.md" `
  --prerelease
```

Repository নাম আলাদা হলে `--repo` value বদলাতে হবে। Offline packages যোগ করতে চাইলে publish করার আগে একই command-এ paths যোগ করুন অথবা পরে:

```powershell
gh release upload v11.0.0-beta.3 `
  ".\dist\HomeCloud-v11-Standard-Offline.zip" `
  ".\dist\HomeCloud-v11-FullAI-Offline.zip" `
  --repo "DeveloperMahmud/home-cloud-v11-releases"
```

---

## Part 6 - Home Cloud-এ Update Center configure

Admin account দিয়ে Home Cloud খুলুন:

```text
Admin -> Settings -> Update Center
```

Repository field:

```text
DeveloperMahmud/home-cloud-v11-releases
```

Channel:

```text
Beta
```

Enable করুন:

```text
Automatically check when an admin opens Settings
```

তারপর:

1. **Save Settings**
2. আবার Settings খুলুন।
3. **Check for Updates**
4. নতুন version পেলে **Download & Verify**
5. verification সফল হলে **Install & Restart**

Auto-check সর্বোচ্চ প্রতিদিন একবার Settings খোলার সময় চলে। নিরাপত্তার জন্য update নিজে থেকে download/install হয় না; admin confirm করলে তবেই install হয়।

Install-এর সময়:

- package SHA-256 যাচাই হয়;
- ZIP-এর প্রতিটি application file যাচাই হয়;
- unsafe path reject হয়;
- changed application files backup হয়;
- `assets.db`, `config.json`, `secret.key`, original assets, thumbnails এবং media cache preserve হয়;
- server restart হয়;
- সমস্যা হলে Update Center থেকে rollback করা যায়।

---

## Part 7 - পরবর্তী beta.4, beta.5 update workflow

প্রতিটি নতুন update-এ version অবশ্যই বাড়াতে হবে। উদাহরণ:

```text
বর্তমান: 11.0.0-beta.3
পরবর্তী: 11.0.0-beta.4
```

Workflow:

1. Source code update করুন।
2. `app.py`-এর `APP_VERSION` নতুন version করুন।
3. tests চালান।
4. নতুন update package build করুন:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\Build_Update_Package_v11.ps1 -Version 11.0.0-beta.4 -Channel beta
   ```

5. নতুন GitHub Release তৈরি করুন:

   ```text
   Tag: v11.0.0-beta.4
   Pre-release: Yes
   ```

6. নতুন release-এ upload করুন:

   ```text
   HomeCloud-v11-update.zip
   update-manifest.json
   SHA256SUMS.txt
   ```

7. পুরোনো release-এর files overwrite করবেন না। প্রতিটি version-এর জন্য নতুন tag/release ব্যবহার করুন।

Installer package শুধু তখন rebuild করা দরকার যখন:

- নতুন user-কে সর্বশেষ complete installer দিতে চান;
- Python dependency বদলেছে;
- installer script বদলেছে;
- Standard/Full AI offline bundle refresh করতে চান।

সাধারণ Python/HTML/CSS/JS update-এর জন্য ছোট update ZIP-ই যথেষ্ট।

---

## Part 8 - Stable Release workflow

Beta final হলে version দিন, উদাহরণ:

```text
11.0.0
```

Build করুন:

```powershell
powershell -ExecutionPolicy Bypass -File .\Build_Update_Package_v11.ps1 -Version 11.0.0 -Channel stable
```

Release tag:

```text
v11.0.0
```

Stable release-এ **Pre-release** নির্বাচন করবেন না। Stable channel-এর clients pre-release skip করে; Beta channel-এর clients stable ও beta দুটোই দেখতে পারে।

---

## Part 9 - Common errors

### `HTTP Error 404: Not Found`

সম্ভাব্য কারণ:

- repository private;
- repository name ভুল;
- owner name ভুল;
- release publish হয়নি, Draft আছে;
- updater এখনও পুরোনো repository ব্যবহার করছে।

### `No compatible Home Cloud update release was found`

যাচাই করুন:

- release Draft নয়;
- `update-manifest.json` upload হয়েছে;
- manifest-এর channel সঠিক;
- Stable channel হলে version-এ `-beta` নেই;
- release repository public।

### `Release is missing HomeCloud-v11-update.zip`

Manifest আছে, কিন্তু manifest-এর `asset` নামের ZIP নেই বা filename বদলানো হয়েছে। Filename অপরিবর্তিত রাখুন:

```text
HomeCloud-v11-update.zip
```

### Notes field-এ 25 MB error

ZIP-টি release notes editor-এ paste/upload করা হয়েছে। Release page-এর আলাদা binary attachment area ব্যবহার করুন অথবা `gh release upload` চালান।

### Installed app নতুন repo দেখছে না

Admin Settings -> Update Center-এ repository manually বদলে Save Settings করুন। Installer/update runtime `config.json` preserve করে।

---

## Final checklist

```text
[ ] Release-only repo public
[ ] Repo name app/installer defaults-এ বসানো
[ ] Version beta.3
[ ] Tests passed
[ ] HomeCloud-v11-update.zip uploaded
[ ] update-manifest.json uploaded
[ ] SHA256SUMS.txt uploaded
[ ] Online installer uploaded
[ ] Beta release marked Pre-release
[ ] Release published, Draft নয়
[ ] Update Center repo/channel saved
[ ] Check -> Download & Verify -> Install & Restart tested
[ ] Rollback backup visible
```

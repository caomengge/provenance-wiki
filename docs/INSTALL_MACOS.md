# Installing Provenance Wiki on a Mac

> **Current status: testing build only.** The Mac app is not yet signed with an
> Apple Developer ID or notarized by Apple. macOS therefore blocks it the first
> time it is opened, and you have to allow it once in System Settings. This is
> fine for evaluation but is **not** how the public release will work. Please
> don't redistribute testing builds.

## Requirements

- A Mac with Apple Silicon (M1 or later). Intel Macs are not supported by this build.
- macOS 15 Sequoia or later.
- An internet connection, plus your own **Anthropic API key** and **Voyage AI API key**
  (see [API keys](#api-keys)).

You do **not** need Python, Node.js, Terminal, or any developer tools.

## Install (testing build)

1. Download `ProvenanceWiki-<version>-macOS-arm64-TESTING-ONLY-unsigned.dmg`.
2. Double-click the `.dmg`. Drag **Provenance Wiki (Testing)** onto **Applications**.
3. Open **Applications** and double-click **Provenance Wiki (Testing)**.
   macOS says it could not verify the app. Click **Done**. Don't move it to the Trash.
4. Open **System Settings → Privacy & Security**. Scroll down to the message about
   "Provenance Wiki (Testing)" and click **Open Anyway**. Confirm with your password,
   then click **Open Anyway** again.

You only need to do step 4 once for each new testing version.

## First launch

- The app opens in your default web browser at an address like `http://127.0.0.1:5100`.
- A small **P** icon appears in the menu bar at the top of the screen. Its menu has
  **Open Provenance Wiki**, **Open Photos Folder**, **Open Archive Folder** and **Quit**.
- The **Welcome** screen first asks **where to store your archive**. The suggested location is
  on your Mac's main drive; to use a larger or external drive, choose **Choose another folder**.
  A folder named `Provenance Wiki Archive` is created there, holding everything (database,
  photographs, logs). macOS may ask to allow access to that folder or drive: click **Allow**.
- It then asks for your two API keys. You can skip those and come back any time via
  **Settings** in the sidebar.

Closing the browser tab does not stop the app. To stop it, choose **Quit** from the
menu-bar icon, or **Settings → Quit Provenance Wiki**. If you closed the browser tab
while the app is still running, use **Open Provenance Wiki** from the menu-bar icon.

## API keys

| Key | Used for | Where to get it |
|---|---|---|
| Anthropic | Reading photographs (transcription, extraction) and answering questions | https://console.anthropic.com/ |
| Voyage AI | Semantic search and finding evidence for answers | https://www.voyageai.com/ |

These are **API keys**, not Claude.ai or ChatGPT subscriptions. Each provider bills
your own account for usage; Provenance Wiki includes no credit.

Keys are stored in your **macOS Keychain** (entries named "ProvenanceWiki"), never in a
file. To change or remove a key, use **Settings**.

**Test connection**: the Anthropic check is free. The Voyage AI check embeds two words,
a tiny billable request.

## What is sent where

- Photographs you ingest → Anthropic (to be read).
- Document text → Voyage AI (to build the search index).
- Your question and the selected evidence → Anthropic (to write the answer).

Nothing else leaves your Mac. Confirm your institution allows sending restricted
material to these services before processing it.

## Where your archive is stored

| What | Location |
|---|---|
| Your archive: database, photographs (`photos/`), thumbnails, backups, logs | The folder you chose on first launch. Suggested: `~/Library/Application Support/ProvenanceWiki/` |
| A small settings folder recording where your archive is | `~/Library/Application Support/ProvenanceWiki Config/` |

**Settings → Where your archive is stored** shows the exact location, its size and the free
space. Use **Open Photos Folder** from the menu-bar icon or the sidebar to add photographs,
then click **Ingest Photos**.

**Back up** by copying the whole archive folder while the app is not running. It contains both
the database and the photographs.

### Moving your archive to another drive

Photographs take most of the space (roughly 5–15 GB per 1,000 photos). To move the archive to a
larger or external drive: **Settings → Where your archive is stored → Change location…**, choose a
folder, check the summary, and click **Copy and switch**. Provenance Wiki copies everything,
checks the copy file by file, and only then switches to it; if anything goes wrong, nothing
changes. Afterwards it offers to delete the old copy (you confirm first).

Folders synced by OneDrive, Google Drive, Dropbox, iCloud Drive or Box, and network drives, are
not allowed: syncing can corrupt the archive's database.

If the archive is on an external drive that isn't connected when you open Provenance Wiki, it
says so and offers **Try again** (after connecting the drive) or **Locate my archive…** (if the
drive or folder now has a different name). It never starts a new, empty archive without asking.

With unsigned testing builds, macOS may ask again for access to an external drive or to
Documents/Desktop after each update. That stops once the app is signed.


## Upgrade

Quit the app, then drag the new version into Applications and choose **Replace**. Your
archive and keys are kept.

## Uninstall

Quit the app and drag it from Applications to the Trash. **Your archive is not
deleted.** To remove it too, delete your archive folder (see Settings for where it is),
`~/Library/Application Support/ProvenanceWiki Config/`, and the "ProvenanceWiki" entries in
the Keychain Access app. Testing builds before this version also kept logs in
`~/Library/Logs/ProvenanceWiki/`.

## Existing source (developer) installs

The app does not touch a source checkout's `data/` and `photos/` folders. A tool to
import an existing source archive is planned for a later build.

## Known limitations of the testing build

- Not signed or notarized (see above). Expect the one-time "Open Anyway" step.
- Apple Silicon and macOS 15+ only.
- Automatic backup before database upgrades is not yet implemented: test with
  sample documents or copies.

## Troubleshooting

- **Page doesn't open:** click the menu-bar **P** icon → **Open Provenance Wiki**.
- **"Semantic search is unavailable":** your Voyage AI key is missing or rejected. Check it in
  **Settings → Test connection**.
- **Logs:** `~/Library/Logs/ProvenanceWiki/provenance-wiki.log`. API keys are masked in logs.

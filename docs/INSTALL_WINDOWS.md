# Installing Provenance Wiki on Windows

> **Current status: testing build only.** The Windows installer is not yet
> code-signed. Windows SmartScreen may therefore warn that it comes from an
> unknown publisher. This is expected for testing builds; please don't
> redistribute them.

## Requirements

- Windows 10 or 11, 64-bit (including Windows 11 Home).
- No administrator rights needed.
- An internet connection, plus your own **Anthropic API key** and **Voyage AI API key**
  (see [API keys](#api-keys)).

You do **not** need Python, Node.js, a command prompt, or any developer tools.

## Install (testing build)

1. Download the testing build (see the README's "Get Provenance Wiki" section). You get a `.zip`;
   open it and extract `ProvenanceWiki-Setup-<version>-Windows-x64-TESTING-ONLY-unsigned.exe`.
2. Double-click the installer. If Windows shows **"Windows protected your PC"**, click
   **More info**, then **Run anyway**.
3. Follow the steps. The installer is for your user account only and doesn't ask for an
   administrator password. You can tick **Create a desktop shortcut** if you want one.
4. At the end, leave **Open Provenance Wiki (Testing) now** ticked and click **Finish**.

## First launch

- The app opens in your default web browser at an address like `http://127.0.0.1:5100`.
- A small **P** icon appears in the system tray at the bottom-right of the screen (you may need
  to click the **^** arrow to see it). Its menu has **Open Provenance Wiki**,
  **Open Photos Folder**, **Open Archive Folder** and **Quit**.
- The **Welcome** screen asks for your two API keys. You can skip it and come back any time via
  **Settings** in the sidebar.

Closing the browser tab does not stop the app. To stop it, choose **Quit** from the tray icon,
or **Settings → Quit Provenance Wiki**. Opening it from the Start menu while it is already
running just opens its page again.

## API keys

| Key | Used for | Where to get it |
|---|---|---|
| Anthropic | Reading photographs (transcription, extraction) and answering questions | https://console.anthropic.com/ |
| Voyage AI | Semantic search and finding evidence for answers | https://www.voyageai.com/ |

These are **API keys**, not Claude.ai or ChatGPT subscriptions. Each provider bills your own
account for usage; Provenance Wiki includes no credit.

Keys are stored in **Windows Credential Manager** (entries mentioning "ProvenanceWiki"), never
in a file. To change or remove a key, use **Settings**.

**Test connection**: the Anthropic check is free. The Voyage AI check embeds two words,
a tiny billable request.

## What is sent where

- Photographs you ingest → Anthropic (to be read).
- Document text → Voyage AI (to build the search index).
- Your question and the selected evidence → Anthropic (to write the answer).

Nothing else leaves your computer. Confirm your institution allows sending restricted material
to these services before processing it.

## Where things are stored

| What | Location |
|---|---|
| The program | `%LOCALAPPDATA%\Programs\Provenance Wiki\` |
| Your archive (database, photographs, thumbnails) | `%LOCALAPPDATA%\ProvenanceWiki\` |
| Photos to ingest | `%LOCALAPPDATA%\ProvenanceWiki\photos\` |
| Logs | `%LOCALAPPDATA%\ProvenanceWiki\logs\` |

Tip: paste `%LOCALAPPDATA%\ProvenanceWiki` into the File Explorer address bar to open the
archive folder, or use **Open Archive Folder** from the tray icon.

**Back up** by copying the whole `%LOCALAPPDATA%\ProvenanceWiki` folder while the app is not
running. It contains both the database and the photographs.

## Upgrade

Run the new installer. It replaces the program and keeps your archive and keys. If Provenance
Wiki is running, the installer closes it first.

## Uninstall

**Settings → Apps → Installed apps → Provenance Wiki (Testing) → Uninstall.**

The uninstaller removes the program and shortcuts. It then asks whether to also delete your
research archive. **No** is the default and keeps it. Choose **Yes** only if you are sure:
deletion cannot be undone. API keys stay in Credential Manager until you remove them in
Settings (before uninstalling) or in Windows Credential Manager.

## Known limitations of the testing build

- Not code-signed: expect a SmartScreen warning the first time.
- 64-bit Windows 10/11 only.
- Automatic backup before database upgrades is not yet implemented: test with sample
  documents or copies.

## Troubleshooting

- **Page doesn't open:** click the tray **P** icon → **Open Provenance Wiki**, or start it again
  from the Start menu.
- **"Semantic search is unavailable":** your Voyage AI key is missing or rejected. Check it in
  **Settings → Test connection**.
- **Logs:** `%LOCALAPPDATA%\ProvenanceWiki\logs\provenance-wiki.log`. API keys are masked in logs.

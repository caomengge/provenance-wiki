# Provenance Archive Wiki

A local research wiki for museum archival provenance research. Processes photos of historical documents using Claude AI to extract structured provenance data, then lets you search, browse, visualize, and ask questions about your archive.

---

## What This Does

- **Ingests photos** of auction catalogs, receipts, letters, and other provenance documents
- **Extracts** structured data (entities, dates, transactions, locations) using Claude Vision
- **Preserves** Chinese, German, French, Hebrew, and other non-English text exactly
- **Lets you search** by keyword or semantic similarity
- **Shows a timeline** of ownership events
- **Draws a network graph** of relationships between people, objects, and institutions
- **Answers questions** using a RAG (retrieval-augmented generation) pipeline grounded in your documents
- **Exports PDFs** of timelines, entity histories, and document dossiers
- **Tracks every ingestion** in a per-run log (processed / failed / re-queued / grouped)
- **Records edit history** of every document and group, viewable in a Show History panel
- **Works with Claude Desktop** via the built-in MCP server

---

## Quick Start

### Step 1 — What You Need

- A Mac (macOS 12+)
- Python 3.11 or later
- Node.js 18 or later (for the web interface)
- An Anthropic API key (get one at [console.anthropic.com](https://console.anthropic.com))

To check if Python and Node are installed, open **Terminal** and run:
```
python3 --version
node --version
```

If either is missing, download from [python.org](https://python.org) and [nodejs.org](https://nodejs.org).

---

### Step 2 — Get Your API Key

1. Go to [console.anthropic.com](https://console.anthropic.com)
2. Sign in or create an account
3. Click **API Keys** → **Create Key**
4. Copy the key (it starts with `sk-ant-...`)

---

### Step 3 — Set Up the App

Open **Terminal** and paste these commands one at a time:

```bash
# Go to the project folder
cd ~/Projects/DCADP_Provenance_wiki

# Copy the example config
cp .env.example .env
```

Now open `.env` in TextEdit:
```bash
open -a TextEdit .env
```

Replace `YOUR_KEY_HERE` with your actual API key. Save and close the file.

---

### Step 4 — Add Your Photos

Copy your document photos (JPG, PNG, TIFF, WEBP) into the `photos/` folder:

```bash
open photos/
```

You can drag photos directly into this folder.

---

### Step 5 — Start the App

In Terminal:

```bash
./run.sh
```

The first time this runs, it will:
1. Set up a Python environment (takes 1–2 minutes)
2. Install the web interface (takes 1–2 minutes)
3. Start the app on http://localhost:5100

Once you see `Starting Flask server on http://localhost:5100`, open your browser and go to:

**http://localhost:5100**

---

### Step 6 — Ingest Your Photos

1. Click **⊕ Ingest Photos** in the left sidebar
2. Watch the progress messages
3. When done, the gallery will populate with your documents

Each photo is sent to Claude for analysis. Ingestion runs four photos in parallel by default (tunable via `INGEST_WORKERS` in `backend/config.py`); a 500-photo backlog typically finishes in ~25 minutes. You can safely stop and restart — already-processed photos are skipped, and any that previously failed are retried automatically.

---

## Troubleshooting

**"ANTHROPIC_API_KEY not set" error**
- Open `.env` and check your key is there without quotes
- The key should look like: `ANTHROPIC_API_KEY=sk-ant-...`

**Photos not appearing after ingest**
- Make sure photos are in the `photos/` folder (not a sub-folder)
- Supported formats: `.jpg`, `.jpeg`, `.png`, `.tif`, `.tiff`, `.webp`
- Check the sidebar for error messages during ingest

**Port 5100 already in use**
- Open Terminal and run: `lsof -ti:5100 | xargs kill -9`
- Then restart with `./run.sh`

**Frontend shows "not built yet"**
- Make sure Node.js is installed: `node --version`
- Run: `cd frontend && npm install && npm run build && cd ..`

---

## Features

### Gallery
Browse all your documents in a grid or list. Filter by source archive, entity (typeahead — type a few letters), or key evidence. The filter and action bar pins to the top while you scroll. Select multiple documents to export as a PDF dossier or to **group** them into a single multi-page document.

### Document Detail
- View the full-resolution image (click to zoom)
- See extracted entities, transactions, dates, and locations
- Add and edit transactions inline
- Add researcher annotations (auto-saved)
- Toggle key evidence flag (★)
- Manage tags with custom colors
- Link related documents

### Document Groups
Select multiple pages in the Gallery and click **Group** to combine them into one multi-page document. Grouping merges the already-extracted page data and runs a tiny text-only Claude call to synthesize a unified title and description — no images re-uploaded, ~1–2 seconds. If the result reads poorly for a particular group, click **↺ Re-extract** on the group page to run a full vision re-extraction.

### Search
- **Keyword search**: SQLite FTS5 with BM25 ranking, highlights matching terms
- **Semantic search**: vector similarity over document embeddings
- Filter by source archive; click an entity from the Entities page to filter results to that entity (chip with × to clear)

### Timeline
Chronological view of all provenance events. Filter by date range or by entity (typeahead). Export as PDF.

### Network Graph
Interactive force-directed graph showing relationships between documents, people, objects, and institutions. Click nodes to see details.

### Research Assistant (Q&A)
Ask natural-language provenance questions. The system retrieves relevant documents and generates a grounded answer citing specific source documents.

Example questions:
- "Who owned this artwork before 1939?"
- "Which auction houses appear in the records?"
- "Is there a gap in the provenance between 1933 and 1945?"

### Ingest Log
Every ingestion run is recorded. Open **Ingest Log** in the sidebar to see, per run:

- **Ingested** — files successfully extracted (click through to the document)
- **Failed** — files that errored, with the error message
- **Re-queued** — previously-failed files that this run retried
- **Grouped** — groups whose pages came from this run

The list auto-refreshes while a run is in progress. The Skipped count (files already in the DB) is shown on the run summary but isn't recorded per-file — it would bloat the log without adding value.

### History (Audit Log)
Click **▸ Show History** at the bottom of any document or group detail page to see every edit recorded against that record: per-field updates with old and new values, entity link/unlink, document links, deletions, group membership changes, and the ingestion run that originally produced the record. Long text fields (transcription, description, annotation) are truncated at 1000 characters in the history to keep the table compact.

### Entities & Merging
The Entities page lists every unique person, object, and institution across the archive. Edit any row to rename, change type, merge into another entity, or delete it. The **Merge into…** picker is a search-as-you-type field so you can find candidates anywhere in the archive — it isn't limited to entities on the current page.

### Transaction Quality
The extractor prompt asks Claude to only emit a transaction for a specific exchange event (sale, purchase, auction, donation, bequest, consignment, gift). Vague past-ownership mentions become entities with `role="previous owner"` instead.

Even so, the LLM sometimes emits weak rows. Every transaction in the UI carries a **quality score** — how many of `seller`, `buyer`, `date`, `price`, `auction_house` are filled, shown as a coloured badge `0/5`–`5/5`:

- **3–5/5** (green): a well-anchored transaction
- **2/5** (amber): borderline — judge by content
- **0–1/5** (red): probably a stray mention or stub. Edit it to add missing anchors, or delete it.

A **Hide weak (< 2/5)** checkbox at the top of each Transactions list sweeps low-scoring rows from view without deleting them.

The backend can also auto-drop weak rows at ingest time via `config.TRANSACTION_MIN_SCORE` (default `0` = keep everything; set to `2` to silently drop weak transactions on ingest).

To bulk-delete existing weak transactions across the whole archive:

```bash
cp data/provenance.db data/provenance.db.bak
.venv/bin/python backend/scripts/clean_weak_transactions.py --min-score 2          # dry-run
.venv/bin/python backend/scripts/clean_weak_transactions.py --min-score 2 --apply  # delete
```

Deletions are recorded in `audit_events` so they can be inspected (or reversed via the SQL backup) later.

---

## PDF Export

- **Timeline PDF**: Click "Export PDF" on the Timeline page
- **Entity History**: On any entity detail page, click "Export PDF"
- **Selected Documents**: In the Gallery, select documents and click "Export Selected (PDF)"

---

## Claude Desktop Integration (MCP)

The app includes an MCP server that lets Claude Desktop search and query your archive directly.

### Setup

1. Make sure the app is running (`./run.sh`)
2. Open Terminal and run:
```bash
echo $HOME
```
Note the path shown (e.g., `/Users/yourname`)

3. Open the Claude Desktop config file:
```bash
open -a TextEdit "$HOME/Library/Application Support/Claude/claude_desktop_config.json"
```

If the file doesn't exist, create it with this content (replace `/Users/yourname/Projects/DCADP_Provenance_wiki` with your actual path):

```json
{
  "mcpServers": {
    "provenance-archive": {
      "command": "/Users/yourname/Projects/DCADP_Provenance_wiki/.venv/bin/python",
      "args": [
        "/Users/yourname/Projects/DCADP_Provenance_wiki/backend/mcp_server.py"
      ],
      "env": {
        "PYTHONPATH": "/Users/yourname/Projects/DCADP_Provenance_wiki/backend"
      }
    }
  }
}
```

4. Restart Claude Desktop

5. In Claude Desktop, you can now ask:
   - "Search my provenance archive for Sotheby's"
   - "Get the details of document #5"
   - "Who is entity 'Paul Rosenberg' in the archive?"
   - "Ask the archive: what happened to artworks in Germany between 1933 and 1945?"

### Available MCP Tools

| Tool | Description |
|------|-------------|
| `search_provenance` | Search documents by keyword or semantic similarity |
| `get_document` | Get full detail for a document by ID |
| `analyze_entity` | Get provenance network for a person/institution |
| `ask_question` | RAG-powered Q&A against your archive |

---

## Performance Notes

The app is designed to stay responsive at ~500 documents on a laptop:

- **Thumbnail cache** — gallery cards serve 480px JPEG thumbnails (~50 KB) instead of the full iPhone original (~5 MB). Generated on ingest, regenerated automatically on rotation, served with `Cache-Control` + `ETag` so the browser skips re-downloads.
- **Parallel ingestion** — four concurrent Claude vision calls by default; tunable via `INGEST_WORKERS`. Honours `Retry-After` on rate-limit responses.
- **Smart grouping** — page-merge instead of re-uploading images for a fresh vision call.
- **Lean detail responses** — internal fields (`embedding_json`, `raw_claude_response`) stay in the database and are not shipped to the frontend on every detail load.
- **Server-side entity search** — filter dropdowns query the database as you type instead of preloading every entity.

---

## Data Storage

All data is stored locally:
- `photos/` — your original image files (never modified)
- `data/thumbnails/` — generated JPEG thumbnails keyed by sha256 (regeneratable; safe to delete)
- `data/provenance.db` — SQLite database with all extracted data (documents, groups, entities, tags, transactions, ingestion runs, audit/history events)
- `data/mcp.log` — MCP server log

To back up your research, copy the entire project folder. At minimum, back up `data/provenance.db` and `photos/`; thumbnails will rebuild on demand. The cleanup scripts (e.g. `clean_weak_transactions.py`) operate on `data/provenance.db`, so take a copy of it before running any `--apply` step.

---

## Privacy

- All processing runs on your Mac
- Photos are sent to Anthropic's API for analysis (covered by Anthropic's privacy policy)
- No data is stored remotely; all results are saved in `data/provenance.db`
- Your API key is stored only in `.env` (never shared)

---

## Updating

To get the latest version, replace the project files. Your data in `data/provenance.db` and `photos/` is preserved.

---

*Provenance Archive Wiki — Built for museum archival research*

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

Each photo is sent to Claude for analysis. Processing takes a few seconds per photo. You can safely stop and restart — already-processed photos are skipped.

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
Browse all your documents in a grid or list. Filter by tag, date, or key evidence. Select multiple documents to export as a PDF dossier.

### Document Detail
- View the full-resolution image (click to zoom)
- See extracted entities, transactions, dates, and locations
- Add researcher annotations (auto-saved)
- Toggle key evidence flag (★)
- Manage tags with custom colors
- Link related documents

### Search
- **Keyword search**: SQLite FTS5 with BM25 ranking, highlights matching terms
- **Semantic search**: vector similarity over document embeddings
- Filter by tag or entity

### Timeline
Chronological view of all provenance events. Filter by date range. Export as PDF.

### Network Graph
Interactive force-directed graph showing relationships between documents, people, objects, and institutions. Click nodes to see details.

### Research Assistant (Q&A)
Ask natural-language provenance questions. The system retrieves relevant documents and generates a grounded answer citing specific source documents.

Example questions:
- "Who owned this artwork before 1939?"
- "Which auction houses appear in the records?"
- "Is there a gap in the provenance between 1933 and 1945?"

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

## Data Storage

All data is stored locally:
- `photos/` — your original image files (never modified)
- `data/provenance.db` — SQLite database with all extracted data
- `data/mcp.log` — MCP server log

To back up your research, copy the entire `DCADP_Provenance_wiki/` folder.

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

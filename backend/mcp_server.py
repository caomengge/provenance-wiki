"""
mcp_server.py – Model Context Protocol (MCP) server for Provenance Archive Wiki.

Exposes the provenance research archive to Claude Desktop and other MCP clients
via four research tools:

  search_provenance   – keyword or semantic search over all documents
  get_document        – retrieve full detail for a document by ID
  analyze_entity      – get an entity's provenance network + document list
  ask_question        – run the full RAG Q&A pipeline

Start this server alongside Flask:
    python backend/mcp_server.py

The server listens on stdio (default MCP transport) and communicates via
JSON-RPC as per the Model Context Protocol specification.

Register in Claude Desktop at:
  ~/Library/Application Support/Claude/claude_desktop_config.json
(see README.md for full instructions)
"""

import json
import logging
import sys
from pathlib import Path

# Add backend/ to path so we can import config and modules
sys.path.insert(0, str(Path(__file__).parent))

logger = logging.getLogger(__name__)


def _setup():
    """Lazy setup: init DB once when first tool is called."""
    from modules.db import init_db
    from config import DATA_DIR
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    init_db()


def main():
    """Run the MCP server using the mcp Python library."""
    try:
        from mcp.server import Server
        from mcp.server.stdio import stdio_server
        from mcp import types
    except ImportError:
        print(
            "ERROR: 'mcp' package not installed.\n"
            "Run: pip install mcp\n",
            file=sys.stderr,
        )
        sys.exit(1)

    _setup()

    server = Server("provenance-archive-wiki")

    # ── Tool definitions ──────────────────────────────────────────────────────

    @server.list_tools()
    async def list_tools():
        return [
            types.Tool(
                name="search_provenance",
                description=(
                    "Search the provenance archive for documents matching a query. "
                    "Returns a list of matching documents with titles, dates, and descriptions. "
                    "Use this to find relevant historical records before asking detailed questions."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query (supports English, Chinese, German, etc.)",
                        },
                        "mode": {
                            "type": "string",
                            "enum": ["keyword", "semantic"],
                            "description": "Search mode: 'keyword' for FTS5, 'semantic' for vector similarity",
                            "default": "keyword",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of results (default 20, max 50)",
                            "default": 20,
                        },
                    },
                    "required": ["query"],
                },
            ),
            types.Tool(
                name="get_document",
                description=(
                    "Retrieve the full detail of a provenance document by its ID. "
                    "Returns title, dates, description, all entities mentioned, "
                    "transaction history, tags, and researcher annotations."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "doc_id": {
                            "type": "integer",
                            "description": "The document ID (from search_provenance results)",
                        }
                    },
                    "required": ["doc_id"],
                },
            ),
            types.Tool(
                name="analyze_entity",
                description=(
                    "Get the provenance network for a person, artwork, or institution. "
                    "Returns the entity's full document list, co-occurring entities, "
                    "and transaction history — useful for tracing ownership chains."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "entity_id": {
                            "type": "integer",
                            "description": "Entity ID (from document detail or entity search)",
                        },
                        "entity_name": {
                            "type": "string",
                            "description": "Alternative: search by name instead of ID",
                        },
                    },
                },
            ),
            types.Tool(
                name="ask_question",
                description=(
                    "Ask a natural-language provenance research question. "
                    "The system retrieves relevant documents and generates a grounded answer "
                    "citing specific source documents. Best for complex ownership history questions."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": "Your provenance research question",
                        }
                    },
                    "required": ["question"],
                },
            ),
        ]

    # ── Tool implementations ──────────────────────────────────────────────────

    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        from config import ANTHROPIC_API_KEY

        if name == "search_provenance":
            from modules.search import search_documents
            q      = arguments.get("query", "")
            mode   = arguments.get("mode", "keyword")
            limit  = min(int(arguments.get("limit", 20)), 50)
            result = search_documents(q, mode=mode, page=1, per_page=limit)
            docs   = result.get("results", [])

            if not docs:
                text = f"No documents found matching '{q}'."
            else:
                lines = [f"Found {result['total']} document(s) matching '{q}':\n"]
                for d in docs:
                    date = d.get("date_depicted") or d.get("date_range_start") or "date unknown"
                    lines.append(f"• [Doc #{d['id']}] {d.get('title','Untitled')} ({date})")
                    if d.get("snippet"):
                        lines.append(f"  …{d['snippet']}…")
                text = "\n".join(lines)

            return [types.TextContent(type="text", text=text)]

        elif name == "get_document":
            from modules.db import get_db, row_to_dict, rows_to_list
            doc_id = int(arguments["doc_id"])

            with get_db() as conn:
                doc = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
                if not doc:
                    return [types.TextContent(type="text", text=f"Document #{doc_id} not found.")]
                doc = row_to_dict(doc)
                doc.pop("embedding_json", None)

                entities = rows_to_list(conn.execute(
                    """SELECT e.name, e.type, de.role FROM document_entities de
                       JOIN entities e ON e.id=de.entity_id WHERE de.document_id=?""",
                    (doc_id,)
                ).fetchall())

                txns = rows_to_list(conn.execute(
                    "SELECT * FROM transactions WHERE document_id=? ORDER BY date",
                    (doc_id,)
                ).fetchall())

            lines = [
                f"# Document #{doc['id']}: {doc.get('title','Untitled')}",
                f"**Date:** {doc.get('date_depicted') or doc.get('date_range_start') or 'Unknown'}",
                f"**Location:** {doc.get('location') or 'Unknown'}",
                f"**Medium:** {doc.get('medium') or 'Unknown'}",
                f"**Key Evidence:** {'Yes' if doc.get('is_key_evidence') else 'No'}",
                "",
                f"**Description:** {doc.get('description') or ''}",
            ]

            if doc.get("annotation"):
                lines += ["", f"**Researcher Note:** {doc['annotation']}"]

            if entities:
                lines += ["", "**Entities Mentioned:**"]
                for e in entities:
                    lines.append(f"  • {e['name']} ({e['type']}) — {e.get('role','')}")

            if txns:
                lines += ["", "**Transactions:**"]
                for t in txns:
                    parts = [t.get("date", "?")]
                    if t.get("seller"):  parts.append(f"Seller: {t['seller']}")
                    if t.get("buyer"):   parts.append(f"Buyer: {t['buyer']}")
                    if t.get("price"):   parts.append(f"{t.get('currency','')} {t['price']}")
                    lines.append("  • " + " | ".join(parts))

            return [types.TextContent(type="text", text="\n".join(lines))]

        elif name == "analyze_entity":
            from modules.db import get_db, row_to_dict, rows_to_list, normalize_entity_name

            entity_id   = arguments.get("entity_id")
            entity_name = arguments.get("entity_name")

            with get_db() as conn:
                if entity_id:
                    entity = conn.execute("SELECT * FROM entities WHERE id=?", (entity_id,)).fetchone()
                elif entity_name:
                    norm = normalize_entity_name(entity_name)
                    entity = conn.execute(
                        "SELECT * FROM entities WHERE normalized_name LIKE ? LIMIT 1",
                        (f"%{norm}%",)
                    ).fetchone()
                else:
                    return [types.TextContent(type="text", text="Provide entity_id or entity_name.")]

                if not entity:
                    return [types.TextContent(type="text", text="Entity not found.")]

                entity = row_to_dict(entity)
                eid = entity["id"]

                docs = rows_to_list(conn.execute(
                    """SELECT d.id, d.title, d.date_depicted, de.role
                       FROM document_entities de JOIN documents d ON d.id=de.document_id
                       WHERE de.entity_id=? ORDER BY d.date_depicted NULLS LAST LIMIT 30""",
                    (eid,)
                ).fetchall())

                co = rows_to_list(conn.execute(
                    """SELECT e.name, e.type, COUNT(*) as cnt
                       FROM document_entities de1
                       JOIN document_entities de2 ON de2.document_id=de1.document_id AND de2.entity_id!=de1.entity_id
                       JOIN entities e ON e.id=de2.entity_id
                       WHERE de1.entity_id=?
                       GROUP BY e.id ORDER BY cnt DESC LIMIT 10""",
                    (eid,)
                ).fetchall())

            lines = [
                f"# Entity: {entity['name']} ({entity['type'].capitalize()})",
                f"**Documents:** {len(docs)}",
                "",
                "**Document Appearances:**",
            ]
            for d in docs:
                date = d.get("date_depicted") or "?"
                lines.append(f"  • [Doc #{d['id']}] {d.get('title','Untitled')} ({date}) — {d.get('role','')}")

            if co:
                lines += ["", "**Frequently Co-occurs With:**"]
                for c in co:
                    lines.append(f"  • {c['name']} ({c['type']}) — {c['cnt']} document(s)")

            return [types.TextContent(type="text", text="\n".join(lines))]

        elif name == "ask_question":
            from modules.qa import answer_question
            if not ANTHROPIC_API_KEY:
                return [types.TextContent(type="text", text="ANTHROPIC_API_KEY not set.")]

            question = arguments.get("question", "")
            result   = answer_question(question, ANTHROPIC_API_KEY)

            lines = [
                result["answer"],
                "",
                f"**Confidence:** {result.get('confidence','?')}",
                f"**Source documents:** {', '.join(f'#{i}' for i in result.get('sources', []))}",
            ]
            return [types.TextContent(type="text", text="\n".join(lines))]

        else:
            return [types.TextContent(type="text", text=f"Unknown tool: {name}")]

    # ── Run ───────────────────────────────────────────────────────────────────
    import asyncio

    async def _run():
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )

    asyncio.run(_run())


if __name__ == "__main__":
    main()

# Copyright 2026 Michael R. Douglas. MIT License.
"""Search interface for the declaration index."""

from __future__ import annotations

import json
import logging

from .db import IndexDB

logger = logging.getLogger(__name__)


def search(db: IndexDB, query: str = "", kind: str | None = None,
           topic: str | None = None, repo: str | None = None,
           since: str | None = None, type_mention: str | None = None,
           limit: int = 10, offset: int = 0,
           output_json: bool = False) -> str:
    """Search declarations and return formatted output."""
    if query:
        results = db.fts_search(
            query=query, limit=limit, offset=offset,
            kind=kind, topic=topic, repo=repo, since=since,
            type_mention=type_mention,
        )
    else:
        results = db.structured_search(
            limit=limit, offset=offset,
            kind=kind, topic=topic, repo=repo, since=since,
            type_mention=type_mention,
        )

    if output_json:
        return json.dumps(results, indent=2, default=str)

    return format_results(results, query=query)


def format_results(results: list[dict], query: str = "") -> str:
    """Format search results as a human-readable table."""
    if not results:
        msg = f'No results found for "{query}"' if query else "No results found"
        return msg

    lines = []
    lines.append(f"Found {len(results)} result(s):\n")

    # Determine column widths
    max_name = min(max(len(r.get("name", "")) for r in results), 60)
    max_kind = max(len(r.get("kind", "")) for r in results)

    header = f"{'Name':<{max_name}}  {'Kind':<{max_kind}}  {'Module'}"
    lines.append(header)
    lines.append("-" * len(header))

    for r in results:
        name = r.get("name", "")
        if len(name) > max_name:
            name = name[:max_name - 3] + "..."
        kind = r.get("kind", "")
        module = r.get("module", "")
        repo_name = r.get("repo_name", "")

        # Truncate module
        if len(module) > 50:
            module = "..." + module[-47:]

        line = f"{name:<{max_name}}  {kind:<{max_kind}}  {module}"
        if repo_name and repo_name != "mathlib4":
            line += f"  [{repo_name}]"
        lines.append(line)

    # Show docstrings for first few results
    lines.append("")
    for r in results[:5]:
        doc = r.get("docstring", "")
        if doc:
            name = r.get("name", "")
            doc_preview = doc[:120].replace("\n", " ")
            if len(doc) > 120:
                doc_preview += "..."
            lines.append(f"  {name}:")
            lines.append(f"    {doc_preview}")

    return "\n".join(lines)

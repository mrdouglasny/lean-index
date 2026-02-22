"""Download and parse the Mathlib declaration cache from the docs site."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from ..db import IndexDB

logger = logging.getLogger(__name__)

HEADER_DATA_URL = "https://leanprover-community.github.io/mathlib4_docs/declarations/header-data.bmp"
MATHLIB_REPO_URL = "https://github.com/leanprover-community/mathlib4"


def download_mathlib_cache(cache_dir: Path, force: bool = False) -> Path:
    """Download header-data.bmp if not cached or stale (>8 hours)."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / "header-data.bmp"
    meta_file = cache_dir / "meta.json"

    # Check if we have a recent cache
    if not force and cache_file.exists() and meta_file.exists():
        with open(meta_file) as f:
            meta = json.load(f)
        age_hours = (time.time() - meta.get("downloaded_at", 0)) / 3600
        if age_hours < 8:
            logger.info(f"Using cached Mathlib data ({age_hours:.1f}h old)")
            return cache_file

    logger.info(f"Downloading Mathlib declaration cache from {HEADER_DATA_URL}...")
    resp = requests.get(HEADER_DATA_URL, stream=True, timeout=120)
    resp.raise_for_status()

    total = 0
    sha = hashlib.sha256()
    with open(cache_file, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1024 * 1024):
            f.write(chunk)
            sha.update(chunk)
            total += len(chunk)

    logger.info(f"Downloaded {total / 1024 / 1024:.1f} MB")

    with open(meta_file, "w") as f:
        json.dump({
            "downloaded_at": time.time(),
            "size_bytes": total,
            "sha256": sha.hexdigest(),
        }, f)

    return cache_file


def parse_mathlib_cache(cache_file: Path) -> tuple[list[dict], list[dict]]:
    """Parse header-data.bmp JSON into declarations and modules.

    Actual format: {decl_name: {"header": html_string, "info": {doc, docLink, kind, line, name, sourceLink}}}

    Returns (declarations, modules).
    """
    logger.info(f"Parsing {cache_file}...")
    with open(cache_file) as f:
        data = json.load(f)

    declarations = []

    if not isinstance(data, dict):
        logger.warning(f"Unexpected top-level type: {type(data)}")
        return [], []

    for decl_name, entry in data.items():
        if not isinstance(entry, dict):
            continue

        info = entry.get("info", {})
        header_html = entry.get("header", "")

        if isinstance(info, dict):
            kind = info.get("kind", "def")
            doc = info.get("doc", "")
            doc_link = info.get("docLink", "")
            source_link = info.get("sourceLink", "")
            line = info.get("line", 0)
        else:
            kind = "def"
            doc = ""
            doc_link = ""
            source_link = ""
            line = 0

        # Extract module from docLink
        module = _extract_module(doc_link or source_link, decl_name)

        # Extract plain-text type signature from header HTML
        type_sig = _html_to_text(header_html) if header_html else ""

        # Strip the "def/theorem/lemma Name" prefix from type_sig
        # The header includes kind + name + args + type
        type_sig = _clean_type_sig(type_sig, decl_name, kind)

        declarations.append({
            "name": decl_name,
            "kind": kind,
            "docstring": doc[:2000] if doc else "",
            "type_sig": type_sig[:2000] if type_sig else "",
            "type_html": header_html[:5000] if header_html else "",
            "module": module,
            "line": line if isinstance(line, int) else 0,
            "file_path": module.replace(".", "/") + ".lean" if module else "",
        })

    logger.info(f"Parsed {len(declarations)} declarations")
    return declarations, []


def _clean_type_sig(text: str, name: str, kind: str) -> str:
    """Clean the type signature extracted from HTML header.

    The header text looks like: "def ADEInequality.A (r : ℕ+) : Multiset ℕ+"
    We want to extract just the signature part after the name.
    """
    if not text:
        return ""

    # Remove the kind prefix
    for prefix in ["def ", "theorem ", "lemma ", "structure ", "class ",
                    "instance ", "abbrev ", "inductive ", "opaque "]:
        if text.startswith(prefix):
            text = text[len(prefix):]
            break

    # Remove the declaration name prefix
    short_name = name.rsplit(".", 1)[-1] if "." in name else name
    if text.startswith(name):
        text = text[len(name):]
    elif text.startswith(short_name):
        text = text[len(short_name):]

    return text.strip()


def _html_to_text(html: str) -> str:
    """Convert HTML type signature to plain text."""
    if not html or not ("<" in html or "&" in html):
        return html
    try:
        soup = BeautifulSoup(html, "html.parser")
        return soup.get_text().strip()
    except Exception:
        return html


def _extract_module(link: str, name: str) -> str:
    """Extract module name from a doc or source link.

    E.g., "Mathlib/Algebra/Lie/Basic.html#LieRing" -> "Mathlib.Algebra.Lie.Basic"
    """
    if not link:
        # Infer from name: LieAlgebra.foo -> Mathlib.Algebra.Lie.Basic (fallback to name prefix)
        parts = name.rsplit(".", 1)
        return parts[0] if "." in name else ""

    # Strip leading ./
    if link.startswith("./"):
        link = link[2:]
    # Strip fragment
    if "#" in link:
        link = link.split("#")[0]
    # Strip .html extension
    if link.endswith(".html"):
        link = link[:-5]
    # Convert path separators to dots
    return link.replace("/", ".")


def index_mathlib(db: IndexDB, cache_dir: Path, force: bool = False,
                  topics: list | None = None) -> dict:
    """Full Mathlib indexing: download cache, parse, insert into DB.

    Args:
        db: Database instance.
        cache_dir: Cache directory for downloaded files.
        force: Force re-download even if cache is fresh.
        topics: If provided, only index declarations that match at least one topic.
                This avoids storing irrelevant Mathlib content (e.g., number theory
                in a Lie algebra index).
    """
    cache_file = download_mathlib_cache(cache_dir, force=force)
    declarations, modules = parse_mathlib_cache(cache_file)

    total_parsed = len(declarations)

    # Filter to topic-relevant declarations if topics are configured
    if topics:
        from ..match import match_declaration
        filtered = []
        for decl in declarations:
            for topic in topics:
                matched, _, _ = match_declaration(decl, topic)
                if matched:
                    filtered.append(decl)
                    break
        logger.info(f"Filtered {total_parsed} -> {len(filtered)} declarations matching {len(topics)} topics")
        declarations = filtered

    # Upsert Mathlib repo
    repo_id = db.upsert_repo(
        url=MATHLIB_REPO_URL,
        name="mathlib4",
        source="mathlib_cache",
        is_mathlib=True,
        description="Mathlib: the math library for Lean 4",
    )

    # Bulk insert declarations
    inserted, updated = db.bulk_upsert_declarations(repo_id, declarations)

    # Mark stale declarations
    current_names = {d["name"] for d in declarations}
    removed = db.mark_stale_declarations(repo_id, current_names)

    db.update_repo_indexed(repo_id, f"cache-{int(time.time())}")

    result = {
        "total_parsed": total_parsed,
        "declarations": len(declarations),
        "inserted": inserted,
        "updated": updated,
        "removed": removed,
    }
    logger.info(f"Mathlib indexed: {result}")
    return result



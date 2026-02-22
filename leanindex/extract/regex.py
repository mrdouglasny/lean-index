"""Regex-based extraction of Lean 4 declarations from source files.

Parses .lean files without building — handles 95%+ of declarations.
Ported from auto-lie/scripts/extract_mathlib_rich.py with enhancements.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)

DECL_KEYWORDS = {"def", "theorem", "lemma", "structure", "class", "instance",
                 "abbrev", "inductive", "opaque"}
MODIFIERS = {"private", "protected", "noncomputable", "unsafe", "partial", "scoped"}
BAD_CHARS = set('(:=[]«»{}<>')


def extract_file(path: str | Path, module: str = "") -> list[dict]:
    """Extract declarations from a single Lean file.

    Args:
        path: Path to the .lean file.
        module: Dotted module name (e.g., "Mathlib.Algebra.Lie.Basic").

    Returns:
        List of declaration dicts with keys:
        name, kind, module, docstring, type_sig, file_path, line, is_noncomputable
    """
    path = Path(path)
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError as e:
        logger.warning(f"Cannot read {path}: {e}")
        return []

    decls = []
    ns_stack: list[str] = []
    current_docstring: str | None = None
    in_docstring = False
    docstring_lines: list[str] = []
    current_attrs: list[str] = []

    for i, raw_line in enumerate(lines):
        line = raw_line.rstrip("\n")
        stripped = line.strip()

        # Track docstrings: /-- ... -/
        if "/--" in stripped and not in_docstring:
            in_docstring = True
            docstring_lines = []
            after = stripped.split("/--", 1)[1]
            if "-/" in after:
                doc_text = after.split("-/")[0].strip()
                docstring_lines.append(doc_text)
                in_docstring = False
                current_docstring = " ".join(docstring_lines)
            else:
                docstring_lines.append(after.strip())
            continue
        elif in_docstring:
            if "-/" in stripped:
                before = stripped.split("-/")[0].strip()
                if before:
                    docstring_lines.append(before)
                in_docstring = False
                current_docstring = " ".join(docstring_lines)
            else:
                docstring_lines.append(stripped)
            continue

        # Track attributes: @[simp, norm_cast, ...]
        if stripped.startswith("@["):
            current_attrs.append(stripped)
            # If attribute spans multiple lines, keep accumulating
            if "]" not in stripped:
                continue
            continue

        # Track namespaces
        m = re.match(r"^namespace\s+(\S+)", stripped)
        if m:
            ns_stack.append(m.group(1))
            current_docstring = None
            current_attrs = []
            continue
        m = re.match(r"^end\s+(\S+)", stripped)
        if m:
            if ns_stack and ns_stack[-1] == m.group(1):
                ns_stack.pop()
            current_docstring = None
            current_attrs = []
            continue

        # Match declarations
        words = stripped.split()
        idx = 0
        is_noncomputable = False
        while idx < len(words) and words[idx] in MODIFIERS:
            if words[idx] == "noncomputable":
                is_noncomputable = True
            idx += 1

        if idx < len(words) and words[idx] in DECL_KEYWORDS:
            kind = words[idx]
            if idx + 1 < len(words):
                name = words[idx + 1]
                # Clean up name
                name = name.rstrip(":")
                if not name or name.startswith("_") or any(c in name for c in BAD_CHARS):
                    current_docstring = None
                    current_attrs = []
                    continue

                # Build fully qualified name
                if ns_stack:
                    fqn = ".".join(ns_stack) + "." + name
                else:
                    fqn = name

                # Extract type signature (first line + continuation)
                sig = _extract_signature(lines, i, stripped, name)

                decls.append({
                    "name": fqn,
                    "kind": kind,
                    "module": module,
                    "docstring": (current_docstring or "")[:2000],
                    "type_sig": sig[:2000],
                    "file_path": str(path),
                    "line": i + 1,
                    "is_noncomputable": is_noncomputable,
                })

            current_docstring = None
            current_attrs = []
            continue

        # Reset docstring on non-declaration, non-blank, non-comment lines
        if stripped and not stripped.startswith("--") and not stripped.startswith("#"):
            if not (idx < len(words) and words[idx] in DECL_KEYWORDS):
                current_docstring = None
                current_attrs = []

    return decls


def _extract_signature(lines: list[str], start_idx: int,
                       first_line: str, name: str) -> str:
    """Extract type signature from declaration, handling multi-line signatures."""
    # Find position after the name on the first line
    try:
        name_pos = first_line.index(name)
        rest = first_line[name_pos + len(name):].strip()
    except ValueError:
        return ""

    # Collect signature text
    sig_parts = [rest]

    # Check if we need continuation lines
    # A signature is incomplete if it has unbalanced parens/brackets or ends with certain tokens
    if _needs_continuation(rest):
        indent = len(lines[start_idx]) - len(lines[start_idx].lstrip())
        for j in range(start_idx + 1, min(start_idx + 20, len(lines))):
            cont = lines[j].rstrip("\n")
            cont_stripped = cont.strip()
            if not cont_stripped:
                continue
            cont_indent = len(cont) - len(cont.lstrip())
            # Stop at less-indented or same-indented declaration-like lines
            if cont_indent <= indent and cont_stripped.split()[0] in (DECL_KEYWORDS | MODIFIERS | {"namespace", "end", "section", "@[", "/--"}):
                break
            sig_parts.append(cont_stripped)
            if not _needs_continuation(cont_stripped):
                break

    sig = " ".join(sig_parts)

    # Extract just the type (after the first colon, before := or where)
    # Handle implicit/explicit params: skip (x : T) [inst : I] {h : P} blocks
    # then extract the return type after the final colon
    clean_sig = sig

    # Remove terminators
    for terminator in [" := by", " :=", " where"]:
        if terminator in clean_sig:
            clean_sig = clean_sig[:clean_sig.index(terminator)]

    return clean_sig.strip()


def _needs_continuation(text: str) -> bool:
    """Check if a line needs continuation (unbalanced delimiters, trailing comma, etc.)."""
    depth = 0
    for ch in text:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
    if depth > 0:
        return True
    stripped = text.rstrip()
    return stripped.endswith(",") or stripped.endswith("→") or stripped.endswith("->")


def extract_repo(repo_dir: str | Path, lake_root: str | Path | None = None) -> list[dict]:
    """Extract all declarations from a Lean repository.

    Args:
        repo_dir: Root directory of the repository.
        lake_root: Root for .lean files (defaults to repo_dir).

    Returns:
        List of declaration dicts.
    """
    repo_dir = Path(repo_dir)
    lake_root = Path(lake_root) if lake_root else repo_dir

    all_decls = []
    lean_files = list(lake_root.rglob("*.lean"))

    # Filter out .lake directory
    lean_files = [f for f in lean_files if ".lake" not in f.parts]

    logger.info(f"Extracting from {len(lean_files)} .lean files in {repo_dir}")

    for fpath in lean_files:
        # Build module name from relative path
        try:
            rel = fpath.relative_to(lake_root)
        except ValueError:
            rel = fpath.relative_to(repo_dir)

        module = str(rel).replace(os.sep, ".").removesuffix(".lean")

        try:
            decls = extract_file(fpath, module)
            all_decls.extend(decls)
        except Exception as e:
            logger.warning(f"Error extracting {fpath}: {e}")

    logger.info(f"Extracted {len(all_decls)} declarations from {len(lean_files)} files")
    return all_decls


def extract_imports(path: str | Path) -> list[str]:
    """Extract import statements from a Lean file."""
    imports = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                stripped = line.strip()
                if stripped.startswith("import "):
                    imports.append(stripped.split()[1])
                elif stripped and not stripped.startswith("--") and not stripped.startswith("/-"):
                    # Stop at first non-import, non-comment line
                    if not stripped.startswith("import") and not stripped.startswith("open"):
                        break
    except OSError:
        pass
    return imports

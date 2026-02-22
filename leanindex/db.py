"""SQLite + FTS5 database for the Lean declaration index."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS repos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual',  -- manual, reservoir, github_search
    branch TEXT DEFAULT 'main',
    last_commit_sha TEXT,
    last_indexed_at TEXT,
    toolchain TEXT,
    description TEXT DEFAULT '',
    stars INTEGER DEFAULT 0,
    is_mathlib INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS declarations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id INTEGER NOT NULL REFERENCES repos(id),
    module TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL,
    kind TEXT NOT NULL,  -- def, theorem, lemma, structure, class, instance, abbrev, inductive, opaque
    type_sig TEXT DEFAULT '',
    type_html TEXT DEFAULT '',
    docstring TEXT DEFAULT '',
    file_path TEXT DEFAULT '',
    line INTEGER DEFAULT 0,
    is_noncomputable INTEGER DEFAULT 0,
    axioms TEXT DEFAULT '',  -- JSON list
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    UNIQUE(repo_id, name)
);

CREATE TABLE IF NOT EXISTS repo_modules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id INTEGER NOT NULL REFERENCES repos(id),
    module TEXT NOT NULL,
    file_path TEXT DEFAULT '',
    imported_by TEXT DEFAULT '',  -- JSON list
    UNIQUE(repo_id, module)
);

CREATE TABLE IF NOT EXISTS topics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS topic_matches (
    declaration_id INTEGER NOT NULL REFERENCES declarations(id),
    topic_id INTEGER NOT NULL REFERENCES topics(id),
    match_reason TEXT DEFAULT '',
    confidence REAL DEFAULT 0.0,
    PRIMARY KEY (declaration_id, topic_id)
);

CREATE TABLE IF NOT EXISTS update_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at TEXT NOT NULL,
    repos_checked INTEGER DEFAULT 0,
    repos_updated INTEGER DEFAULT 0,
    new_declarations INTEGER DEFAULT 0,
    removed_declarations INTEGER DEFAULT 0,
    summary TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_declarations_repo ON declarations(repo_id);
CREATE INDEX IF NOT EXISTS idx_declarations_kind ON declarations(kind);
CREATE INDEX IF NOT EXISTS idx_declarations_module ON declarations(module);
CREATE INDEX IF NOT EXISTS idx_declarations_name ON declarations(name);
CREATE INDEX IF NOT EXISTS idx_topic_matches_topic ON topic_matches(topic_id);
CREATE INDEX IF NOT EXISTS idx_topic_matches_decl ON topic_matches(declaration_id);
CREATE INDEX IF NOT EXISTS idx_repo_modules_repo ON repo_modules(repo_id);
"""

FTS_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS declarations_fts USING fts5(
    name, type_sig, docstring, module,
    content='declarations',
    content_rowid='id',
    tokenize='unicode61'
);

-- Triggers to keep FTS in sync
CREATE TRIGGER IF NOT EXISTS declarations_ai AFTER INSERT ON declarations BEGIN
    INSERT INTO declarations_fts(rowid, name, type_sig, docstring, module)
    VALUES (new.id, new.name, new.type_sig, new.docstring, new.module);
END;

CREATE TRIGGER IF NOT EXISTS declarations_ad AFTER DELETE ON declarations BEGIN
    INSERT INTO declarations_fts(declarations_fts, rowid, name, type_sig, docstring, module)
    VALUES ('delete', old.id, old.name, old.type_sig, old.docstring, old.module);
END;

CREATE TRIGGER IF NOT EXISTS declarations_au AFTER UPDATE ON declarations BEGIN
    INSERT INTO declarations_fts(declarations_fts, rowid, name, type_sig, docstring, module)
    VALUES ('delete', old.id, old.name, old.type_sig, old.docstring, old.module);
    INSERT INTO declarations_fts(rowid, name, type_sig, docstring, module)
    VALUES (new.id, new.name, new.type_sig, new.docstring, new.module);
END;
"""


class IndexDB:
    """Manages the SQLite database for the declaration index."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    def close(self):
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @contextmanager
    def transaction(self):
        """Context manager for a database transaction."""
        conn = self.conn
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def init_schema(self):
        """Create all tables, indexes, and FTS."""
        with self.transaction() as conn:
            conn.executescript(SCHEMA_SQL)
            conn.executescript(FTS_SQL)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def get_schema_version(self) -> int:
        row = self.conn.execute("PRAGMA user_version").fetchone()
        return row[0] if row else 0

    # --- Repos ---

    def upsert_repo(self, url: str, name: str, source: str = "manual",
                    branch: str = "main", description: str = "",
                    stars: int = 0, toolchain: str = "",
                    is_mathlib: bool = False) -> int:
        """Insert or update a repo. Returns repo_id."""
        with self.transaction() as conn:
            row = conn.execute("SELECT id FROM repos WHERE url = ?", (url,)).fetchone()
            if row:
                conn.execute("""
                    UPDATE repos SET name=?, source=?, branch=?, description=?,
                    stars=?, toolchain=?, is_mathlib=? WHERE id=?
                """, (name, source, branch, description, stars, toolchain,
                      int(is_mathlib), row["id"]))
                return row["id"]
            else:
                cur = conn.execute("""
                    INSERT INTO repos (url, name, source, branch, description,
                    stars, toolchain, is_mathlib)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (url, name, source, branch, description, stars, toolchain,
                      int(is_mathlib)))
                return cur.lastrowid

    def update_repo_indexed(self, repo_id: int, commit_sha: str):
        """Mark a repo as indexed at a given commit."""
        now = datetime.now(timezone.utc).isoformat()
        with self.transaction() as conn:
            conn.execute(
                "UPDATE repos SET last_commit_sha=?, last_indexed_at=? WHERE id=?",
                (commit_sha, now, repo_id)
            )

    def get_repo(self, url: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM repos WHERE url = ?", (url,)).fetchone()
        return dict(row) if row else None

    def get_repo_by_id(self, repo_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM repos WHERE id = ?", (repo_id,)).fetchone()
        return dict(row) if row else None

    def list_repos(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM repos ORDER BY name").fetchall()
        return [dict(r) for r in rows]

    # --- Declarations ---

    def bulk_upsert_declarations(self, repo_id: int, decls: list[dict]) -> tuple[int, int]:
        """Bulk insert/update declarations. Returns (inserted, updated)."""
        now = datetime.now(timezone.utc).isoformat()
        inserted = 0
        updated = 0

        with self.transaction() as conn:
            for d in decls:
                row = conn.execute(
                    "SELECT id FROM declarations WHERE repo_id = ? AND name = ?",
                    (repo_id, d["name"])
                ).fetchone()

                if row:
                    conn.execute("""
                        UPDATE declarations SET module=?, kind=?, type_sig=?,
                        type_html=?, docstring=?, file_path=?, line=?,
                        is_noncomputable=?, axioms=?, last_seen_at=?
                        WHERE id=?
                    """, (
                        d.get("module", ""), d.get("kind", "def"),
                        d.get("type_sig", ""), d.get("type_html", ""),
                        d.get("docstring", ""), d.get("file_path", ""),
                        d.get("line", 0), int(d.get("is_noncomputable", False)),
                        d.get("axioms", ""), now, row["id"]
                    ))
                    updated += 1
                else:
                    conn.execute("""
                        INSERT INTO declarations (repo_id, module, name, kind,
                        type_sig, type_html, docstring, file_path, line,
                        is_noncomputable, axioms, first_seen_at, last_seen_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        repo_id, d.get("module", ""), d["name"],
                        d.get("kind", "def"), d.get("type_sig", ""),
                        d.get("type_html", ""), d.get("docstring", ""),
                        d.get("file_path", ""), d.get("line", 0),
                        int(d.get("is_noncomputable", False)),
                        d.get("axioms", ""), now, now
                    ))
                    inserted += 1

        return inserted, updated

    def mark_stale_declarations(self, repo_id: int, current_names: set[str]) -> int:
        """Remove declarations no longer present in the repo. Returns count removed."""
        rows = self.conn.execute(
            "SELECT id, name FROM declarations WHERE repo_id = ?", (repo_id,)
        ).fetchall()

        stale_ids = [r["id"] for r in rows if r["name"] not in current_names]
        if stale_ids:
            with self.transaction() as conn:
                placeholders = ",".join("?" * len(stale_ids))
                conn.execute(
                    f"DELETE FROM topic_matches WHERE declaration_id IN ({placeholders})",
                    stale_ids
                )
                conn.execute(
                    f"DELETE FROM declarations WHERE id IN ({placeholders})",
                    stale_ids
                )
        return len(stale_ids)

    def get_declaration_count(self, repo_id: int | None = None) -> int:
        if repo_id is not None:
            row = self.conn.execute(
                "SELECT COUNT(*) FROM declarations WHERE repo_id = ?", (repo_id,)
            ).fetchone()
        else:
            row = self.conn.execute("SELECT COUNT(*) FROM declarations").fetchone()
        return row[0]

    # --- Modules ---

    def bulk_upsert_modules(self, repo_id: int, modules: list[dict]):
        """Bulk insert/update modules."""
        with self.transaction() as conn:
            for m in modules:
                conn.execute("""
                    INSERT INTO repo_modules (repo_id, module, file_path, imported_by)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(repo_id, module) DO UPDATE SET
                    file_path=excluded.file_path, imported_by=excluded.imported_by
                """, (repo_id, m["module"], m.get("file_path", ""),
                      m.get("imported_by", "")))

    # --- Topics ---

    def ensure_topic(self, name: str) -> int:
        """Get or create a topic. Returns topic_id."""
        row = self.conn.execute("SELECT id FROM topics WHERE name = ?", (name,)).fetchone()
        if row:
            return row["id"]
        with self.transaction() as conn:
            cur = conn.execute("INSERT INTO topics (name) VALUES (?)", (name,))
            return cur.lastrowid

    def clear_topic_matches(self, topic_id: int | None = None):
        """Clear topic matches, optionally for a specific topic."""
        with self.transaction() as conn:
            if topic_id is not None:
                conn.execute("DELETE FROM topic_matches WHERE topic_id = ?", (topic_id,))
            else:
                conn.execute("DELETE FROM topic_matches")

    def bulk_insert_topic_matches(self, matches: list[dict]):
        """Insert topic matches in bulk."""
        with self.transaction() as conn:
            conn.executemany("""
                INSERT OR IGNORE INTO topic_matches
                (declaration_id, topic_id, match_reason, confidence)
                VALUES (:declaration_id, :topic_id, :match_reason, :confidence)
            """, matches)

    def list_topics(self) -> list[dict]:
        rows = self.conn.execute("""
            SELECT t.id, t.name, COUNT(tm.declaration_id) as match_count
            FROM topics t
            LEFT JOIN topic_matches tm ON t.id = tm.topic_id
            GROUP BY t.id ORDER BY t.name
        """).fetchall()
        return [dict(r) for r in rows]

    # --- Update Log ---

    def log_update(self, repos_checked: int, repos_updated: int,
                   new_decls: int, removed_decls: int, summary: str):
        now = datetime.now(timezone.utc).isoformat()
        with self.transaction() as conn:
            conn.execute("""
                INSERT INTO update_log (run_at, repos_checked, repos_updated,
                new_declarations, removed_declarations, summary)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (now, repos_checked, repos_updated, new_decls, removed_decls, summary))

    # --- Search helpers ---

    def fts_search(self, query: str, limit: int = 50, offset: int = 0,
                   kind: str | None = None, topic: str | None = None,
                   repo: str | None = None, since: str | None = None,
                   type_mention: str | None = None) -> list[dict]:
        """Full-text search with optional structured filters."""
        params: list[Any] = []
        joins = []
        wheres = []

        # Base FTS query
        base = """
            SELECT d.*, r.name as repo_name, r.url as repo_url,
                   bm25(declarations_fts) as rank
            FROM declarations_fts fts
            JOIN declarations d ON d.id = fts.rowid
            JOIN repos r ON r.id = d.repo_id
        """

        if query:
            wheres.append("declarations_fts MATCH ?")
            # Quote each token to prevent FTS5 operator interpretation
            # (e.g., "C*-algebra" -> '"C*-algebra"', "foo bar" -> '"foo" "bar"')
            safe_query = " ".join(f'"{token}"' for token in query.split())
            params.append(safe_query)

        if kind:
            wheres.append("d.kind = ?")
            params.append(kind)

        if topic:
            joins.append("JOIN topic_matches tm ON tm.declaration_id = d.id")
            joins.append("JOIN topics t ON t.id = tm.topic_id")
            wheres.append("t.name = ?")
            params.append(topic)

        if repo:
            wheres.append("(r.name LIKE ? OR r.url LIKE ?)")
            params.extend([f"%{repo}%", f"%{repo}%"])

        if since:
            wheres.append("d.first_seen_at >= ?")
            params.append(since)

        if type_mention:
            wheres.append("d.type_sig LIKE ?")
            params.append(f"%{type_mention}%")

        sql = base + " ".join(joins)
        if wheres:
            sql += " WHERE " + " AND ".join(wheres)
        sql += " ORDER BY rank LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = self.conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def structured_search(self, limit: int = 50, offset: int = 0,
                          kind: str | None = None, topic: str | None = None,
                          repo: str | None = None, since: str | None = None,
                          type_mention: str | None = None,
                          name_pattern: str | None = None) -> list[dict]:
        """Structured search without FTS (for non-text queries)."""
        params: list[Any] = []
        joins = []
        wheres = []

        base = """
            SELECT d.*, r.name as repo_name, r.url as repo_url
            FROM declarations d
            JOIN repos r ON r.id = d.repo_id
        """

        if kind:
            wheres.append("d.kind = ?")
            params.append(kind)

        if topic:
            joins.append("JOIN topic_matches tm ON tm.declaration_id = d.id")
            joins.append("JOIN topics t ON t.id = tm.topic_id")
            wheres.append("t.name = ?")
            params.append(topic)

        if repo:
            wheres.append("(r.name LIKE ? OR r.url LIKE ?)")
            params.extend([f"%{repo}%", f"%{repo}%"])

        if since:
            wheres.append("d.first_seen_at >= ?")
            params.append(since)

        if type_mention:
            wheres.append("d.type_sig LIKE ?")
            params.append(f"%{type_mention}%")

        if name_pattern:
            wheres.append("d.name LIKE ?")
            params.append(f"%{name_pattern}%")

        sql = base + " ".join(joins)
        if wheres:
            sql += " WHERE " + " AND ".join(wheres)
        sql += " ORDER BY d.name LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = self.conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    # --- Stats ---

    def stats(self) -> dict:
        """Get index statistics."""
        conn = self.conn
        total_decls = conn.execute("SELECT COUNT(*) FROM declarations").fetchone()[0]
        total_repos = conn.execute("SELECT COUNT(*) FROM repos").fetchone()[0]

        by_kind = {}
        for row in conn.execute(
            "SELECT kind, COUNT(*) as cnt FROM declarations GROUP BY kind ORDER BY cnt DESC"
        ).fetchall():
            by_kind[row["kind"]] = row["cnt"]

        by_repo = {}
        for row in conn.execute("""
            SELECT r.name, COUNT(d.id) as cnt
            FROM repos r LEFT JOIN declarations d ON d.repo_id = r.id
            GROUP BY r.id ORDER BY cnt DESC
        """).fetchall():
            by_repo[row["name"]] = row["cnt"]

        by_topic = {}
        for row in conn.execute("""
            SELECT t.name, COUNT(tm.declaration_id) as cnt
            FROM topics t LEFT JOIN topic_matches tm ON t.id = tm.topic_id
            GROUP BY t.id ORDER BY cnt DESC
        """).fetchall():
            by_topic[row["name"]] = row["cnt"]

        last_update = conn.execute(
            "SELECT * FROM update_log ORDER BY run_at DESC LIMIT 1"
        ).fetchone()

        return {
            "total_declarations": total_decls,
            "total_repos": total_repos,
            "by_kind": by_kind,
            "by_repo": by_repo,
            "by_topic": by_topic,
            "last_update": dict(last_update) if last_update else None,
        }

    def changelog(self, since: str) -> dict:
        """Get new and removed declarations since a date."""
        new = self.conn.execute("""
            SELECT d.name, d.kind, d.module, r.name as repo_name
            FROM declarations d JOIN repos r ON r.id = d.repo_id
            WHERE d.first_seen_at >= ? ORDER BY d.first_seen_at DESC
        """, (since,)).fetchall()

        updates = self.conn.execute(
            "SELECT * FROM update_log WHERE run_at >= ? ORDER BY run_at",
            (since,)
        ).fetchall()

        return {
            "new_declarations": [dict(r) for r in new],
            "updates": [dict(r) for r in updates],
        }

# Copyright 2026 Michael R. Douglas. MIT License.
"""Validation tests for IndexDB search and stats functions.

Runs against a real or freshly-created test database. Use pytest:

    pytest tests/test_db.py -v

If LEAN_INDEX_TEST_DB is set, uses that database (read-only tests only).
Otherwise, creates a temporary in-memory database with fixture data.
"""

import os
import sqlite3
import tempfile
from pathlib import Path

import pytest

from leanindex.db import IndexDB


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _seed_db(db: IndexDB):
    """Populate a fresh DB with enough data to exercise all search paths."""
    db.init_schema()

    # Repos
    repo1_id = db.upsert_repo(
        url="https://github.com/leanprover-community/mathlib4",
        name="mathlib4", source="manual", stars=1000, is_mathlib=True,
    )
    repo2_id = db.upsert_repo(
        url="https://github.com/example/lie-stuff",
        name="lie-stuff", source="manual", stars=5,
    )

    # Declarations
    decls_mathlib = [
        {
            "name": "LieAlgebra.IsKilling",
            "kind": "class",
            "module": "Mathlib.Algebra.Lie.Killing",
            "type_sig": "class LieAlgebra.IsKilling (R : Type) (L : Type)",
            "docstring": "We say a Lie algebra is Killing if its Killing form is non-singular.",
            "source_url": "",
        },
        {
            "name": "killingForm",
            "kind": "def",
            "module": "Mathlib.Algebra.Lie.TraceForm",
            "type_sig": "def killingForm (R : Type) (L : Type) : BilinForm R L",
            "docstring": "The Killing form of a Lie algebra.",
            "source_url": "",
        },
        {
            "name": "LieAlgebra.IsSolvable",
            "kind": "class",
            "module": "Mathlib.Algebra.Lie.Solvable",
            "type_sig": "class LieAlgebra.IsSolvable (R : Type) (L : Type)",
            "docstring": "",
            "source_url": "",
        },
        {
            "name": "RootPairing.IsRootSystem",
            "kind": "class",
            "module": "Mathlib.LinearAlgebra.RootSystem.Defs",
            "type_sig": "class RootPairing.IsRootSystem",
            "docstring": "A root system is a root pairing.",
            "source_url": "",
        },
        {
            "name": "LieAlgebra.solvable_iff_equiv_solvable",
            "kind": "theorem",
            "module": "Mathlib.Algebra.Lie.Solvable",
            "type_sig": "theorem solvable_iff_equiv_solvable",
            "docstring": "Solvability is preserved under equivalence.",
            "source_url": "",
        },
    ]
    decls_lie = [
        {
            "name": "LieAlgebra.solvable_of_commutator_solvable",
            "kind": "theorem",
            "module": "Lie.GeneralResults",
            "type_sig": "theorem solvable_of_commutator_solvable",
            "docstring": "If the commutator is solvable, so is the Lie algebra.",
            "source_url": "",
        },
    ]
    db.bulk_upsert_declarations(repo1_id, decls_mathlib)
    db.bulk_upsert_declarations(repo2_id, decls_lie)
    db.update_repo_indexed(repo1_id, "abc123")
    db.update_repo_indexed(repo2_id, "def456")

    # Topics
    tid = db.ensure_topic("lie-algebras")
    tid2 = db.ensure_topic("root-systems")
    # Match all declarations to lie-algebras, root system decl also to root-systems
    all_decls = db.conn.execute("SELECT id, name FROM declarations").fetchall()
    matches = []
    for d in all_decls:
        matches.append({"declaration_id": d["id"], "topic_id": tid,
                         "match_reason": "test", "confidence": 0.8})
        if "Root" in d["name"]:
            matches.append({"declaration_id": d["id"], "topic_id": tid2,
                             "match_reason": "test", "confidence": 0.9})
    db.bulk_insert_topic_matches(matches)

    # Update log
    db.log_update(2, 2, 6, 0, "Test seed")


@pytest.fixture
def seeded_db(tmp_path):
    """Return an IndexDB seeded with test data."""
    db_path = tmp_path / "test_index.db"
    db = IndexDB(db_path)
    _seed_db(db)
    yield db
    db.close()


@pytest.fixture
def real_db():
    """Return a read-only handle to a real DB if LEAN_INDEX_TEST_DB is set."""
    db_path = os.environ.get("LEAN_INDEX_TEST_DB")
    if not db_path:
        pytest.skip("LEAN_INDEX_TEST_DB not set")
    db = IndexDB(db_path)
    yield db
    db.close()


# ---------------------------------------------------------------------------
# Tests against seeded fixture DB
# ---------------------------------------------------------------------------

class TestFTSSearch:
    """Full-text search via fts_search()."""

    def test_basic_search(self, seeded_db):
        results = seeded_db.fts_search("Killing")
        assert len(results) >= 1
        names = [r["name"] for r in results]
        assert "LieAlgebra.IsKilling" in names

    def test_multi_word_search(self, seeded_db):
        results = seeded_db.fts_search("Killing form")
        assert len(results) >= 1

    def test_kind_filter(self, seeded_db):
        results = seeded_db.fts_search("solvable", kind="theorem")
        for r in results:
            assert r["kind"] == "theorem"

    def test_topic_filter(self, seeded_db):
        results = seeded_db.fts_search("root", topic="root-systems")
        assert len(results) >= 1

    def test_repo_filter(self, seeded_db):
        results = seeded_db.fts_search("solvable", repo="lie-stuff")
        names = [r["name"] for r in results]
        assert "LieAlgebra.solvable_of_commutator_solvable" in names

    def test_type_mention_filter(self, seeded_db):
        results = seeded_db.fts_search("Killing", type_mention="BilinForm")
        assert len(results) >= 1
        assert any("BilinForm" in r["type_sig"] for r in results)

    def test_limit_and_offset(self, seeded_db):
        all_results = seeded_db.fts_search("Lie", limit=100)
        limited = seeded_db.fts_search("Lie", limit=2)
        assert len(limited) <= 2
        if len(all_results) > 2:
            offset_results = seeded_db.fts_search("Lie", limit=2, offset=2)
            assert len(offset_results) >= 1

    def test_no_results(self, seeded_db):
        results = seeded_db.fts_search("nonexistent_xyzzy_12345")
        assert results == []

    def test_result_has_expected_fields(self, seeded_db):
        results = seeded_db.fts_search("Killing")
        assert len(results) >= 1
        r = results[0]
        assert "name" in r
        assert "kind" in r
        assert "module" in r
        assert "repo_name" in r
        assert "bm25_rank" in r
        assert "topic_confidence" in r

    def test_bm25_rank_is_numeric(self, seeded_db):
        results = seeded_db.fts_search("Killing")
        for r in results:
            assert isinstance(r["bm25_rank"], (int, float))


class TestStructuredSearch:
    """Structured (non-FTS) search via structured_search()."""

    def test_kind_filter(self, seeded_db):
        results = seeded_db.structured_search(kind="class")
        assert len(results) >= 1
        for r in results:
            assert r["kind"] == "class"

    def test_topic_filter(self, seeded_db):
        results = seeded_db.structured_search(topic="root-systems")
        assert len(results) >= 1

    def test_repo_filter(self, seeded_db):
        results = seeded_db.structured_search(repo="mathlib4")
        assert len(results) >= 1
        for r in results:
            assert r["repo_name"] == "mathlib4"

    def test_name_pattern(self, seeded_db):
        results = seeded_db.structured_search(name_pattern="Killing")
        assert len(results) >= 1

    def test_type_mention(self, seeded_db):
        results = seeded_db.structured_search(type_mention="BilinForm")
        assert len(results) >= 1

    def test_no_results(self, seeded_db):
        results = seeded_db.structured_search(kind="nonexistent_kind")
        assert results == []

    def test_combined_filters(self, seeded_db):
        results = seeded_db.structured_search(kind="theorem", topic="lie-algebras")
        for r in results:
            assert r["kind"] == "theorem"


class TestStats:
    """stats() method."""

    def test_stats_returns_dict(self, seeded_db):
        s = seeded_db.stats()
        assert isinstance(s, dict)
        assert "total_declarations" in s
        assert "total_repos" in s
        assert "by_kind" in s
        assert "by_repo" in s
        assert "by_topic" in s

    def test_stats_counts(self, seeded_db):
        s = seeded_db.stats()
        assert s["total_declarations"] == 6
        assert s["total_repos"] == 2

    def test_stats_by_kind(self, seeded_db):
        s = seeded_db.stats()
        assert "theorem" in s["by_kind"]
        assert "class" in s["by_kind"]
        assert "def" in s["by_kind"]

    def test_stats_by_repo(self, seeded_db):
        s = seeded_db.stats()
        assert "mathlib4" in s["by_repo"]
        assert "lie-stuff" in s["by_repo"]

    def test_stats_by_topic(self, seeded_db):
        s = seeded_db.stats()
        assert "lie-algebras" in s["by_topic"]
        assert s["by_topic"]["lie-algebras"] >= 1

    def test_stats_last_update(self, seeded_db):
        s = seeded_db.stats()
        assert s["last_update"] is not None
        assert "summary" in s["last_update"]


class TestChangelog:
    """changelog() method."""

    def test_changelog_returns_all_recent(self, seeded_db):
        c = seeded_db.changelog(since="2000-01-01")
        assert len(c["new_declarations"]) == 6
        assert len(c["updates"]) >= 1

    def test_changelog_future_date_empty(self, seeded_db):
        c = seeded_db.changelog(since="2099-01-01")
        assert len(c["new_declarations"]) == 0


# ---------------------------------------------------------------------------
# Tests against real DB (opt-in via LEAN_INDEX_TEST_DB env var)
# ---------------------------------------------------------------------------

class TestRealDB:
    """Smoke tests against a real populated database."""

    def test_fts_search_killing(self, real_db):
        results = real_db.fts_search("Killing form")
        assert len(results) >= 1
        assert any("Killing" in r["name"] or "killing" in r["name"]
                    for r in results)

    def test_fts_search_root_system(self, real_db):
        results = real_db.fts_search("root system")
        assert len(results) >= 1

    def test_fts_search_with_kind(self, real_db):
        results = real_db.fts_search("solvable", kind="theorem")
        assert len(results) >= 1
        for r in results:
            assert r["kind"] == "theorem"

    def test_fts_search_with_topic(self, real_db):
        results = real_db.fts_search("Cartan", topic="lie-algebras")
        assert len(results) >= 1

    def test_structured_search_kind(self, real_db):
        results = real_db.structured_search(kind="class", limit=5)
        assert len(results) >= 1

    def test_structured_search_name(self, real_db):
        results = real_db.structured_search(name_pattern="IsKilling")
        assert len(results) >= 1

    def test_stats_smoke(self, real_db):
        s = real_db.stats()
        assert s["total_declarations"] > 100
        assert s["total_repos"] >= 1

    def test_no_results_for_gibberish(self, real_db):
        results = real_db.fts_search("xyzzy_nonexistent_term_12345")
        assert results == []

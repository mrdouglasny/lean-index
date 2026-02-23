# Copyright 2026 Michael R. Douglas. MIT License.
"""Topic matching engine for declarations."""

from __future__ import annotations

import logging
import re

from .config import TopicConfig
from .db import IndexDB

logger = logging.getLogger(__name__)


def match_declaration(decl: dict, topic: TopicConfig) -> tuple[bool, str, float]:
    """Check if a declaration matches a topic.

    Returns (matched, reason, confidence).
    Strategies:
        - module_prefix: confidence 1.0
        - type_mention: confidence 0.8
        - name_pattern: confidence 0.6
    """
    module = decl.get("module", "")
    name = decl.get("name", "")
    type_sig = decl.get("type_sig", "")

    # 1. Module prefix match (highest confidence)
    for prefix in topic.matchers.module_prefixes:
        if module.startswith(prefix):
            return True, f"module:{prefix}", 1.0

    # 2. Type signature mention
    for mention in topic.matchers.type_mentions:
        if mention in type_sig or mention in name:
            return True, f"type:{mention}", 0.8

    # 3. Name pattern
    for pattern in topic.matchers.name_patterns:
        try:
            if re.search(pattern, name):
                return True, f"name:{pattern}", 0.6
        except re.error:
            logger.warning(f"Invalid regex pattern: {pattern}")

    return False, "", 0.0


def match_all_topics(db: IndexDB, topics: list[TopicConfig],
                     repo_id: int | None = None):
    """Match all declarations against all topics and store results.

    Args:
        db: Database instance.
        topics: List of topic configs.
        repo_id: If set, only match declarations from this repo.
    """
    # Ensure topics exist in DB
    topic_ids = {}
    for topic in topics:
        topic_ids[topic.name] = db.ensure_topic(topic.name)

    # Get declarations to match
    if repo_id is not None:
        rows = db.conn.execute(
            "SELECT * FROM declarations WHERE repo_id = ?", (repo_id,)
        ).fetchall()
    else:
        rows = db.conn.execute("SELECT * FROM declarations").fetchall()

    decls = [dict(r) for r in rows]
    logger.info(f"Matching {len(decls)} declarations against {len(topics)} topics")

    # Clear existing matches for the relevant scope
    if repo_id is not None:
        # Clear matches for declarations in this repo
        db.conn.execute("""
            DELETE FROM topic_matches WHERE declaration_id IN
            (SELECT id FROM declarations WHERE repo_id = ?)
        """, (repo_id,))
        db.conn.commit()
    else:
        db.clear_topic_matches()

    # Match each declaration against each topic
    matches = []
    match_counts = {t.name: 0 for t in topics}

    for decl in decls:
        for topic in topics:
            matched, reason, confidence = match_declaration(decl, topic)
            if matched:
                matches.append({
                    "declaration_id": decl["id"],
                    "topic_id": topic_ids[topic.name],
                    "match_reason": reason,
                    "confidence": confidence,
                })
                match_counts[topic.name] += 1

    # Bulk insert
    if matches:
        db.bulk_insert_topic_matches(matches)

    for name, count in match_counts.items():
        logger.info(f"  {name}: {count} matches")

    return match_counts

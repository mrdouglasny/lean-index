"""Report generation: stats, changelog, topic coverage."""

from __future__ import annotations

import json

from .db import IndexDB


def format_stats(db: IndexDB) -> str:
    """Generate a stats report."""
    s = db.stats()
    lines = []

    lines.append(f"# Lean Index Statistics\n")

    # Topic-matched summary
    if s["by_topic"]:
        total_matched = sum(s["by_topic"].values())
        topic_repos = db.get_repos_with_topic_matches()
        lines.append(f"**{total_matched:,} topic-matched declarations** across "
                      f"**{len(topic_repos)} repositories**")
        lines.append(f"({s['total_declarations']:,} total declarations scanned "
                      f"from {s['total_repos']} repos)")
        lines.append("")

        lines.append("## By Topic\n")
        lines.append(f"| {'Topic':<30} | {'Matches':>10} |")
        lines.append(f"|{'-'*32}|{'-'*12}|")
        for topic, count in sorted(s["by_topic"].items(), key=lambda x: -x[1]):
            lines.append(f"| {topic:<30} | {count:>10,} |")
        lines.append("")
    else:
        lines.append(f"Total declarations: {s['total_declarations']:,}")
        lines.append(f"Total repos: {s['total_repos']}")
        lines.append("")

    if s["by_kind"]:
        lines.append("## By Kind (topic-matched only)\n")
        # Show kind breakdown for topic-matched declarations
        kind_rows = db.conn.execute("""
            SELECT d.kind, COUNT(DISTINCT d.id) as cnt
            FROM declarations d
            JOIN topic_matches tm ON tm.declaration_id = d.id
            GROUP BY d.kind ORDER BY cnt DESC
        """).fetchall()
        if kind_rows:
            lines.append(f"| {'Kind':<15} | {'Count':>10} |")
            lines.append(f"|{'-'*17}|{'-'*12}|")
            for row in kind_rows:
                lines.append(f"| {row['kind']:<15} | {row['cnt']:>10,} |")
        else:
            # Fallback to all declarations if no topic matches
            lines.append(f"| {'Kind':<15} | {'Count':>10} |")
            lines.append(f"|{'-'*17}|{'-'*12}|")
            for kind, count in sorted(s["by_kind"].items(), key=lambda x: -x[1]):
                lines.append(f"| {kind:<15} | {count:>10,} |")
        lines.append("")

    if s["by_topic"]:
        # Show repos with topic matches
        topic_repos = db.get_repos_with_topic_matches()
        if topic_repos:
            lines.append("## Top Repositories (by topic matches)\n")
            lines.append(f"| {'Repository':<35} | {'Matched':>10} | {'Total':>10} |")
            lines.append(f"|{'-'*37}|{'-'*12}|{'-'*12}|")
            for r in topic_repos[:30]:  # Top 30
                name = r["name"][:35]
                lines.append(f"| {name:<35} | {r['matched_decls']:>10,} | {r['total_decls']:>10,} |")
            if len(topic_repos) > 30:
                lines.append(f"| ... and {len(topic_repos) - 30} more |  |  |")
            lines.append("")

    if s["last_update"]:
        u = s["last_update"]
        lines.append(f"## Last Update\n")
        lines.append(f"- **When**: {u['run_at']}")
        lines.append(f"- **Repos checked**: {u['repos_checked']}")
        lines.append(f"- **Repos updated**: {u['repos_updated']}")
        lines.append(f"- **New declarations**: {u['new_declarations']}")
        lines.append(f"- **Removed declarations**: {u['removed_declarations']}")
        if u.get("summary"):
            lines.append(f"- **Summary**: {u['summary']}")

    return "\n".join(lines)


def format_changelog(db: IndexDB, since: str) -> str:
    """Generate a changelog since a given date."""
    cl = db.changelog(since)
    lines = []

    lines.append(f"# Changelog (since {since})\n")

    new_decls = cl["new_declarations"]
    if new_decls:
        lines.append(f"## New Declarations ({len(new_decls)})\n")

        # Group by repo
        by_repo: dict[str, list] = {}
        for d in new_decls:
            repo = d.get("repo_name", "unknown")
            by_repo.setdefault(repo, []).append(d)

        for repo, decls in sorted(by_repo.items()):
            lines.append(f"### {repo} (+{len(decls)})\n")
            for d in decls[:50]:  # Limit per repo
                lines.append(f"- `{d['name']}` ({d['kind']}) in {d['module']}")
            if len(decls) > 50:
                lines.append(f"- ... and {len(decls) - 50} more")
            lines.append("")
    else:
        lines.append("No new declarations.\n")

    updates = cl["updates"]
    if updates:
        lines.append(f"## Update History ({len(updates)} runs)\n")
        for u in updates:
            lines.append(f"- **{u['run_at']}**: {u.get('summary', 'no summary')}")

    return "\n".join(lines)


def format_repos(db: IndexDB) -> str:
    """List tracked repos."""
    repos = db.list_repos()
    if not repos:
        return "No repos tracked."

    lines = []
    lines.append(f"{'Name':<30} {'Declarations':>12} {'Source':<15} {'Last Indexed'}")
    lines.append("-" * 85)

    for r in repos:
        count = db.get_declaration_count(r["id"])
        name = r["name"][:30]
        source = r.get("source", "")[:15]
        indexed = (r.get("last_indexed_at") or "never")[:19]
        lines.append(f"{name:<30} {count:>12,} {source:<15} {indexed}")

    return "\n".join(lines)


def generate_indexed_repos_md(db: IndexDB) -> str:
    """Generate a REPOS.md listing repos with topic-matched declarations."""
    repos = db.get_repos_with_topic_matches()
    lines = []
    lines.append("# Indexed Repositories\n")
    lines.append("Repositories with declarations matching the configured topics.\n")
    lines.append(f"| Repository | Topic Matches | Total Decls | Source | Last Indexed |")
    lines.append(f"|-----------|:---:|:---:|--------|-------------|")

    total_matched = 0
    repo_count = 0
    for r in repos:
        matched = r["matched_decls"]
        total = r["total_decls"]
        if matched == 0:
            continue
        repo_count += 1
        total_matched += matched
        url = r.get("url", "")
        name = r["name"]
        source = r.get("source", "")
        indexed = (r.get("last_indexed_at") or "never")[:10]

        if url:
            name_col = f"[{name}]({url})"
        else:
            name_col = name
        lines.append(f"| {name_col} | {matched:,} | {total:,} | {source} | {indexed} |")

    lines.append(f"\n**{total_matched:,} topic-matched declarations across {repo_count} repositories**")

    # Summary of scan scope
    all_repos = db.list_repos()
    total_scanned = len(all_repos)
    total_all_decls = sum(db.get_declaration_count(r["id"]) for r in all_repos)
    lines.append(f"\n*Scanned {total_scanned} repositories ({total_all_decls:,} total declarations) from Mathlib, Lean Reservoir, GitHub search, and curated lists.*")

    return "\n".join(lines)

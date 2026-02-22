"""Report generation: stats, changelog, topic coverage."""

from __future__ import annotations

import json

from .db import IndexDB


def format_stats(db: IndexDB) -> str:
    """Generate a stats report."""
    s = db.stats()
    lines = []

    lines.append(f"# Lean Index Statistics\n")
    lines.append(f"Total declarations: {s['total_declarations']:,}")
    lines.append(f"Total repos: {s['total_repos']}")
    lines.append("")

    if s["by_kind"]:
        lines.append("## By Kind\n")
        lines.append(f"| {'Kind':<15} | {'Count':>10} |")
        lines.append(f"|{'-'*17}|{'-'*12}|")
        for kind, count in sorted(s["by_kind"].items(), key=lambda x: -x[1]):
            lines.append(f"| {kind:<15} | {count:>10,} |")
        lines.append("")

    if s["by_repo"]:
        lines.append("## By Repository\n")
        lines.append(f"| {'Repository':<35} | {'Count':>10} |")
        lines.append(f"|{'-'*37}|{'-'*12}|")
        for repo, count in sorted(s["by_repo"].items(), key=lambda x: -x[1]):
            if count > 0:
                name = repo[:35]
                lines.append(f"| {name:<35} | {count:>10,} |")
        lines.append("")

    if s["by_topic"]:
        lines.append("## By Topic\n")
        lines.append(f"| {'Topic':<30} | {'Matches':>10} |")
        lines.append(f"|{'-'*32}|{'-'*12}|")
        for topic, count in sorted(s["by_topic"].items(), key=lambda x: -x[1]):
            lines.append(f"| {topic:<30} | {count:>10,} |")
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
    """Generate a REPOS.md listing all repos that contributed declarations."""
    repos = db.list_repos()
    lines = []
    lines.append("# Indexed Repositories\n")
    lines.append("Repositories that contributed declarations to this index.\n")
    lines.append(f"| Repository | Declarations | Source | Last Indexed |")
    lines.append(f"|-----------|-------------|--------|-------------|")

    total = 0
    for r in sorted(repos, key=lambda x: -db.get_declaration_count(x["id"])):
        count = db.get_declaration_count(r["id"])
        if count == 0:
            continue
        total += count
        url = r.get("url", "")
        name = r["name"]
        source = r.get("source", "")
        indexed = (r.get("last_indexed_at") or "never")[:10]

        if url:
            name_col = f"[{name}]({url})"
        else:
            name_col = name
        lines.append(f"| {name_col} | {count:,} | {source} | {indexed} |")

    lines.append(f"\n**Total: {total:,} declarations from {sum(1 for r in repos if db.get_declaration_count(r['id']) > 0)} repositories**")
    return "\n".join(lines)

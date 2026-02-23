# Copyright 2026 Michael R. Douglas. MIT License.
"""CLI for lean-index: cross-repository Lean 4 declaration index."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import click

from . import __version__
from .config import load_config
from .db import IndexDB

logger = logging.getLogger("leanindex")


def setup_logging(verbose: bool):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(levelname)s: %(message)s",
    )


def get_db(data_dir: str | None, config_dir: str | None) -> tuple[IndexDB, "IndexConfig"]:
    """Load config and open DB."""
    from .config import load_config
    config = load_config(
        config_dir=Path(config_dir) if config_dir else None,
        data_dir=Path(data_dir) if data_dir else None,
    )
    db = IndexDB(config.db_path)
    return db, config


@click.group()
@click.option("--config", "config_dir", default=None, help="Config directory (with topics.yaml)")
@click.option("--data-dir", default=None, help="Data directory for DB and cache")
@click.option("-v", "--verbose", is_flag=True, help="Verbose output")
@click.version_option(version=__version__)
@click.pass_context
def main(ctx, config_dir, data_dir, verbose):
    """lean-index: Cross-repository Lean 4 declaration index."""
    setup_logging(verbose)
    ctx.ensure_object(dict)
    ctx.obj["config_dir"] = config_dir
    ctx.obj["data_dir"] = data_dir


@main.command()
@click.pass_context
def init(ctx):
    """Create database and download Mathlib cache."""
    db, config = get_db(ctx.obj["data_dir"], ctx.obj["config_dir"])
    config.data_dir.mkdir(parents=True, exist_ok=True)
    config.cache_dir.mkdir(parents=True, exist_ok=True)

    click.echo(f"Initializing database at {config.db_path}")
    db.init_schema()

    click.echo(f"Downloading Mathlib declaration cache...")
    from .extract.mathlib_cache import index_mathlib

    # Filter Mathlib to topic-relevant declarations if topics are configured
    topics_for_filter = config.topics if config.topics else None
    if topics_for_filter:
        click.echo(f"Filtering to {len(topics_for_filter)} configured topics")
    result = index_mathlib(db, config.cache_dir, topics=topics_for_filter)

    total = result.get('total_parsed', result['declarations'])
    click.echo(f"Parsed {total:,} Mathlib declarations, "
               f"indexed {result['declarations']:,} topic-relevant "
               f"({result['inserted']:,} new, {result['updated']:,} updated)")

    # Match topics on the indexed declarations
    if config.topics:
        click.echo(f"Matching {len(config.topics)} topics...")
        from .match import match_all_topics
        match_all_topics(db, config.topics)

    # Auto-generate REPOS.md
    from .report import generate_indexed_repos_md
    repos_md = generate_indexed_repos_md(db)
    repos_md_path = config.data_dir.parent / "REPOS.md"
    with open(repos_md_path, "w") as f:
        f.write(repos_md)
    click.echo(f"Generated {repos_md_path}")

    db.close()
    click.echo("Done.")


@main.command()
@click.option("--local-only", is_flag=True, help="Only re-index local repos (skip online)")
@click.pass_context
def update(ctx, local_only):
    """Update the index.

    In a topic repo (has topics.yaml): full cycle — Mathlib, discover, index, match.
    In a consumer project (no topics.yaml): re-index local repos from local-repos.yaml.
    Use --local-only in a topic repo to skip the full online cycle.
    """
    db, config = get_db(ctx.obj["data_dir"], ctx.obj["config_dir"])

    is_consumer = not config.topics
    do_local_only = local_only or is_consumer

    if do_local_only:
        # Consumer mode or --local-only: just re-index local repos
        if db.get_schema_version() == 0:
            click.echo("Database not initialized. Run 'lean-index init' or 'lean-index fetch-db' first.")
            sys.exit(1)

        if not config.local_repos:
            click.echo("No local repos configured. Use 'lean-index add <path-or-url>' first.")
            sys.exit(0)

        from .update import index_local, index_repo
        from .match import match_all_topics

        total_inserted = 0
        total_removed = 0
        for entry in config.local_repos:
            label = entry.name or entry.path or entry.url
            try:
                if entry.path:
                    result = index_local(db, path=entry.path, name=entry.name,
                                         description=entry.description)
                elif entry.url:
                    result = index_repo(db, url=entry.url, name=entry.name,
                                        branch=entry.branch, description=entry.description,
                                        source="local-repos")
                else:
                    continue

                if result.get("error"):
                    click.echo(f"  {label}: error - {result['error']}")
                elif result.get("skipped"):
                    click.echo(f"  {label}: up to date")
                else:
                    click.echo(f"  {label}: {result.get('declarations', 0):,} declarations "
                               f"({result.get('inserted', 0)} new, {result.get('removed', 0)} removed)")
                    total_inserted += result.get("inserted", 0)
                    total_removed += result.get("removed", 0)
            except Exception as e:
                click.echo(f"  {label}: error - {e}")

        # Re-match topics if we have them
        if config.topics:
            match_all_topics(db, config.topics)

        click.echo(f"\nLocal update complete: +{total_inserted} -{total_removed} declarations")
        db.close()
        return

    # Full update (topic repo mode)
    if db.get_schema_version() == 0:
        click.echo("Database not initialized. Run 'lean-index init' first.")
        sys.exit(1)

    from .update import run_update
    stats = run_update(db, config)

    click.echo(f"\nUpdate complete:")
    click.echo(f"  Repos checked: {stats['repos_checked']}")
    click.echo(f"  Repos updated: {stats['repos_updated']}")
    click.echo(f"  New declarations: {stats['new_declarations']}")
    click.echo(f"  Removed declarations: {stats['removed_declarations']}")
    if stats["errors"]:
        click.echo(f"  Errors: {len(stats['errors'])}")
        for e in stats["errors"][:5]:
            click.echo(f"    - {e}")

    # Auto-generate REPOS.md
    from .report import generate_indexed_repos_md
    repos_md = generate_indexed_repos_md(db)
    repos_md_path = config.data_dir.parent / "REPOS.md"
    with open(repos_md_path, "w") as f:
        f.write(repos_md)
    click.echo(f"Generated {repos_md_path}")

    db.close()


@main.command()
@click.pass_context
def discover(ctx):
    """Discover Lean repos from Reservoir and GitHub."""
    db, config = get_db(ctx.obj["data_dir"], ctx.obj["config_dir"])
    from .discover import discover_all

    repos = discover_all(config, config.cache_dir)
    click.echo(f"Discovered {len(repos)} repos:")

    for r in repos[:30]:
        stars = r.get("stars", 0)
        star_str = f" ({stars}*)" if stars else ""
        click.echo(f"  {r.get('name', '?'):<30} {r.get('source', ''):<15}{star_str}")
        if r.get("description"):
            click.echo(f"    {r['description'][:80]}")

    if len(repos) > 30:
        click.echo(f"  ... and {len(repos) - 30} more")

    db.close()


@main.command("index-mathlib")
@click.option("--force", is_flag=True, help="Force re-download even if cache is fresh")
@click.pass_context
def index_mathlib_cmd(ctx, force):
    """Re-download and reindex Mathlib declaration cache."""
    db, config = get_db(ctx.obj["data_dir"], ctx.obj["config_dir"])

    if db.get_schema_version() == 0:
        db.init_schema()

    from .extract.mathlib_cache import index_mathlib
    topics_for_filter = config.topics if config.topics else None
    result = index_mathlib(db, config.cache_dir, force=force, topics=topics_for_filter)

    total = result.get('total_parsed', result['declarations'])
    click.echo(f"Mathlib: parsed {total:,}, indexed {result['declarations']:,} "
               f"({result['inserted']:,} new, {result['updated']:,} updated, "
               f"{result['removed']:,} removed)")

    if config.topics:
        click.echo("Re-matching topics...")
        from .match import match_all_topics
        match_all_topics(db, config.topics)

    db.close()


@main.command("index-repo")
@click.argument("url")
@click.option("--branch", default="main", help="Branch to index")
@click.pass_context
def index_repo_cmd(ctx, url, branch):
    """Index a specific repo via regex extraction."""
    db, config = get_db(ctx.obj["data_dir"], ctx.obj["config_dir"])

    if db.get_schema_version() == 0:
        db.init_schema()

    from .update import index_repo
    result = index_repo(db, url=url, branch=branch)

    if result.get("error"):
        click.echo(f"Error: {result['error']}")
        sys.exit(1)

    click.echo(f"Indexed {result.get('name', url)}: "
               f"{result.get('declarations', 0):,} declarations "
               f"({result.get('inserted', 0)} new, {result.get('removed', 0)} removed)")

    if config.topics:
        repo = db.get_repo(url)
        if repo:
            from .match import match_all_topics
            match_all_topics(db, config.topics, repo_id=repo["id"])

    db.close()


@main.command("add")
@click.argument("target")
@click.option("--name", "-n", default="", help="Repository name (defaults to directory/repo name)")
@click.option("--description", "-d", default="", help="Repository description")
@click.option("--branch", "-b", default="main", help="Branch (for URLs)")
@click.pass_context
def add_cmd(ctx, target, name, description, branch):
    """Add a local path or repo URL to your index.

    Saves to local-repos.yaml and indexes immediately.

    \b
    Examples:
      lean-index add ~/Documents/Github/my-project/lean
      lean-index add https://github.com/user/repo
      lean-index add ./lean -n my-project -d "My formalization"
    """
    import yaml as _yaml

    db, config = get_db(ctx.obj["data_dir"], ctx.obj["config_dir"])
    if db.get_schema_version() == 0:
        db.init_schema()

    config_dir = Path(ctx.obj["config_dir"]) if ctx.obj["config_dir"] else Path.cwd()
    local_repos_file = config_dir / "local-repos.yaml"

    # Determine if target is a URL or local path
    is_url = target.startswith("http://") or target.startswith("https://") or target.startswith("git@")
    resolved_path = "" if is_url else str(Path(target).expanduser().resolve())

    if not is_url and not Path(resolved_path).is_dir():
        click.echo(f"Error: {resolved_path} is not a directory")
        sys.exit(1)

    if not name:
        if is_url:
            name = target.rstrip("/").split("/")[-1]
        else:
            name = Path(resolved_path).name

    # Save to local-repos.yaml
    if local_repos_file.exists():
        with open(local_repos_file) as f:
            data = _yaml.safe_load(f) or {}
    else:
        data = {}

    repos_list = data.setdefault("repos", [])

    # Check for duplicates
    for existing in repos_list:
        if is_url and existing.get("url") == target:
            click.echo(f"Already in local-repos.yaml: {target}")
            break
        if not is_url and existing.get("path") == resolved_path:
            click.echo(f"Already in local-repos.yaml: {resolved_path}")
            break
    else:
        entry = {"name": name}
        if is_url:
            entry["url"] = target
            if branch != "main":
                entry["branch"] = branch
        else:
            entry["path"] = resolved_path
        if description:
            entry["description"] = description
        repos_list.append(entry)

        with open(local_repos_file, "w") as f:
            _yaml.dump(data, f, default_flow_style=False, sort_keys=False)
        click.echo(f"Added to {local_repos_file}")

    # Index immediately
    if is_url:
        from .update import index_repo
        result = index_repo(db, url=target, name=name, branch=branch,
                            description=description, source="local-repos")
    else:
        from .update import index_local
        result = index_local(db, path=resolved_path, name=name, description=description)

    if result.get("error"):
        click.echo(f"Error: {result['error']}")
        sys.exit(1)

    click.echo(f"Indexed {result.get('name', target)}: "
               f"{result.get('declarations', 0):,} declarations "
               f"({result.get('inserted', 0)} new, {result.get('removed', 0)} removed)")

    # Match topics if configured
    if config.topics:
        url_key = target if is_url else ""
        if not url_key:
            try:
                r = subprocess.run(
                    ["git", "-C", resolved_path, "remote", "get-url", "origin"],
                    capture_output=True, text=True, timeout=5,
                )
                url_key = r.stdout.strip() if r.returncode == 0 else f"local://{resolved_path}"
            except Exception:
                url_key = f"local://{resolved_path}"

        repo = db.get_repo(url_key)
        if repo:
            click.echo(f"Matching {len(config.topics)} topics...")
            from .match import match_all_topics
            match_all_topics(db, config.topics, repo_id=repo["id"])

    db.close()


@main.command("add-repo")
@click.argument("url")
@click.option("--description", "-d", default="", help="Repository description")
@click.option("--branch", default="main", help="Branch to track")
@click.pass_context
def add_repo_cmd(ctx, url, description, branch):
    """Add a repo to the curated list and index it."""
    db, config = get_db(ctx.obj["data_dir"], ctx.obj["config_dir"])

    if db.get_schema_version() == 0:
        db.init_schema()

    name = url.rstrip("/").split("/")[-1]
    repo_id = db.upsert_repo(
        url=url, name=name, source="curated", branch=branch,
        description=description,
    )
    click.echo(f"Added {name} (id={repo_id})")

    # Index it
    from .update import index_repo
    result = index_repo(db, url=url, branch=branch, description=description, source="curated")
    click.echo(f"Indexed: {result.get('declarations', 0):,} declarations")

    db.close()


@main.command()
@click.argument("query", default="")
@click.option("--kind", "-k", default=None, help="Filter by kind (theorem, def, class, ...)")
@click.option("--topic", "-t", default=None, help="Filter by topic name")
@click.option("--repo", "-r", default=None, help="Filter by repo name")
@click.option("--since", default=None, help="Filter by first-seen date (YYYY-MM-DD)")
@click.option("--type", "type_mention", default=None, help="Filter by type signature mention")
@click.option("--limit", "-n", default=10, help="Max results (default: 10)")
@click.option("--all", "show_all", is_flag=True, help="Show all results (no limit)")
@click.option("--json", "output_json", is_flag=True, help="Output as JSON")
@click.pass_context
def search(ctx, query, kind, topic, repo, since, type_mention, limit, show_all, output_json):
    """Search declarations by text and/or structured filters.

    Results are ranked by relevance (text match, topic confidence, repo stars,
    documentation quality, declaration kind). Default: top 10 results.
    """
    db, config = get_db(ctx.obj["data_dir"], ctx.obj["config_dir"])

    if db.get_schema_version() == 0:
        click.echo("Database not initialized. Run 'lean-index init' first.")
        sys.exit(1)

    effective_limit = 100000 if show_all else limit

    from .search import search as do_search
    output = do_search(
        db, query=query, kind=kind, topic=topic, repo=repo,
        since=since, type_mention=type_mention, limit=effective_limit,
        output_json=output_json,
    )
    click.echo(output)
    db.close()


@main.command()
@click.pass_context
def stats(ctx):
    """Show index statistics."""
    db, config = get_db(ctx.obj["data_dir"], ctx.obj["config_dir"])

    if db.get_schema_version() == 0:
        click.echo("Database not initialized. Run 'lean-index init' first.")
        sys.exit(1)

    from .report import format_stats
    click.echo(format_stats(db))
    db.close()


@main.command()
@click.option("--since", required=True, help="Show changes since date (YYYY-MM-DD)")
@click.pass_context
def changelog(ctx, since):
    """Show new declarations since a date."""
    db, config = get_db(ctx.obj["data_dir"], ctx.obj["config_dir"])

    if db.get_schema_version() == 0:
        click.echo("Database not initialized. Run 'lean-index init' first.")
        sys.exit(1)

    from .report import format_changelog
    click.echo(format_changelog(db, since))
    db.close()


@main.command()
@click.pass_context
def repos(ctx):
    """List tracked repositories."""
    db, config = get_db(ctx.obj["data_dir"], ctx.obj["config_dir"])

    if db.get_schema_version() == 0:
        click.echo("Database not initialized. Run 'lean-index init' first.")
        sys.exit(1)

    from .report import format_repos
    click.echo(format_repos(db))
    db.close()


@main.command("fetch-db")
@click.argument("owner_repo")
@click.option("--output", "-o", default=None, help="Output path for the database")
@click.pass_context
def fetch_db(ctx, owner_repo, output):
    """Download pre-built database from a topic repo's GitHub release."""
    data_dir = ctx.obj.get("data_dir") or "data"
    out_path = output or os.path.join(data_dir, "index.db")
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

    click.echo(f"Fetching latest database from {owner_repo}...")

    # Try gh CLI first
    try:
        result = subprocess.run(
            ["gh", "release", "download", "--repo", owner_repo,
             "--pattern", "index.db", "--dir", os.path.dirname(out_path) or ".",
             "--clobber"],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0:
            click.echo(f"Downloaded to {out_path}")
            return
        else:
            logger.debug(f"gh release download failed: {result.stderr}")
    except FileNotFoundError:
        pass

    # Fallback: use GitHub API
    import requests
    api_url = f"https://api.github.com/repos/{owner_repo}/releases/latest"
    resp = requests.get(api_url, timeout=30)
    if resp.status_code != 200:
        click.echo(f"Error: Could not find releases for {owner_repo}")
        sys.exit(1)

    release = resp.json()
    db_asset = None
    for asset in release.get("assets", []):
        if asset["name"] == "index.db":
            db_asset = asset
            break

    if not db_asset:
        click.echo(f"Error: No index.db found in latest release of {owner_repo}")
        sys.exit(1)

    click.echo(f"Downloading {db_asset['name']} ({db_asset['size'] / 1024 / 1024:.1f} MB)...")
    dl_resp = requests.get(
        db_asset["browser_download_url"],
        stream=True, timeout=120,
    )
    dl_resp.raise_for_status()

    with open(out_path, "wb") as f:
        for chunk in dl_resp.iter_content(chunk_size=1024 * 1024):
            f.write(chunk)

    click.echo(f"Downloaded to {out_path}")


@main.command("preview-topics")
@click.option("--config-file", default=None, help="Alternative topics.yaml to preview")
@click.pass_context
def preview_topics(ctx, config_file):
    """Preview how many Mathlib declarations match current (or proposed) topics.

    Use this before changing topics.yaml to estimate the impact.
    """
    _, config = get_db(ctx.obj["data_dir"], ctx.obj["config_dir"])

    # Load alternative config if specified
    if config_file:
        import yaml
        from .config import TopicConfig
        with open(config_file) as f:
            data = yaml.safe_load(f) or {}
        topics = [TopicConfig.from_dict(t) for t in data.get("topics", [])]
        click.echo(f"Previewing with topics from: {config_file}")
    else:
        topics = config.topics

    if not topics:
        click.echo("No topics configured. Add topics to topics.yaml first.")
        sys.exit(1)

    # Load the cached Mathlib data
    cache_file = config.cache_dir / "header-data.bmp"
    if not cache_file.exists():
        click.echo(f"No cached Mathlib data at {cache_file}. Run 'lean-index init' first.")
        sys.exit(1)

    click.echo(f"Loading Mathlib cache...")
    from .extract.mathlib_cache import parse_mathlib_cache
    declarations, _ = parse_mathlib_cache(cache_file)
    click.echo(f"Total Mathlib declarations: {len(declarations):,}\n")

    from .match import match_declaration

    # Count matches per topic and overall
    per_topic = {t.name: {"module": 0, "type": 0, "name": 0, "total": 0, "examples": []}
                 for t in topics}
    matched_names = set()

    for decl in declarations:
        for topic in topics:
            matched, reason, confidence = match_declaration(decl, topic)
            if matched:
                matched_names.add(decl["name"])
                t = per_topic[topic.name]
                t["total"] += 1
                if reason.startswith("module:"):
                    t["module"] += 1
                elif reason.startswith("type:"):
                    t["type"] += 1
                elif reason.startswith("name:"):
                    t["name"] += 1
                if len(t["examples"]) < 3:
                    t["examples"].append(f"{decl['name']} ({decl.get('kind', '?')})")
                break  # Count each decl only once for the total

    click.echo(f"{'Topic':<30} {'Module':>8} {'Type':>8} {'Name':>8} {'Total':>8}")
    click.echo("-" * 70)
    for t in topics:
        s = per_topic[t.name]
        click.echo(f"{t.name:<30} {s['module']:>8,} {s['type']:>8,} {s['name']:>8,} {s['total']:>8,}")
    click.echo("-" * 70)
    click.echo(f"{'UNIQUE DECLARATIONS':<30} {'':>8} {'':>8} {'':>8} {len(matched_names):>8,}")
    click.echo(f"{'% of Mathlib':<30} {'':>8} {'':>8} {'':>8} {100*len(matched_names)/max(len(declarations),1):>7.1f}%")

    click.echo(f"\nExamples per topic:")
    for t in topics:
        examples = per_topic[t.name]["examples"]
        if examples:
            click.echo(f"  {t.name}:")
            for ex in examples:
                click.echo(f"    - {ex}")

    # If we have an existing DB, show the diff
    db_path = config.db_path
    if db_path.exists():
        db = IndexDB(db_path)
        current_count = db.get_declaration_count()
        if current_count > 0:
            click.echo(f"\nCompared to current index ({current_count:,} declarations):")
            delta = len(matched_names) - current_count
            if delta > 0:
                click.echo(f"  Would ADD ~{delta:,} declarations")
            elif delta < 0:
                click.echo(f"  Would REMOVE ~{abs(delta):,} declarations")
            else:
                click.echo(f"  No significant change expected")
        db.close()


@main.command("build-repo")
@click.argument("url")
@click.option("--branch", default="main", help="Branch to build")
@click.pass_context
def build_repo_cmd(ctx, url, branch):
    """Deep-index a repo via lake build + lean4export (optional)."""
    click.echo("Deep extraction via lake build is not yet implemented.")
    click.echo("Use 'lean-index index-repo' for regex-based extraction instead.")
    sys.exit(1)


if __name__ == "__main__":
    main()

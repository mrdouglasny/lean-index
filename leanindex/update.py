"""Orchestrates the full update cycle: discover, index, match, log."""

from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path

from .config import IndexConfig
from .db import IndexDB
from .discover import discover_all, _repo_matches_topics
from .extract.mathlib_cache import index_mathlib
from .extract.regex import extract_repo
from .match import match_all_topics

logger = logging.getLogger(__name__)


def check_repo_head(url: str, branch: str = "main") -> str | None:
    """Check the HEAD commit of a remote repo via git ls-remote."""
    try:
        result = subprocess.run(
            ["git", "ls-remote", url, f"refs/heads/{branch}"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().split()[0]
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def clone_repo(url: str, dest: Path, branch: str = "main") -> bool:
    """Shallow clone a repo."""
    try:
        result = subprocess.run(
            ["git", "clone", "--depth", "1", "--branch", branch,
             url, str(dest)],
            capture_output=True, text=True, timeout=120,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.warning(f"Clone failed for {url}: {e}")
        return False


def index_repo(db: IndexDB, url: str, name: str = "",
               branch: str = "main", description: str = "",
               stars: int = 0, source: str = "manual") -> dict:
    """Index a single non-Mathlib repo: clone, extract, insert."""
    if not name:
        name = url.rstrip("/").split("/")[-1]

    # Check if we already have this commit
    existing = db.get_repo(url)
    head_sha = check_repo_head(url, branch)

    if existing and head_sha and existing.get("last_commit_sha") == head_sha:
        logger.info(f"Skipping {name}: already at {head_sha[:8]}")
        return {"skipped": True, "name": name}

    # Upsert repo record
    repo_id = db.upsert_repo(
        url=url, name=name, source=source, branch=branch,
        description=description, stars=stars,
    )

    # Clone to temp directory
    with tempfile.TemporaryDirectory(prefix=f"lean-index-{name}-") as tmpdir:
        dest = Path(tmpdir) / name
        logger.info(f"Cloning {url} -> {dest}")

        if not clone_repo(url, dest, branch):
            logger.warning(f"Failed to clone {url}")
            return {"error": "clone_failed", "name": name}

        # Extract declarations
        decls = extract_repo(dest)

        if not decls:
            logger.info(f"No declarations found in {name}")
            return {"declarations": 0, "name": name}

        # Insert into DB
        inserted, updated = db.bulk_upsert_declarations(repo_id, decls)
        current_names = {d["name"] for d in decls}
        removed = db.mark_stale_declarations(repo_id, current_names)

        if head_sha:
            db.update_repo_indexed(repo_id, head_sha)

    result = {
        "name": name,
        "declarations": len(decls),
        "inserted": inserted,
        "updated": updated,
        "removed": removed,
    }
    logger.info(f"Indexed {name}: {result}")
    return result


def index_local(db: IndexDB, path: str, name: str = "",
                url: str = "", description: str = "") -> dict:
    """Index a local Lean project directory (no cloning)."""
    repo_dir = Path(path).resolve()
    if not repo_dir.is_dir():
        return {"error": f"Not a directory: {repo_dir}", "name": name}

    if not name:
        name = repo_dir.name

    if not url:
        # Try to get URL from git remote
        try:
            result = subprocess.run(
                ["git", "-C", str(repo_dir), "remote", "get-url", "origin"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                url = result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        if not url:
            url = f"local://{repo_dir}"

    # Get current HEAD
    head_sha = None
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            head_sha = result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    # Upsert repo record
    repo_id = db.upsert_repo(
        url=url, name=name, source="local",
        description=description,
    )

    # Extract declarations directly from local path
    decls = extract_repo(repo_dir)

    if not decls:
        logger.info(f"No declarations found in {name}")
        return {"declarations": 0, "name": name}

    # Insert into DB
    inserted, updated = db.bulk_upsert_declarations(repo_id, decls)
    current_names = {d["name"] for d in decls}
    removed = db.mark_stale_declarations(repo_id, current_names)

    if head_sha:
        db.update_repo_indexed(repo_id, head_sha)

    result = {
        "name": name,
        "declarations": len(decls),
        "inserted": inserted,
        "updated": updated,
        "removed": removed,
    }
    logger.info(f"Indexed local {name}: {result}")
    return result


def run_update(db: IndexDB, config: IndexConfig) -> dict:
    """Run a full update cycle.

    1. Re-index Mathlib cache
    2. Discover repos
    3. Check commits and index changed repos
    4. Match topics
    5. Log update
    """
    stats = {
        "repos_checked": 0,
        "repos_updated": 0,
        "new_declarations": 0,
        "removed_declarations": 0,
        "errors": [],
    }

    # 1. Re-index Mathlib
    logger.info("=== Step 1: Indexing Mathlib ===")
    try:
        topics_for_filter = config.topics if config.topics else None
        mathlib_result = index_mathlib(db, config.cache_dir, topics=topics_for_filter)
        stats["new_declarations"] += mathlib_result.get("inserted", 0)
        stats["removed_declarations"] += mathlib_result.get("removed", 0)
        stats["repos_updated"] += 1
    except Exception as e:
        logger.error(f"Mathlib indexing failed: {e}")
        stats["errors"].append(f"mathlib: {e}")

    # 2. Discover repos
    logger.info("=== Step 2: Discovering repos ===")
    try:
        discovered = discover_all(config, config.cache_dir)
    except Exception as e:
        logger.error(f"Discovery failed: {e}")
        discovered = []
        stats["errors"].append(f"discovery: {e}")

    # Add curated repos
    for repo_entry in config.repos:
        already = any(r["url"] == repo_entry.url for r in discovered)
        if not already:
            discovered.append({
                "url": repo_entry.url,
                "name": repo_entry.url.rstrip("/").split("/")[-1],
                "description": repo_entry.description,
                "source": "curated",
                "branch": repo_entry.branch,
            })

    # 3. Pre-filter repos by topic relevance (skip irrelevant Reservoir repos)
    if config.topics:
        pre_filter_count = len(discovered)
        discovered = [r for r in discovered if _repo_matches_topics(r, config.topics)]
        skipped = pre_filter_count - len(discovered)
        if skipped > 0:
            logger.info(f"Pre-filtered {skipped} repos with no topic relevance "
                        f"({len(discovered)} remaining)")

    # 4. Index repos
    logger.info(f"=== Step 3: Indexing {len(discovered)} repos ===")
    for repo_info in discovered:
        url = repo_info.get("url", "")
        if not url:
            continue
        stats["repos_checked"] += 1

        try:
            result = index_repo(
                db, url=url,
                name=repo_info.get("name", ""),
                branch=repo_info.get("branch", "main"),
                description=repo_info.get("description", ""),
                stars=repo_info.get("stars", 0),
                source=repo_info.get("source", "discovered"),
            )
            if not result.get("skipped"):
                stats["repos_updated"] += 1
                stats["new_declarations"] += result.get("inserted", 0)
                stats["removed_declarations"] += result.get("removed", 0)
        except Exception as e:
            logger.error(f"Error indexing {url}: {e}")
            stats["errors"].append(f"{url}: {e}")

    # 5. Index local repos (from local-repos.yaml)
    if config.local_repos:
        logger.info(f"=== Step 4: Indexing {len(config.local_repos)} local repos ===")
        for entry in config.local_repos:
            try:
                if entry.path:
                    result = index_local(
                        db, path=entry.path,
                        name=entry.name,
                        description=entry.description,
                    )
                elif entry.url:
                    result = index_repo(
                        db, url=entry.url,
                        name=entry.name,
                        branch=entry.branch,
                        description=entry.description,
                        source="local-repos",
                    )
                else:
                    continue

                label = entry.path or entry.url
                if result.get("error"):
                    logger.warning(f"Local repo {label}: {result['error']}")
                    stats["errors"].append(f"local:{label}: {result['error']}")
                elif not result.get("skipped"):
                    stats["repos_updated"] += 1
                    stats["new_declarations"] += result.get("inserted", 0)
                    stats["removed_declarations"] += result.get("removed", 0)
            except Exception as e:
                logger.error(f"Error indexing {entry.path or entry.url}: {e}")
                stats["errors"].append(f"local:{entry.path or entry.url}: {e}")

    # 6. Match topics
    if config.topics:
        logger.info("=== Step 5: Matching topics ===")
        try:
            match_all_topics(db, config.topics)
        except Exception as e:
            logger.error(f"Topic matching failed: {e}")
            stats["errors"].append(f"matching: {e}")

    # 6. Log update
    summary = (f"Checked {stats['repos_checked']} repos, "
               f"updated {stats['repos_updated']}, "
               f"+{stats['new_declarations']} -{stats['removed_declarations']} declarations")
    if stats["errors"]:
        summary += f", {len(stats['errors'])} errors"

    db.log_update(
        repos_checked=stats["repos_checked"],
        repos_updated=stats["repos_updated"],
        new_decls=stats["new_declarations"],
        removed_decls=stats["removed_declarations"],
        summary=summary,
    )

    logger.info(f"Update complete: {summary}")
    return stats

#!/usr/bin/env python3
"""
relocate_gittree.py — one-shot host migration tool.

Moves `out/<project>/git_repo_dir_<sha>/.git` → `out/<project>/_gittree_<sha>/.git`
(or the reverse with --reverse), so the oracle history sits in a SIBLING of the
work-tree instead of inside it.

Idempotent: re-running is a no-op. Safe to dry-run.
"""
from __future__ import annotations

import argparse
import fnmatch
import logging
import re
import shutil
import sys
from pathlib import Path

LOG = logging.getLogger("relocate_gittree")

# Match "git_repo_dir_<sha>" where sha is a non-empty token of hex-or-word chars.
WORKDIR_RE = re.compile(r"^git_repo_dir_(?P<sha>[A-Za-z0-9._-]+)$")


def _default_root() -> Path:
    return Path(__file__).resolve().parent / "out"


def _iter_workdirs(root: Path, project_filter: str | None):
    """Yield (project, sha, workdir_path) for every git_repo_dir_<sha> under root."""
    if not root.is_dir():
        LOG.error("root does not exist or is not a directory: %s", root)
        return
    for project_dir in sorted(root.iterdir()):
        if not project_dir.is_dir():
            continue
        project = project_dir.name
        if project_filter and not fnmatch.fnmatch(project, project_filter):
            continue
        for child in sorted(project_dir.iterdir()):
            if not child.is_dir():
                continue
            m = WORKDIR_RE.match(child.name)
            if not m:
                continue
            yield project, m.group("sha"), child


def _move(src: Path, dst: Path, dry_run: bool) -> None:
    if dry_run:
        LOG.info("[dry-run] mv %s -> %s", src, dst)
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    LOG.info("mv %s -> %s", src, dst)
    shutil.move(str(src), str(dst))


def _rmdir_if_empty(p: Path, dry_run: bool) -> None:
    try:
        if any(p.iterdir()):
            return
    except FileNotFoundError:
        return
    if dry_run:
        LOG.info("[dry-run] rmdir %s", p)
        return
    LOG.info("rmdir %s", p)
    p.rmdir()


def run(root: Path, dry_run: bool, reverse: bool, project_filter: str | None) -> int:
    migrated = 0
    skipped = 0
    conflicts = 0
    errors = 0

    for project, sha, workdir in _iter_workdirs(root, project_filter):
        src = workdir / ".git"
        dst = root / project / f"_gittree_{sha}" / ".git"

        if reverse:
            # reverse: dst (sibling) -> src (inside workdir)
            mv_from, mv_to = dst, src
        else:
            # forward: src (inside workdir) -> dst (sibling)
            mv_from, mv_to = src, dst

        from_exists = mv_from.exists()
        to_exists = mv_to.exists()

        if to_exists and not from_exists:
            LOG.debug("skip (already at target): %s@%s", project, sha)
            skipped += 1
            continue

        if not from_exists and not to_exists:
            LOG.debug("skip (neither src nor dst exists): %s@%s", project, sha)
            skipped += 1
            continue

        if from_exists and to_exists:
            LOG.warning("conflict (both src and dst exist): %s @ %s", project, sha)
            LOG.warning("  src=%s", mv_from)
            LOG.warning("  dst=%s", mv_to)
            conflicts += 1
            continue

        # from_exists and not to_exists → actually do the move
        try:
            _move(mv_from, mv_to, dry_run)
            migrated += 1
            if reverse:
                # the sibling parent (_gittree_<sha>/) may now be empty; tidy up.
                _rmdir_if_empty(dst.parent, dry_run)
        except Exception as exc:  # pragma: no cover - defensive
            LOG.error("error moving %s -> %s: %s", mv_from, mv_to, exc)
            errors += 1

    LOG.info(
        "report: migrated=%d, skipped=%d, conflicts=%d, errors=%d",
        migrated, skipped, conflicts, errors,
    )
    return 0 if (errors == 0 and conflicts == 0) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=_default_root(),
                        help="root directory containing per-project subdirs (default: ./out)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print actions, don't move")
    parser.add_argument("--reverse", action="store_true",
                        help="move sibling _gittree_<sha>/.git back into git_repo_dir_<sha>/.git")
    parser.add_argument("--project", type=str, default=None,
                        help="optional project-name filter (glob)")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="DEBUG-level logging (print every action even if no-op)")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    return run(
        root=args.root.resolve(),
        dry_run=args.dry_run,
        reverse=args.reverse,
        project_filter=args.project,
    )


if __name__ == "__main__":
    sys.exit(main())

"""
test_relocate_gittree.py — Phase 3 unit tests for the host-side migration tool
`relocate_gittree.py` that moves each bug's `.git` from inside `git_repo_dir_<sha>/`
to a sibling `_gittree_<sha>/` (and back via --reverse).

These tests run entirely on the host with a `tmp_path` fake `out/` tree. They do
NOT require the Docker container, the webapp, or any network.

Skipped gracefully if `relocate_gittree.py` does not yet exist (another agent may
not have created it).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
RELOCATE_SCRIPT = REPO_ROOT / "relocate_gittree.py"


pytestmark = pytest.mark.skipif(
    not RELOCATE_SCRIPT.exists(),
    reason="relocate_gittree.py not yet created (Phase 1 pending)",
)


# ────────────────────────────────────────────────────────────────────
#  helpers
# ────────────────────────────────────────────────────────────────────

def _git(*args, cwd):
    """Run git with a clean identity in `cwd`. Returns CompletedProcess."""
    env = os.environ.copy()
    env.setdefault("GIT_AUTHOR_NAME", "t")
    env.setdefault("GIT_AUTHOR_EMAIL", "t@t")
    env.setdefault("GIT_COMMITTER_NAME", "t")
    env.setdefault("GIT_COMMITTER_EMAIL", "t@t")
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd), env=env,
        capture_output=True, text=True, check=False, timeout=60,
    )


def _make_fake_bug(root: Path, project: str, sha: str, src_text: str = "int main(){return 0;}\n"):
    """Create root/<project>/git_repo_dir_<sha>/ with a real one-commit .git/.
    Returns the workdir path and the file digest of src.c."""
    workdir = root / project / f"git_repo_dir_{sha}"
    workdir.mkdir(parents=True)
    (workdir / "src.c").write_text(src_text)
    r = _git("init", "-q", "-b", "master", cwd=workdir)
    # `-b master` may not exist on older git; fall back to plain init.
    if r.returncode != 0:
        _git("init", "-q", cwd=workdir)
    _git("config", "user.email", "t@t", cwd=workdir)
    _git("config", "user.name", "t", cwd=workdir)
    _git("add", "src.c", cwd=workdir)
    r = _git("commit", "-q", "-m", "init", cwd=workdir)
    assert r.returncode == 0, f"fake bug git commit failed: {r.stderr}"
    return workdir


def _run_script(*extra, root: Path) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(RELOCATE_SCRIPT), "--root", str(root), *extra]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)


# ────────────────────────────────────────────────────────────────────
#  tests
# ────────────────────────────────────────────────────────────────────

def test_forward_migration(tmp_path):
    """`relocate_gittree.py` moves .git out of golden into a sibling _gittree_."""
    root = tmp_path / "out"
    workdir = _make_fake_bug(root, "proj1", "AAA")
    src_before = (workdir / "src.c").read_bytes()

    result = _run_script(root=root)
    assert result.returncode == 0, f"stderr={result.stderr}\nstdout={result.stdout}"

    sibling_git = root / "proj1" / "_gittree_AAA" / ".git"
    assert sibling_git.is_dir(), f"sibling _gittree_AAA/.git missing\nstderr={result.stderr}"
    assert not (workdir / ".git").exists(), "old .git in golden still present"

    # source file untouched, byte-for-byte
    assert (workdir / "src.c").read_bytes() == src_before

    # the relocated .git is still a real, working repo
    r = _git("--git-dir", str(sibling_git), "rev-parse", "HEAD", cwd=root)
    assert r.returncode == 0, f"relocated git not usable: {r.stderr}"
    assert r.stdout.strip(), "git rev-parse HEAD returned empty"


def test_idempotent(tmp_path):
    """Running twice leaves the same end state; second run reports skipped."""
    root = tmp_path / "out"
    _make_fake_bug(root, "proj1", "AAA")

    r1 = _run_script(root=root)
    assert r1.returncode == 0

    sibling_git = root / "proj1" / "_gittree_AAA" / ".git"
    assert sibling_git.is_dir()

    # second pass: idempotent
    r2 = _run_script(root=root)
    assert r2.returncode == 0
    # script logs to stderr by default (logging.basicConfig)
    combined = (r2.stderr + r2.stdout).lower()
    # Either a literal "skip" or migrated=0 must show up.
    assert ("skip" in combined) or ("migrated=0" in combined), (
        f"second run did not report a skip / zero-migration:\nstderr={r2.stderr}\nstdout={r2.stdout}"
    )

    # End state unchanged.
    assert sibling_git.is_dir()
    assert not (root / "proj1" / "git_repo_dir_AAA" / ".git").exists()


def test_reverse(tmp_path):
    """After forward migration, --reverse restores the original layout."""
    root = tmp_path / "out"
    workdir = _make_fake_bug(root, "proj1", "AAA")
    src_before = (workdir / "src.c").read_bytes()

    assert _run_script(root=root).returncode == 0
    sibling_git = root / "proj1" / "_gittree_AAA" / ".git"
    assert sibling_git.is_dir()

    r = _run_script("--reverse", root=root)
    assert r.returncode == 0, f"--reverse failed: {r.stderr}"

    # Layout is back to original.
    assert (workdir / ".git").is_dir(), "reverse failed to restore .git in golden"
    assert not sibling_git.exists(), "sibling _gittree_AAA/.git lingered after --reverse"
    assert (workdir / "src.c").read_bytes() == src_before

    # Repo is still functional in its old home.
    rp = _git("rev-parse", "HEAD", cwd=workdir)
    assert rp.returncode == 0 and rp.stdout.strip()


def test_dry_run(tmp_path):
    """`--dry-run` prints a plan but moves nothing."""
    root = tmp_path / "out"
    workdir = _make_fake_bug(root, "proj1", "AAA")

    r = _run_script("--dry-run", root=root)
    assert r.returncode == 0, f"dry-run failed: {r.stderr}"

    assert (workdir / ".git").is_dir(), "dry-run actually moved the .git"
    assert not (root / "proj1" / "_gittree_AAA").exists(), (
        "dry-run created the sibling _gittree directory"
    )
    combined = (r.stderr + r.stdout).lower()
    assert "dry-run" in combined or "[dry-run]" in combined or "would" in combined, (
        f"dry-run did not announce itself in logs:\n{r.stderr}\n{r.stdout}"
    )


def test_no_git(tmp_path):
    """A bug directory without a .git/ is skipped, not an error."""
    root = tmp_path / "out"
    workdir = root / "proj1" / "git_repo_dir_AAA"
    workdir.mkdir(parents=True)
    (workdir / "src.c").write_text("// no git here\n")

    r = _run_script(root=root)
    assert r.returncode == 0, f"unexpected non-zero exit: {r.stderr}"
    # Nothing should have been created.
    assert not (root / "proj1" / "_gittree_AAA").exists()
    # Working tree untouched.
    assert (workdir / "src.c").read_text() == "// no git here\n"


def test_project_filter(tmp_path):
    """`--project proj2` only migrates proj2; proj1 is untouched."""
    root = tmp_path / "out"
    wd1 = _make_fake_bug(root, "proj1", "AAA")
    wd2 = _make_fake_bug(root, "proj2", "BBB")

    r = _run_script("--project", "proj2", root=root)
    assert r.returncode == 0, f"stderr={r.stderr}"

    # proj2 migrated
    assert (root / "proj2" / "_gittree_BBB" / ".git").is_dir()
    assert not (wd2 / ".git").exists()

    # proj1 left alone
    assert (wd1 / ".git").is_dir()
    assert not (root / "proj1" / "_gittree_AAA").exists()

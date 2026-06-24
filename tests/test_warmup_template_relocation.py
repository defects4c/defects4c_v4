"""
test_warmup_template_relocation.py — end-to-end-style proof, no GitHub.

`git_setup.sh` is hardcoded to clone from GitHub, so a true end-to-end run is
not feasible in CI. Instead, this test reproduces the post-setup on-disk state
manually from a local bare repo, then exercises the rest of the pipeline:

  1. Build a fake bug with a local-bare-repo origin (one buggy + one fix commit
     on src/foo.c).
  2. Hand-construct the pre-migration layout under tmp_path/out/<proj>/.
  3. Run `relocate_gittree.py --root tmp/out` and verify the post-migration layout.
  4. Render `workflow_cmake_tpl.jinja` via `BugsInfo` and assert the rendered
     `run_reproduce.sh` references `--git-dir=.../_gittree_<sha>/.git`.

The full real warmup (cmake + ninja + ctest) is NOT exercised — that requires
the bug's source tree, which is project-specific. We assert only the layout +
rendered-script properties that the relocation is responsible for.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TPL_DIR = REPO_ROOT / "defectsc_tpl"
RELOCATE_SCRIPT = REPO_ROOT / "relocate_gittree.py"
WORKFLOW_TPL = TPL_DIR / "projects_v1" / "workflow_cmake_tpl.jinja"
BUG_HELPER = TPL_DIR / "bug_helper_v1_out2.py"


if str(TPL_DIR) not in sys.path:
    sys.path.insert(0, str(TPL_DIR))


PROJECT_NAME = "fakeorg___fakeproj"


def _git(*args, cwd, check=False):
    env = os.environ.copy()
    env.setdefault("GIT_AUTHOR_NAME", "t")
    env.setdefault("GIT_AUTHOR_EMAIL", "t@t")
    env.setdefault("GIT_COMMITTER_NAME", "t")
    env.setdefault("GIT_COMMITTER_EMAIL", "t@t")
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd), env=env,
        capture_output=True, text=True, check=check, timeout=60,
    )


@pytest.fixture
def fake_origin(tmp_path):
    """Build a local bare repo with two commits.

    Returns (bare_path, commit_before_sha, commit_after_sha, src_path_in_tree).
    """
    work = tmp_path / "origin_work"
    work.mkdir()
    _git("init", "-q", "-b", "master", cwd=work)
    # Older git: fall back
    if not (work / ".git").is_dir():
        _git("init", "-q", cwd=work)
    _git("config", "user.email", "t@t", cwd=work)
    _git("config", "user.name", "t", cwd=work)
    (work / "src").mkdir()
    (work / "src" / "foo.c").write_text("int foo(void){return 1;} /*buggy*/\n")
    _git("add", "src/foo.c", cwd=work)
    r = _git("commit", "-q", "-m", "buggy", cwd=work)
    assert r.returncode == 0, r.stderr
    commit_before = _git("rev-parse", "HEAD", cwd=work).stdout.strip()

    (work / "src" / "foo.c").write_text("int foo(void){return 0;} /*fixed*/\n")
    r = _git("commit", "-aq", "-m", "fix", cwd=work)
    assert r.returncode == 0, r.stderr
    commit_after = _git("rev-parse", "HEAD", cwd=work).stdout.strip()

    bare = tmp_path / "origin.git"
    r = _git("clone", "--bare", str(work), str(bare), cwd=tmp_path)
    assert r.returncode == 0, r.stderr

    return bare, commit_before, commit_after, "src/foo.c"


def _setup_pre_migration_state(tmp_path, fake_origin):
    """Hand-build the pre-migration on-disk layout under tmp_path/out/."""
    bare, commit_before, commit_after, src_path = fake_origin

    out_root = tmp_path / "out"
    proj_dir = out_root / PROJECT_NAME
    sha = commit_after
    workdir = proj_dir / f"git_repo_dir_{sha}"
    workdir.parent.mkdir(parents=True, exist_ok=True)

    # `git clone bare workdir` creates workdir/.git pointing at bare history.
    r = _git("clone", "-q", str(bare), str(workdir), cwd=tmp_path)
    assert r.returncode == 0, r.stderr

    # Make sure HEAD = commit_after (the fix), matching real git_setup.sh.
    r = _git("checkout", "-q", commit_after, cwd=workdir)
    assert r.returncode == 0, r.stderr

    return out_root, sha, commit_before, commit_after, src_path


def _write_metadata(tmp_path, sha, commit_before, src_path):
    pv1 = tmp_path / "projects_v1"
    proj = pv1 / PROJECT_NAME
    proj.mkdir(parents=True)
    # Mirror the real projects_v1/ common templates next to the fake project,
    # since _build_tpl_path() / _workflow_reproduce_tpl() resolve from pv1.
    real_pv1 = TPL_DIR / "projects_v1"
    for fname in ("common_build_tpl.jinja", "common_test_tpl.jinja",
                  "workflow_cmake_tpl.jinja", "workflow_cmake_rebuild_tpl.jinja",
                  "workflow_cmake_compile_test_tpl.jinja"):
        src = real_pv1 / fname
        if src.is_file():
            shutil.copy(str(src), str(pv1 / fname))
    (proj / "project.json").write_text(json.dumps({
        "env": ["CC=gcc", "CXX=g++"],
        "c_compile": {
            "build": "ninja", "build_flags": [],
            "test": "ctest", "test_flags": [],
        },
    }))
    (proj / "bugs_list_new.json").write_text(json.dumps([{
        "commit_after": sha,
        "commit_before": commit_before,
        "commit_date": "2026-01-01",
        "files": {"src": [src_path], "test": ["tests/test_foo.c"]},
        "unittest": "test_foo",
        "type": "bug",
        "status": "ok",
        "c_compile": {"build": None, "build_flags": [], "test_flags": []},
        "url": "file:///dev/null",
    }]))
    return pv1


def test_post_migration_layout_and_template_render(tmp_path, monkeypatch, fake_origin):
    """End-to-end-style: pre-migration → relocate → render → assert."""
    if not RELOCATE_SCRIPT.exists():
        pytest.skip("relocate_gittree.py not yet created (Phase 1 pending)")
    if not WORKFLOW_TPL.exists():
        pytest.skip(f"workflow_cmake_tpl.jinja missing at {WORKFLOW_TPL}")

    # 1+2: pre-migration on-disk state
    out_root, sha, commit_before, commit_after, src_path = _setup_pre_migration_state(
        tmp_path, fake_origin,
    )
    workdir = out_root / PROJECT_NAME / f"git_repo_dir_{sha}"
    assert (workdir / ".git").is_dir(), "pre-migration: .git inside golden"
    assert (workdir / "src" / "foo.c").exists()

    # 3: run the migration tool
    r = subprocess.run(
        [sys.executable, str(RELOCATE_SCRIPT), "--root", str(out_root)],
        capture_output=True, text=True, timeout=120,
    )
    assert r.returncode == 0, f"relocate_gittree failed: {r.stderr}\n{r.stdout}"

    sibling_git = out_root / PROJECT_NAME / f"_gittree_{sha}" / ".git"
    assert sibling_git.is_dir(), f"post-migration: sibling .git missing\n{r.stderr}"
    assert not (workdir / ".git").exists(), "post-migration: golden still has .git"

    # 3b: oracle history is still readable via --git-dir
    rp = _git("--git-dir", str(sibling_git), "rev-parse", "HEAD", cwd=tmp_path)
    assert rp.returncode == 0 and rp.stdout.strip() == commit_after, rp.stderr

    # 3c: agent's "cd ..; git log" from the workdir parent is now blocked.
    # (We probe the parent of workdir, which is the project dir; no .git there.)
    rcheat = _git("-C", str(workdir.parent), "log", "-1", cwd=tmp_path)
    assert rcheat.returncode != 0, (
        f"cheat path NOT blocked: parent git log succeeded with {rcheat.stdout!r}"
    )

    # 4: render workflow_cmake_tpl.jinja via BugsInfo and assert relocation form.
    pv1 = _write_metadata(tmp_path, sha, commit_before, src_path)
    import bug_helper_v1_out2 as bh
    monkeypatch.setattr(bh, "ROOT_DIR", str(out_root) + "/")
    monkeypatch.setitem(bh.PROJECTS_DIRS, "v1", str(pv1))
    monkeypatch.setitem(bh.PROJECTS_DIRS, "v0", str(tmp_path / "nonexistent_v0"))

    info = bh.BugsInfo(PROJECT_NAME, sha)
    if "gittree_dir" not in info.meta_info:
        pytest.skip("BugsInfo does not yet inject gittree_dir (Phase 2 pending)")

    # set_reproduce_build renders run_reproduce.sh into wrk_git (= workdir)
    info.set_reproduce_build()
    repro = workdir / "run_reproduce.sh"
    assert repro.is_file(), "set_reproduce_build did not write run_reproduce.sh"
    body = repro.read_text()

    assert "git --git-dir=" in body, (
        "rendered run_reproduce.sh does not use git --git-dir=... after relocation:\n"
        + body
    )
    expected_path = str(sibling_git.parent)  # .../_gittree_<sha>
    assert expected_path in body, (
        f"rendered script does not reference {expected_path}:\n{body}"
    )
    # And the legacy `git -C <repo_dir> checkout` lines must NOT be emitted.
    assert f"git -C {workdir} checkout" not in body, (
        "rendered script still uses legacy `git -C <repo_dir> checkout`:\n" + body
    )

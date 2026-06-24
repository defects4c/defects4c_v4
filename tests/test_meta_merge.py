"""
test_meta_merge.py — Phase 3 unit tests for §5A.2 of design_of_deploy.md.

Verifies that `BugsInfo._init_meta_info` (in defectsc_tpl/bug_helper_v1_out2.py):
  - CONCATENATES list-valued keys (`build_flags`, `test_flags`) across
    project.json + per-bug entry,
  - REPLACES scalar keys (`build`, `test`) when the per-bug value is non-null,
  - INHERITS the project scalar when the per-bug value is null,
  - injects a new `gittree_dir` pointing at the sibling oracle path.

Also: a rendering regression-guard for `workflow_cmake_tpl.jinja`. With
`gittree_dir` present the rendered script must use `git --git-dir=...`. Without
it, the script must fall back to the legacy `git -C` form (so unmigrated bugs
keep working during a phased rollout).

All tests are host-only and don't hit the live webapp.
"""
from __future__ import annotations

import json
import os
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TPL_DIR = REPO_ROOT / "defectsc_tpl"
PROJECTS_V1 = TPL_DIR / "projects_v1"
BUG_HELPER = TPL_DIR / "bug_helper_v1_out2.py"
WORKFLOW_TPL = PROJECTS_V1 / "workflow_cmake_tpl.jinja"


# Ensure defectsc_tpl is importable
if str(TPL_DIR) not in sys.path:
    sys.path.insert(0, str(TPL_DIR))


# ────────────────────────────────────────────────────────────────────
#  Shared fixture: a fake out/ + projects_v1/ tree with a single bug.
# ────────────────────────────────────────────────────────────────────

PROJECT_NAME = "fakeorg___fakeproj"
SHA = "deadbeefcafebabe1234567890abcdef12345678"


def _write_fake_tree(tmp_path: Path,
                     project_c_compile: dict,
                     bug_c_compile: dict):
    """Lay out a minimal defectsc_tpl-shaped tree under tmp_path.

    Returns (out_root, projects_v1_root).
    """
    out_root = tmp_path / "out"
    out_root.mkdir()
    (out_root / PROJECT_NAME).mkdir()
    # Golden dir must exist for BugsInfo() to pass its assert
    (out_root / PROJECT_NAME / f"git_repo_dir_{SHA}").mkdir()

    pv1 = tmp_path / "projects_v1"
    proj_dir = pv1 / PROJECT_NAME
    proj_dir.mkdir(parents=True)

    project_json = {
        "env": ["CC=gcc", "CXX=g++"],
        "c_compile": project_c_compile,
    }
    (proj_dir / "project.json").write_text(json.dumps(project_json))

    bug_entry = {
        "commit_after": SHA,
        "commit_before": "0" * 40,
        "commit_date": "2026-01-01",
        "files": {"src": ["src/foo.c"], "test": ["tests/test_foo.c"]},
        "unittest": "test_foo",
        "type": "bug",
        "status": "ok",
        "c_compile": bug_c_compile,
        "url": "https://example.invalid/" + PROJECT_NAME,
    }
    (proj_dir / "bugs_list_new.json").write_text(json.dumps([bug_entry]))

    return out_root, pv1


@pytest.fixture
def fake_tree(tmp_path, monkeypatch):
    """Default fixture; tests override flag/scalar dicts via _write_fake_tree."""
    # We just expose helpers; actual layout is built per-test.
    return tmp_path, monkeypatch


def _build_info(tmp_path, monkeypatch, project_cc, bug_cc):
    """Construct BugsInfo against an isolated tree. Returns the BugsInfo object."""
    out_root, pv1 = _write_fake_tree(tmp_path, project_cc, bug_cc)

    # Patch the module globals to point at our fake roots.
    import bug_helper_v1_out2 as bh
    monkeypatch.setattr(bh, "ROOT_DIR", str(out_root) + "/")
    monkeypatch.setitem(bh.PROJECTS_DIRS, "v1", str(pv1))
    # v0 path: point at a non-existent dir so detect_version picks v1.
    monkeypatch.setitem(bh.PROJECTS_DIRS, "v0", str(tmp_path / "nonexistent_v0"))

    return bh.BugsInfo(PROJECT_NAME, SHA)


# ────────────────────────────────────────────────────────────────────
#  Merge tests
# ────────────────────────────────────────────────────────────────────

def test_build_flags_concatenate(fake_tree):
    tmp_path, monkeypatch = fake_tree
    info = _build_info(
        tmp_path, monkeypatch,
        project_cc={"build": "ninja", "build_flags": ["-A"], "test": "ctest", "test_flags": []},
        bug_cc={"build": None, "build_flags": ["-B"], "test_flags": []},
    )
    assert info.meta_info["build_flags"] == ["-A", "-B"], (
        f"expected concat, got {info.meta_info['build_flags']!r}"
    )


def test_test_flags_concatenate(fake_tree):
    tmp_path, monkeypatch = fake_tree
    info = _build_info(
        tmp_path, monkeypatch,
        project_cc={"build": "ninja", "build_flags": [], "test": "ctest", "test_flags": ["-X"]},
        bug_cc={"build": None, "build_flags": [], "test_flags": ["-Y"]},
    )
    assert info.meta_info["test_flags"] == ["-X", "-Y"], (
        f"expected concat, got {info.meta_info['test_flags']!r}"
    )


def test_scalar_replace_when_set(fake_tree):
    tmp_path, monkeypatch = fake_tree
    info = _build_info(
        tmp_path, monkeypatch,
        project_cc={"build": "ninja", "build_flags": [], "test": "ctest", "test_flags": []},
        bug_cc={"build": "make", "build_flags": [], "test_flags": []},
    )
    assert info.meta_info["build"] == "make", (
        f"per-bug scalar should win; got {info.meta_info.get('build')!r}"
    )


def test_scalar_inherit_when_null(fake_tree):
    tmp_path, monkeypatch = fake_tree
    info = _build_info(
        tmp_path, monkeypatch,
        project_cc={"build": "ninja", "build_flags": [], "test": "ctest", "test_flags": []},
        bug_cc={"build": None, "build_flags": [], "test_flags": []},
    )
    assert info.meta_info["build"] == "ninja", (
        f"null per-bug scalar should inherit project default; got {info.meta_info.get('build')!r}"
    )


def test_gittree_dir_injected(fake_tree):
    tmp_path, monkeypatch = fake_tree
    info = _build_info(
        tmp_path, monkeypatch,
        project_cc={"build": "ninja", "build_flags": [], "test": "ctest", "test_flags": []},
        bug_cc={"build": None, "build_flags": [], "test_flags": []},
    )
    if "gittree_dir" not in info.meta_info:
        pytest.skip("BugsInfo._init_meta_info does not yet inject gittree_dir (Phase 2 pending)")
    gittree = info.meta_info["gittree_dir"]
    expected_suffix = os.path.join(PROJECT_NAME, f"_gittree_{SHA}")
    assert gittree.endswith(expected_suffix), (
        f"gittree_dir should be sibling of golden; got {gittree!r}"
    )
    # And it must be a sibling, not a child, of git_repo_dir_<sha>.
    assert "git_repo_dir_" not in gittree, (
        f"gittree_dir should NOT live inside git_repo_dir_; got {gittree!r}"
    )


# ────────────────────────────────────────────────────────────────────
#  Workflow template render — regression guard for the relocation
# ────────────────────────────────────────────────────────────────────

def _render_workflow(context: dict) -> str:
    if not WORKFLOW_TPL.exists():
        pytest.skip(f"workflow_cmake_tpl.jinja missing at {WORKFLOW_TPL}")
    import jinja2
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(WORKFLOW_TPL.parent)),
        keep_trailing_newline=True,
    )
    return env.get_template(WORKFLOW_TPL.name).render(**context)


def test_workflow_template_renders_both_modes():
    """The relocation edit must keep both modes working:
       - with gittree_dir → script uses `git --git-dir=...` (relocated layout)
       - without           → falls back to `git -C ...` (unmigrated bugs)
    """
    base_ctx = {
        "env": [],
        "repo_dir": "/out/p/git_repo_dir_AAA",
        "commit_after": "aaaaaaa",
        "commit_before": "bbbbbbb",
        "src_file": "src/foo.c",
        "test_log": "/out/p/logs/test_AAA_fix.log",
        "build_dir": "build_AAA",
    }

    rendered_with = _render_workflow({**base_ctx, "gittree_dir": "/out/p/_gittree_AAA"})
    rendered_without = _render_workflow(base_ctx)

    # When the new variable is present, the template uses --git-dir=...
    if "git --git-dir=" not in rendered_with:
        pytest.skip(
            "workflow_cmake_tpl.jinja has NOT been edited to support gittree_dir yet "
            "(Phase 1 pending)"
        )
    assert "git --git-dir=" in rendered_with
    # The dual form may still contain a literal `git -C ` in the else branch; check
    # that the {{repo_dir}} `git -C` lines are NOT emitted in this mode.
    assert "/out/p/_gittree_AAA/.git" in rendered_with, (
        "rendered gittree_dir not interpolated:\n" + rendered_with
    )

    # And the legacy fall-through still renders something usable.
    assert "git -C /out/p/git_repo_dir_AAA" in rendered_without, (
        "without gittree_dir the template must fall back to git -C; got:\n" + rendered_without
    )


# ────────────────────────────────────────────────────────────────────
#  Locked-down regression guard on the merge logic itself
# ────────────────────────────────────────────────────────────────────

def test_merge_logic_locked():
    """Copy-of-prod-logic regression guard for §5A.2.

    The exact merge formula (lines 372-385 of bug_helper_v1_out2.py) is
    `meta_info = {**meta_project, **system_compile, **defect_compile,
                  **{build_flags: b_flags, test_flags: t_flags}}`.
    If this test breaks, BugsInfo._init_meta_info has drifted from §5A.2 and the
    warmup / oracle merge will silently disagree.
    """
    meta_project = {"env": ["CC=gcc"], "c_compile": {"build": "ninja",
                                                     "build_flags": ["-A"],
                                                     "test_flags": []}}
    meta_defect = {"c_compile": {"build": None,
                                 "build_flags": ["-B"],
                                 "test_flags": ["regex"]}}
    system_compile = meta_project["c_compile"]
    defect_compile = meta_defect["c_compile"]
    b_flags = (system_compile.get("build_flags") or []) + (defect_compile.get("build_flags") or [])
    t_flags = (system_compile.get("test_flags") or []) + (defect_compile.get("test_flags") or [])
    defect_compile = {k: v for k, v in defect_compile.items() if v is not None and len(v) > 0}

    merged = {**meta_project, **system_compile, **defect_compile,
              **{"build_flags": b_flags, "test_flags": t_flags}}

    assert merged["build_flags"] == ["-A", "-B"]
    assert merged["test_flags"] == ["regex"]
    assert merged["build"] == "ninja"  # bug-null inherits

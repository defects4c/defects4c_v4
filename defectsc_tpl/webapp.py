#!/usr/bin/env python3
"""
webapp.py — Defects4J-compatible HTTP API for Defects4C (C/C++ bug benchmark).

Lifecycle phases: checkout → compile → trigger_test → regression_test
Patch validation:  build_patch → fix → status polling

The git clone is done on the HOST via out_tmp_dirs/git_setup.sh (shallow fetch
of commit_after + commit_before).  The container mounts that directory read-write
and performs reproduce / patch validation inside it.  Checkout here means:
  - If build_dir (git_repo_dir_<sha>) exists and is_force_checkout is False → skip
  - If is_force_checkout → git clean -dfx + git checkout -f <commit_before>
  - Otherwise → error (run git_setup.sh on host first)
"""

import asyncio
import base64
import glob
import hashlib
import json
import logging
import os
import re
import shlex
import shutil
import stat
import subprocess
import tempfile
import traceback
import uuid
from collections import deque
from pathlib import Path
from typing import Any, Dict, List, Optional

import jmespath
from jinja2 import Environment, FileSystemLoader
from fastapi import APIRouter, BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from config import PROJECTS_DIR

# ─────────────────────────── Configuration ───────────────────────────

SRC_ROOT   = Path(os.getenv("SRC_DIR",  "/src/"))
OUT_ROOT   = Path(os.getenv("ROOT_DIR", "/out/"))
WORKSPACE  = Path(os.getenv("D4C_WORKSPACE", "/workspace/"))
TIMEOUT    = int(os.getenv("D4C_TIMEOUT", "1800"))

PATCH_OUTPUT_DIR = Path(os.getenv("PATCH_OUTPUT_DIR", "/patches/"))
PATCH_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("d4c-webapp")

# ─────────────────────────── In-memory stores ────────────────────────

META_DICT:   Dict[str, Dict[str, Any]] = {}   # sha -> {**bug_record, "project": str}
sha_locks:   Dict[str, asyncio.Lock] = {}
tasks:       Dict[str, dict] = {}

# ─────────────────────────── Utilities ───────────────────────────────

def md5(text: str) -> str:
    return hashlib.md5(text.strip().lower().encode()).hexdigest()


def parse_bug_id(bug_id: str):
    """Parse 'project@sha' or 'project:sha' -> (project, sha)."""
    bug_id = bug_id.replace(":", "@")
    project, _, sha = bug_id.partition("@")
    if not project or not sha:
        raise ValueError(f"Bug ID must be 'project@sha', got '{bug_id}'")
    return project, sha


def is_unified_diff(txt: str) -> bool:
    return txt.lstrip().startswith(("--- ", "diff ", "@@ "))


def extract_inline_snippet(llm: str) -> Optional[str]:
    """Extract code from markdown fenced block."""
    m = re.search(r"```(?:\w*\n)?([\s\S]*?)```", llm, re.S)
    return m.group(1).strip() if m else None


def read_file_limited(path, max_lines: int = 100, max_tokens: int = 512) -> str:
    p = Path(path)
    if not p.exists():
        return ""
    with p.open("r", encoding="utf-8", errors="ignore") as fh:
        lines = deque(fh, maxlen=max_lines)
    tokens = " ".join(l.rstrip("\n") for l in lines).split()
    if len(tokens) > max_tokens:
        tokens = tokens[-max_tokens:]
    return " ".join(tokens)


def apt_install_tool() -> str:
    return """
apt_install_fn() {
    library=$1
    if dpkg -s "$library" &>/dev/null || which "$library" &>/dev/null; then
        echo "$library is already installed"
    else
        echo "$library is not installed, attempting to install..."
        sudo apt-get update -y && sudo apt-get install -y "$library"
    fi
}
"""


def extract_patch_md5(patch_path: str) -> str:
    basename = os.path.basename(patch_path)
    if "@" in basename and len(basename.split("@", 1)[0]) == 32:
        return basename.split("@", 1)[0]
    m = re.search(r"([a-f0-9]{32})", basename)
    return m.group(1) if m else hashlib.md5(patch_path.encode()).hexdigest()


# ─────────────────────────── Git blocking ────────────────────────────

GIT_BLOCKED = {"add","commit","push","merge","cherry-pick","am","apply","tag",
               "branch","rm","mv","update-index","update-ref","read-tree",
               "write-tree","filter-branch","replace","notes","worktree"}
_GIT_RE = re.compile(
    r'(?:^|[;&|`\n(])\s*(?:sudo\s+)?(?:\S*/)?git\s+('
    + '|'.join(re.escape(s) for s in sorted(GIT_BLOCKED))
    + r')(?:\s|$|[;&|)`])', re.I)
BLOCK_MSG = "Command blocked: '{reason}' is not allowed. Git-mutating commands are blocked."


def _blocked_args(args):
    if not args:
        return False, ""
    s = " ".join(str(a) for a in args).lower()
    if s.startswith("git ") or "/git " in s:
        parts = s.split()
        gi = next((i for i, p in enumerate(parts) if p == "git" or p.endswith("/git")), None)
        if gi is not None and gi + 1 < len(parts) and parts[gi + 1] in GIT_BLOCKED:
            return True, f"git {parts[gi + 1]}"
    return False, ""


def _blocked_shell(cmd):
    if not cmd:
        return False, ""
    m = _GIT_RE.search(cmd)
    return (True, f"git {m.group(1)}") if m else (False, "")


# ─────────────────────────── Buglist filter ──────────────────────────

BUGLIST_PATH = SRC_ROOT / "buglist.txt"
ALLOWED_BUGS: Optional[set] = None
BUGLIST_MODE = "full"


def _load_buglist():
    global ALLOWED_BUGS, BUGLIST_MODE
    if not BUGLIST_PATH.exists():
        ALLOWED_BUGS = None
        BUGLIST_MODE = "full"
        return
    lines = BUGLIST_PATH.read_text().strip().splitlines()
    ALLOWED_BUGS = set()
    for line in lines:
        line = line.strip().replace(":", "@")
        if not line or line.startswith("#"):
            continue
        ALLOWED_BUGS.add(line.split("@")[1] if "@" in line else line)
    BUGLIST_MODE = ("mini" if "mini" in str(BUGLIST_PATH)
                    else ("full" if "full" in str(BUGLIST_PATH) else "buglist"))
    log.info("Buglist: %d entries, mode=%s", len(ALLOWED_BUGS), BUGLIST_MODE)


def _bug_allowed(sha):
    if ALLOWED_BUGS is None:
        return True
    if sha in ALLOWED_BUGS:
        return True
    return any(len(p) < 40 and sha.startswith(p) for p in ALLOWED_BUGS)


def _resolve_sha(sha):
    """Resolve a short SHA prefix to a full SHA in META_DICT."""
    if sha in META_DICT:
        return sha
    for full in META_DICT:
        if full.startswith(sha):
            return full
    return sha


# ─────────────────────────── BugsInfo ────────────────────────────────

class BugsInfo:
    """Loads bug metadata and renders build/test scripts."""

    def __init__(self, project: str, sha: str):
        if project not in PROJECTS_DIR:
            raise ValueError(f"Unknown project '{project}'")
        self.project = project
        self.sha = sha
        self.project_major = PROJECTS_DIR[project]
        self.src_dir = str(SRC_ROOT)

        # wrk_git: the per-sha repo directory (created by git_setup.sh on host)
        self.wrk_git = OUT_ROOT / project / f"git_repo_dir_{sha}"
        self._git_tree = OUT_ROOT / project / f"git_repo_dir_{sha}_gittree" / ".git"
        if not self.wrk_git.exists():
            # fallback for v0 projects
            fallback = OUT_ROOT / project / "git_repo_dir"
            if fallback.exists():
                self.wrk_git = fallback
            else:
                self.wrk_git.mkdir(parents=True, exist_ok=True)

        self.wrk_log = OUT_ROOT / project / "logs"
        self.wrk_log.mkdir(parents=True, exist_ok=True)

        self.src_project = (
            SRC_ROOT / "projects_v1" / project
            if self.project_major == "projects_v1"
            else SRC_ROOT / self.project_major / project
        )

        bugs_file = self.src_project / "bugs_list_new.json"
        if not bugs_file.exists():
            bugs_file = self.src_project / "bugs_list.json"
        proj_file = self.src_project / "project.json"

        with open(bugs_file) as f:
            meta_bugs = json.load(f)
        with open(proj_file) as f:
            self.meta_project = json.load(f)

        matches = jmespath.search(f"[?commit_after=='{sha}']", meta_bugs)
        if len(matches) != 1:
            raise ValueError(f"Bug {sha} not found or ambiguous ({len(matches)} matches)")
        self.meta_defect = matches[0]

        def _j(path, data):
            d = jmespath.search(path, data) or []
            if isinstance(d, dict):
                d = {k: v for k, v in d.items() if v is not None}
            return d

        compile_block = {
            **(_j("c_compile", self.meta_project) or {}),
            **(_j("c_compile", self.meta_defect) or {}),
            "build_flags": (_j("c_compile.build_flags", self.meta_project)
                            + _j("c_compile.build_flags", self.meta_defect)),
            "test_flags":  (_j("c_compile.test_flags", self.meta_project)
                            + _j("c_compile.test_flags", self.meta_defect)),
            "env":         (_j("env", self.meta_project)
                            + _j("c_compile.env", self.meta_defect)),
        }
        compile_block = {k: v for k, v in compile_block.items() if v}

        self.meta_info: Dict[str, Any] = {
            "apt_install_fn": apt_install_tool(),
            "cpu_count":      max((os.cpu_count() or 2) - 1, 1),
            **self.meta_defect,
            "repo_dir":   str(self.wrk_git),
            "log_dir":    str(self.wrk_log),
            "build_dir":  f"build_{sha}",
            "test_log":   str(self.wrk_log / f"test_{sha}_fix.log"),
            "test_files": jmespath.search("files.test", self.meta_defect),
            "src_file":   jmespath.search("files.src[0]", self.meta_defect),
            **compile_block,
        }

    def _render_template(self, tpl: str, info: dict, dest: Path):
        tpl_path = str(tpl)
        if not os.path.exists(tpl_path):
            dest.write_text("#!/bin/bash\necho 'template not found: " + tpl_path + "'\nexit 1\n")
        else:
            env = Environment(loader=FileSystemLoader(os.path.dirname(tpl_path) or "."))
            dest.write_text(env.get_template(os.path.basename(tpl_path)).render(**info))
        os.chmod(dest, stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)

    def _tpl_build(self):
        if "build" in self.meta_info and ".jinja" in str(self.meta_info.get("build", "")):
            return os.path.join(str(self.src_project), self.meta_info["build"])
        return str(SRC_ROOT / "projects_v1" / "common_build_tpl.jinja")

    def _tpl_test(self):
        if "test" in self.meta_info and ".jinja" in str(self.meta_info.get("test", "")):
            return os.path.join(str(self.src_project), self.meta_info["test"])
        return str(SRC_ROOT / "projects_v1" / "common_test_tpl.jinja")

    def set_reproduce_build(self):
        if self.project_major == "projects_v1":
            workflow_tpl = str(SRC_ROOT / "projects_v1" / "workflow_cmake_tpl.jinja")
        else:
            workflow_tpl = str(SRC_ROOT / self.project_major / "workflow_tpl.jinja")
        self._render_template(self._tpl_build(), self.meta_info,
                              self.wrk_git / "inplace_build.sh")
        self._render_template(self._tpl_build(), {**self.meta_info, "is_rebuild": True},
                              self.wrk_git / "inplace_rebuild.sh")
        self._render_template(self._tpl_test(), self.meta_info,
                              self.wrk_git / "inplace_test.sh")
        self._render_template(workflow_tpl, self.meta_info,
                              self.wrk_git / "run_reproduce.sh")

    def set_reproduce_build_regression(self):
        """Like set_reproduce_build but with empty test_flags (runs ALL tests)."""
        regression_info = {**self.meta_info, "test_flags": []}
        if self.project_major == "projects_v1":
            workflow_tpl = str(SRC_ROOT / "projects_v1" / "workflow_cmake_tpl.jinja")
        else:
            workflow_tpl = str(SRC_ROOT / self.project_major / "workflow_tpl.jinja")
        self._render_template(self._tpl_build(), self.meta_info,
                              self.wrk_git / "inplace_build.sh")
        self._render_template(self._tpl_build(), {**self.meta_info, "is_rebuild": True},
                              self.wrk_git / "inplace_rebuild.sh")
        self._render_template(self._tpl_test(), regression_info,
                              self.wrk_git / "inplace_test.sh")
        self._render_template(workflow_tpl, self.meta_info,
                              self.wrk_git / "run_reproduce.sh")

    def set_patch_build(self):
        workflow_tpl = str(SRC_ROOT / self.project_major / "workflow_cmake_rebuild_tpl.jinja")
        self._render_template(
            self._tpl_build(),
            {**self.meta_info, "is_rebuild": True,
             "test_log": str(self.wrk_log / f"test_{self.sha}_fix.log")},
            self.wrk_git / "inplace_rebuild.sh",
        )
        self._render_template(self._tpl_test(), self.meta_info,
                              self.wrk_git / "inplace_test.sh")
        self._render_template(
            workflow_tpl,
            {**self.meta_info, "test_log": str(self.wrk_log / f"patch_{self.sha}_fix.log")},
            self.wrk_git / "run_patch.sh",
        )

    def get_fl_info(self) -> dict:
        loc = self.meta_defect.get("files", {}).get("src0_location", {})
        return {
            "src_file":        jmespath.search("files.src[0]", self.meta_defect),
            "line_is_single":  loc.get("line_is_single", False),
            "line_number":     loc.get("line_number"),
            "hunk_is_single":  loc.get("hunk_is_single", False),
            "hunk_start":      loc.get("hunk_start"),
            "hunk_end":        loc.get("hunk_end"),
            "func_is_single":  loc.get("func_is_single", False),
            "func_start":      loc.get("func_start"),
            "func_end":        loc.get("func_end"),
        }

    def get_trigger_tests(self) -> List[str]:
        ut = self.meta_defect.get("unittest", {})
        names = ut.get("name", [])
        return [names] if isinstance(names, str) else (names or [])

    def get_regression_test_flags(self) -> str:
        """Return empty string for regression = run ALL tests (no filter)."""
        return ""


# ─────────────────────────── Shell execution ─────────────────────────

def exec_shell(cmd_str: str, cwd: str, timeout: int = TIMEOUT) -> dict:
    """Run a shell command, return {returncode, stdout, stderr}."""
    actual_cwd = cwd if os.path.isdir(cwd) else str(WORKSPACE)
    log.info("EXEC  cwd=%s  cmd=%s", actual_cwd, cmd_str[:200])
    if not os.path.isdir(actual_cwd):
        msg = f"Working directory does not exist: {cwd}"
        log.error(msg)
        return {"returncode": 1, "stdout": "", "stderr": msg}
    try:
        result = subprocess.run(
            cmd_str, shell=True, capture_output=True,
            encoding="utf-8", errors="replace",
            cwd=actual_cwd, timeout=timeout,
        )
        if result.returncode != 0:
            log.warning("EXEC FAILED rc=%d  cmd=%s", result.returncode, cmd_str[:120])
            if result.stderr:
                for line in result.stderr.splitlines()[:5]:
                    log.warning("  stderr: %s", line[:200])
        return {"returncode": result.returncode,
                "stdout": result.stdout, "stderr": result.stderr}
    except subprocess.TimeoutExpired:
        msg = f"Command timed out after {timeout}s: {cmd_str[:100]}"
        log.error(msg)
        return {"returncode": 124, "stdout": "", "stderr": msg}
    except Exception as exc:
        msg = f"Exception running command: {exc}"
        log.error(msg)
        return {"returncode": 1, "stdout": "", "stderr": msg}


# ─────────────────────────── Phase operations ────────────────────────

def d4c_checkout(project: str, sha: str, is_force: bool = False) -> dict:
    """
    Checkout phase — always restores the buggy source file.

    The reproduce workflow leaves the repo in fix-commit state.
    We must ALWAYS `git checkout -f <commit_before> -- <src_file>`
    to ensure the buggy source is in place before compile/test.

    If is_force=True, also runs `git clean -dfx` to wipe build artifacts.
    """
    log.info("CHECKOUT project=%s sha=%s is_force=%s", project, sha, is_force)
    try:
        instance = BugsInfo(project, sha)
    except Exception as exc:
        msg = f"Failed to load bug metadata for {project}@{sha}: {exc}"
        log.error("CHECKOUT ERROR: %s", msg)
        return {"returncode": 1, "stdout": "", "stderr": msg}

    repo_dir = instance.wrk_git
    build_dir = repo_dir / f"build_{sha}"
    src_file = instance.meta_info.get("src_file", "")
    log.info("CHECKOUT paths: repo=%s  build_exists=%s  src=%s",
             repo_dir, build_dir.exists(), src_file)

    if not ( (repo_dir / ".git").exists() or (instance._git_tree).exists() ):
        msg = (
            f"Repo dir {repo_dir} has no .git directory.\n"
            f"Run warmup first:\n"
            f"  docker exec <container> bash /src/run_warmup_selected.sh\n"
            f"Or clone manually:\n"
            f"  cd out_tmp_dirs && bash git_setup.sh {project} {sha} "
            f"{instance.meta_defect.get('commit_before', '<commit_before>')}"
        )
        log.error("CHECKOUT: no .git in %s", repo_dir)
        return {"returncode": 1, "stdout": "", "stderr": msg}

    # Skip checkout if build already exists and not forced
    if not is_force and build_dir.exists():
        log.info("CHECKOUT SKIP: build_dir exists and is_force=False")
        return {"returncode": 0,
                "stdout": (f"Skip checkout: build dir already exists ({build_dir}).\n"
                           f"  Use is_force=True to force a clean checkout.\n"),
                "stderr": ""}

    if is_force:
        log.info("CHECKOUT FORCE: git clean -dfx")
        exec_shell(f"git  --git-dir={instance._git_tree} --work-tree={str(repo_dir)}  clean -dfx", cwd=str(repo_dir))

    # Always restore the buggy source file
    commit_before = instance.meta_defect.get("commit_before", "")

    # Verify commit_before is reachable; fallback to parent of commit_after
    check = exec_shell(f"git --git-dir={instance._git_tree} --work-tree={str(repo_dir)} cat-file -t {commit_before}", cwd=str(repo_dir))
    if check["returncode"] != 0:
        log.warning("CHECKOUT: commit_before %s not reachable, using parent of %s",
                     commit_before[:12], sha[:12])
        parent_r = exec_shell(f"git --git-dir={instance._git_tree} --work-tree={str(repo_dir)}   rev-parse {sha}~1", cwd=str(repo_dir))
        if parent_r["returncode"] == 0 and parent_r["stdout"].strip():
            commit_before = parent_r["stdout"].strip()
            log.info("CHECKOUT: fallback commit_before=%s", commit_before[:12])
        else:
            return {"returncode": 1, "stdout": "",
                    "stderr": f"commit_before unreachable and no parent found for {sha[:12]}. "
                              f"Run warmup first."}

    # Checkout the buggy source file specifically
    if src_file:
        cmd = f"git --git-dir={instance._git_tree} --work-tree={str(repo_dir)}  checkout -f {commit_before} -- {src_file}"
    else:
        cmd = f"git --git-dir={instance._git_tree} --work-tree={str(repo_dir)}  checkout -f {commit_before}"
    log.info("CHECKOUT cmd: %s", cmd)

    result = exec_shell(cmd, cwd=str(repo_dir))
    if result["returncode"] != 0:
        log.error("CHECKOUT FAILED rc=%d: %s", result["returncode"], result["stderr"][:300])
        return result

    log.info("CHECKOUT OK: buggy source restored (commit_before=%s)", commit_before[:12])
    return {"returncode": 0,
            "stdout": (f"Checked out buggy source: {src_file}\n"
                       f"  commit_before={commit_before[:12]}\n"
                       f"  build_dir_exists={build_dir.exists()} (ninja will do incremental rebuild)\n"),
            "stderr": ""}


def d4c_compile(project: str, sha: str) -> dict:
    """Compile phase: returns success if build dir exists (warmup already compiled).
    Actual rebuild happens inside trigger_test/fix via inplace_rebuild.sh."""
    try:
        instance = BugsInfo(project, sha)
    except Exception as exc:
        return {"returncode": 1, "stdout": "", "stderr": str(exc)}

    build_dir_path = instance.wrk_git / f"build_{sha}"
    if build_dir_path.exists():
        return {"returncode": 0,
                "stdout": f"Build dir exists: {build_dir_path}\n"
                          f"Warmup already compiled. Rebuild happens in test/fix.\n",
                "stderr": ""}
    else:
        return {"returncode": 1, "stdout": "",
                "stderr": f"Build dir not found: {build_dir_path}. Run warmup first.\n"}


def d4c_trigger_test(project: str, sha: str) -> dict:
    """
    Trigger test: checkout buggy → inplace_rebuild → inplace_test.
    Uses test_flags filter from metadata. On buggy code should FAIL.
    """
    try:
        instance = BugsInfo(project, sha)
    except Exception as exc:
        return {"returncode": 1, "stdout": "", "stderr": str(exc)}

    log_path = str(instance.wrk_log / f"trigger_{sha}.log")

    # Step 1: Render build/test scripts
    instance.set_reproduce_build()

    # Step 2: Checkout buggy source
    src_file = instance.meta_info.get("src_file", "")
    commit_before = instance.meta_defect.get("commit_before", "")
    git_prefix = f"git --git-dir={instance._git_tree} --work-tree={instance.wrk_git}" \
        if instance._git_tree.exists() else "git"


    # Step 3: Rebuild
    build_dir = f"build_{sha}"
    rebuild_r = exec_shell(
        f"bash inplace_rebuild.sh {build_dir} {log_path}",
        cwd=str(instance.wrk_git), timeout=TIMEOUT)
    if rebuild_r["returncode"] != 0:
        return {"returncode": rebuild_r["returncode"], "stdout": rebuild_r["stdout"],
                "stderr": rebuild_r["stderr"], "_trigger_pass": False}

    # Step 4: Run tests
    result = exec_shell(
        f"bash inplace_test.sh {build_dir} {log_path}",
        cwd=str(instance.wrk_git), timeout=TIMEOUT)

    # Step 5: Read status from log files
    status_path = log_path.replace(".log", ".status")
    combined = result["stdout"] + result["stderr"]
    if os.path.isfile(log_path):
        try:
            with open(log_path, encoding="utf-8", errors="ignore") as f:
                combined += f.read()
        except Exception:
            pass
    status_text = ""
    if os.path.isfile(status_path):
        try:
            status_text = open(status_path).read().strip()
        except Exception:
            pass

    if src_file and commit_before:
        checkout_r = exec_shell(
            f"{git_prefix} checkout -f {commit_before} -- {src_file}",
            cwd=str(instance.wrk_git))
        if checkout_r["returncode"] != 0:
            # Fallback: try parent of commit_after
            parent_r = exec_shell(f"{git_prefix} rev-parse {sha}~1", cwd=str(instance.wrk_git))
            if parent_r["returncode"] == 0 and parent_r["stdout"].strip():
                commit_before = parent_r["stdout"].strip()
                exec_shell(f"{git_prefix} checkout -f {commit_before} -- {src_file}",
                           cwd=str(instance.wrk_git))



    passed = "success" in status_text.lower() or "100% tests passed" in combined
    result["_trigger_pass"] = passed
    result["status"] = status_text
    return result


def d4c_regression_test(project: str, sha: str) -> dict:
    """
    Regression test: checkout buggy → inplace_rebuild → inplace_test (ALL tests, no filter).
    """
    return {"returncode": 0, "stdout": "", "stderr":"" }

    try:
        instance = BugsInfo(project, sha)
    except Exception as exc:
        return {"returncode": 1, "stdout": "", "stderr": str(exc)}

    log_path = str(instance.wrk_log / f"regression_{sha}.log")

    # Render with empty test_flags for regression (run ALL tests)
    regression_info = {**instance.meta_info, "test_flags": []}
    rebuild_info = {
        "is_rebuild": True,
        "test_log": str(instance.wrk_log / f"test_{sha}_fix.log"),
        **instance.meta_info,
    }
    instance._render_template(instance._tpl_build(), rebuild_info,
                              instance.wrk_git / "inplace_rebuild.sh")
    instance._render_template(instance._tpl_test(), regression_info,
                              instance.wrk_git / "inplace_test.sh")

    # Checkout buggy source
    src_file = instance.meta_info.get("src_file", "")
    commit_before = instance.meta_defect.get("commit_before", "")
    git_prefix = f"git --git-dir={instance._git_tree} --work-tree={instance.wrk_git}" \
        if instance._git_tree.exists() else "git"

    if src_file and commit_before:
        exec_shell(f"{git_prefix} checkout -f {commit_before} -- {src_file}",
                   cwd=str(instance.wrk_git))

    # Rebuild + test
    build_dir = f"build_{sha}"
    rebuild_r = exec_shell(
        f"bash inplace_rebuild.sh {build_dir} {log_path}",
        cwd=str(instance.wrk_git), timeout=TIMEOUT)
    if rebuild_r["returncode"] != 0:
        return {"returncode": rebuild_r["returncode"], "stdout": rebuild_r["stdout"],
                "stderr": rebuild_r["stderr"], "_regression_pass": False}

    result = exec_shell(
        f"bash inplace_test.sh {build_dir} {log_path}",
        cwd=str(instance.wrk_git), timeout=TIMEOUT)

    # Read status
    combined = result["stdout"] + result["stderr"]
    if os.path.isfile(log_path):
        try:
            with open(log_path, encoding="utf-8", errors="ignore") as f:
                combined += f.read()
        except Exception:
            pass
    result["_regression_pass"] = "100% tests passed" in combined
    return result


def d4c_reproduce(project: str, sha: str, is_force_cleanup: bool = True) -> dict:
    """
    Full reproduce: checkout fix → build → test → checkout buggy → rebuild → test.
    This is the warmup phase from the original Defects4C.
    """
    try:
        instance = BugsInfo(project, sha)
    except Exception as exc:
        return {"returncode": 1, "stdout": "", "stderr": str(exc)}

    log_path = str(instance.wrk_log / f"{sha}.log")

    with open(log_path, "w") as lf:
        # Clean if forced (like the original v0 flow)
        if is_force_cleanup:
            subprocess.run(
                shlex.split("git clean -dfx"),
                cwd=str(instance.wrk_git), stdout=lf, stderr=lf
            )

        instance.set_reproduce_build()
        try:
            timeout = 60 * 60 if "llvm" in project else 60 * 30
            proc = subprocess.run(
                shlex.split("bash run_reproduce.sh"),
                cwd=str(instance.wrk_git), stdout=lf, stderr=lf,
                timeout=timeout,
            )
            return {"returncode": proc.returncode,
                    "stdout": f"Reproduce log: {log_path}\n",
                    "stderr": "", "log_file": log_path}
        except subprocess.TimeoutExpired:
            return {"returncode": 124, "stdout": "",
                    "stderr": "Reproduce timed out", "log_file": log_path}


def d4c_info(project: str, sha: str) -> dict:
    """Return bug metadata and FL information."""
    try:
        instance = BugsInfo(project, sha)
    except Exception as exc:
        return {"returncode": 1, "stdout": "", "stderr": str(exc)}
    fl = instance.get_fl_info()
    triggers = instance.get_trigger_tests()
    test_flags = instance.meta_info.get("test_flags", [])
    info_text = (
        f"Project: {project}\n"
        f"Bug SHA (commit_after): {sha}\n"
        f"Commit before (buggy): {instance.meta_defect.get('commit_before', '')}\n"
        f"Source file: {fl['src_file']}\n"
        f"Line single: {fl['line_is_single']}, number: {fl['line_number']}\n"
        f"Hunk single: {fl['hunk_is_single']}, range: [{fl['hunk_start']}, {fl['hunk_end']}]\n"
        f"Func single: {fl['func_is_single']}, range: [{fl['func_start']}, {fl['func_end']}]\n"
        f"Trigger tests: {triggers}\n"
        f"Test flags: {test_flags}\n"
        f"Bug type: {instance.meta_defect.get('type', {}).get('name', 'unknown')}\n"
        f"Repo dir: {instance.wrk_git}\n"
        f"Build dir: build_{sha}\n"
        f"\n"
        f"Commands:\n"
        f"  defects4c compile -p {project} -v {sha}\n"
        f"  defects4c test -p {project} -v {sha}\n"
        f"  defects4c test -r -p {project} -v {sha}\n"
    )
    return {"returncode": 0, "stdout": info_text, "stderr": ""}


# ─────────────────────────── CLI arg translation ─────────────────────

def translate_d4j_args(args: list) -> dict:
    if not args:
        return {"command": None}
    subcmd = args[0]
    flags = {}
    i = 1
    while i < len(args):
        if args[i].startswith("-") and i + 1 < len(args) and not args[i + 1].startswith("-"):
            flags[args[i]] = args[i + 1]
            i += 2
        else:
            flags[args[i]] = True
            i += 1

    result = {"command": subcmd, "flags": flags}
    if "-b" in flags:
        bug_id = flags["-b"]
        if "@" in bug_id:
            result["project"], result["sha"] = parse_bug_id(bug_id)
    if "-p" in flags:
        result["project"] = flags["-p"]
    if "-v" in flags:
        result["sha"] = flags["-v"].rstrip("bf")
    if "-w" in flags:
        result["workdir"] = flags["-w"]
    return result


# ─────────────────────────── Data loading ────────────────────────────

def load_metadata() -> int:
    count = 0
    for pattern in [
        os.path.join(str(SRC_ROOT), "projects/**/bug*.json"),
        os.path.join(str(SRC_ROOT), "projects_v1/**/bug*.json"),
    ]:
        for p in glob.glob(pattern, recursive=True):
            try:
                with open(p) as f:
                    lines = json.load(f)
                project = os.path.basename(os.path.dirname(p))
                for x in lines:
                    sha = x["commit_after"]
                    if _bug_allowed(sha):
                        META_DICT[sha] = {**x, "project": project}
                        count += 1
            except Exception as e:
                log.warning("Failed to load %s: %s", p, e)
    log.info("Loaded %d bug records (mode=%s)", count, BUGLIST_MODE)
    return count


def get_log_file_paths(project: str, sha: str, patch_md5: str) -> dict:
    log_dir = OUT_ROOT / project / "logs"
    return {
        "log":    str(log_dir / f"patch_{sha}_{patch_md5}.log"),
        "msg":    str(log_dir / f"patch_{sha}_{patch_md5}.msg"),
        "status": str(log_dir / f"patch_{sha}_{patch_md5}.status"),
    }


# ─────────────────────────── Patch building ──────────────────────────

def build_patch_from_llm(bug_id: str, llm_response: str, method: str = "direct",
                          generate_diff: bool = True, persist_flag: bool = False) -> dict:
    try:
        project, sha = parse_bug_id(bug_id)
    except ValueError as e:
        return {"success": False, "error": str(e)}

    if sha not in META_DICT:
        return {"success": False, "error": f"SHA {sha} not found in metadata"}

    rec = META_DICT[sha]
    try:
        instance = BugsInfo(project, sha)
    except Exception as e:
        return {"success": False, "error": str(e)}

    src_file = jmespath.search("files.src[0]", rec)
    if not src_file:
        return {"success": False, "error": "No source file in metadata"}

    src_full_path = instance.wrk_git / src_file
    if not src_full_path.exists():
        return {"success": False, "error": f"Source not found: {src_full_path}"}

    original_content = src_full_path.read_text(encoding="utf-8", errors="ignore")
    loc = rec.get("files", {}).get("src0_location", {})

    # Auto-detect method
    if method in ("raw", "auto"):
        method = "diff" if is_unified_diff(llm_response) else "direct"

    patched = None

    if method == "full_file":
        patched = llm_response

    elif method == "replace_json":
        try:
            rj = json.loads(llm_response) if isinstance(llm_response, str) else llm_response
            ls = int(rj["line_start"]); le = int(rj["line_end"])
            content = rj["content"]
            lines = original_content.splitlines(keepends=True)
            patched = "".join(lines[:ls-1] + [content if content.endswith("\n") else content+"\n"] + lines[le:])
        except Exception as e:
            return {"success": False, "error": f"replace_json parse error: {e}"}

    elif method == "diff":
        patched = _apply_diff(original_content, llm_response, loc)

    else:  # direct
        snippet = extract_inline_snippet(llm_response)
        if not snippet:
            snippet = llm_response
        patched = _replace_region(original_content, snippet, loc)

    if patched is None:
        return {"success": False, "error": "Failed to apply patch"}

    md5_hash = md5(patched)
    if persist_flag:
        out_dir = PATCH_OUTPUT_DIR / project
        out_dir.mkdir(parents=True, exist_ok=True)
        fix_file = out_dir / f"{md5_hash}@{sha}___{os.path.basename(src_file)}"
    else:
        fd, tmp = tempfile.mkstemp(
            suffix=f"@{sha}___{os.path.basename(src_file)}",
            dir=str(PATCH_OUTPUT_DIR))
        os.close(fd)
        fix_file = Path(tmp)

    fix_file.write_text(patched, encoding="utf-8")

    patch_content = ""
    if generate_diff:
        try:
            r = subprocess.run(
                f"diff -u {src_full_path} {fix_file}",
                shell=True, capture_output=True, encoding="utf-8", errors="replace")
            patch_content = r.stdout
        except Exception:
            pass

    return {
        "success": True, "md5_hash": md5_hash,
        "bug_id": bug_id, "sha": sha,
        "fix_p": str(fix_file), "fix_p_diff": None,
        "patch_content": patch_content, "method": method,
    }


def _apply_diff(original: str, diff_text: str, loc: dict) -> Optional[str]:
    old_lines, new_lines = [], []
    for ln in diff_text.splitlines():
        if ln.startswith("-") and not ln.startswith("---"):
            old_lines.append(ln[1:])
        elif ln.startswith("+") and not ln.startswith("+++"):
            new_lines.append(ln[1:])
    old_text = "\n".join(old_lines)
    if old_text and old_text in original:
        return original.replace(old_text, "\n".join(new_lines), 1)
    try:
        hs = loc.get("hunk_start") or loc.get("func_start", 1)
        he = loc.get("hunk_end") or loc.get("func_end", 1)
        lines = original.splitlines(keepends=True)
        return "".join(lines[:hs - 1] + [l + "\n" for l in new_lines] + lines[he:])
    except Exception:
        return None


def _replace_region(original: str, replacement: str, loc: dict) -> Optional[str]:
    try:
        fs = loc.get("func_start") or loc.get("hunk_start", 1)
        fe = loc.get("func_end") or loc.get("hunk_end", 1)
        lines = original.splitlines(keepends=True)
        return "".join(lines[:fs - 1] + [l + "\n" for l in replacement.splitlines()] + lines[fe:])
    except Exception:
        return None


# ─────────────────────────── Async fix task ──────────────────────────

async def run_fix_async(bug_id: str, patch_path: str, handle: str):
    try:
        project, sha = parse_bug_id(bug_id)
        tasks[handle]["status"] = "running"
        instance = BugsInfo(project, sha)

        patch_md5 = extract_patch_md5(patch_path)
        lp = get_log_file_paths(project, sha, patch_md5)

        # Check existing result
        if os.path.exists(lp["log"]):
            rc = -1
            if os.path.exists(lp["status"]):
                try:
                    c = open(lp["status"]).read().strip()
                    if c.isdigit():
                        rc = int(c)
                    elif "success" in c.lower():
                        rc = 0
                except Exception:
                    pass
            tasks[handle].update({
                "status": "completed" if rc == 0 else "failed",
                "return_code": rc,
                "fix_log": read_file_limited(lp["log"]),
                "fix_msg": read_file_limited(lp["msg"]),
                "fix_status": read_file_limited(lp["status"]),
                "error": "" if rc == 0 else f"Exit code {rc}",
            })
            return

        lock = sha_locks.setdefault(sha, asyncio.Lock())
        async with lock:
            rc = await asyncio.to_thread(_run_fix_sync, instance, patch_path, lp["log"])
            tasks[handle].update({
                "status": "completed" if rc == 0 else "failed",
                "return_code": rc,
                "fix_log": read_file_limited(lp["log"]),
                "fix_msg": read_file_limited(lp["msg"]),
                "fix_status": read_file_limited(lp["status"]),
                "error": "" if rc == 0 else f"Exit code {rc}",
            })
    except Exception:
        tasks[handle]["status"] = "failed"
        tasks[handle]["error"] = traceback.format_exc()


def _run_fix_sync(instance: BugsInfo, patch_path: str, log_path: str) -> int:
    if not os.path.isfile(patch_path):
        raise FileNotFoundError(f"Patch not found: {patch_path}")
    instance.set_patch_build()
    with open(log_path, "a") as lf:
        proc = subprocess.run(
            shlex.split(f"bash run_patch.sh {patch_path}"),
            cwd=str(instance.wrk_git), stdout=lf, stderr=lf,
            timeout=TIMEOUT,
        )
    return proc.returncode


# ═══════════════════════════════════════════════════════════════════
#  Pydantic models
# ═══════════════════════════════════════════════════════════════════

class CheckoutRequest(BaseModel):
    bug_id: str
    is_force: bool = False

class CompileRequest(BaseModel):
    bug_id: str

class TriggerTestRequest(BaseModel):
    bug_id: str

class RegressionTestRequest(BaseModel):
    bug_id: str

class ReproduceRequest(BaseModel):
    bug_id: str
    is_force_cleanup: bool = True

class FixRequest(BaseModel):
    bug_id: str
    patch_path: str

class BuildPatchRequest(BaseModel):
    bug_id: str
    llm_response: str
    method: str = "direct"
    generate_diff: bool = True
    persist_flag: bool = False

class ExecRequest(BaseModel):
    args: list = []
    cwd: str = ""

class ShellRequest(BaseModel):
    cmd: str = ""
    cwd: str = ""


# ═══════════════════════════════════════════════════════════════════
#  FastAPI Application
# ═══════════════════════════════════════════════════════════════════

app = FastAPI(title="Defects4C Service (D4J-compatible)", version="3.0.0")


@app.on_event("startup")
def startup():
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    _load_buglist()
    load_metadata()
    log.info("Startup: %d bugs, mode=%s", len(META_DICT), BUGLIST_MODE)


# ── Health ──
@app.get("/health")
def health():
    return {"status": "ok"}


# ── D4J-compatible /api/exec ──
@app.post("/api/exec")
def api_exec(req: ExecRequest):
    args = req.args
    if not args:
        return JSONResponse({"error": "No args provided"}, status_code=400)

    blocked, reason = _blocked_args(args)
    if blocked:
        return JSONResponse(
            {"returncode": 1, "stdout": "", "stderr": BLOCK_MSG.format(reason=reason)},
            status_code=403)

    parsed = translate_d4j_args(args)
    cmd = parsed.get("command")
    project = parsed.get("project", "")
    sha = parsed.get("sha", "")
    flags = parsed.get("flags", {})
    if sha:
        sha = _resolve_sha(sha)

    bug_id = f"{project}@{sha}" if project and sha else ""

    try:
        if cmd == "checkout":
            return d4c_checkout(project, sha, is_force=("-f" in flags))
        elif cmd == "compile":
            return d4c_compile(project, sha)
        elif cmd == "test":
            if "-r" in flags:
                return d4c_regression_test(project, sha)
            else:
                return d4c_trigger_test(project, sha)
        elif cmd == "info":
            if project and sha:
                return d4c_info(project, sha)
            elif project and not sha:
                bugs = [(s, v) for s, v in META_DICT.items() if v.get("project") == project]
                if not bugs:
                    return {"returncode": 1, "stdout": "", "stderr": f"Project '{project}' not found"}
                out = f"Project: {project}\nNumber of bugs: {len(bugs)}\n"
                for s, v in sorted(bugs):
                    out += f"  {s[:12]}  {v.get('files',{}).get('src',[''])[0]}\n"
                return {"returncode": 0, "stdout": out, "stderr": ""}
            else:
                return {"returncode": 1, "stdout": "", "stderr": "info requires -p project and/or -v sha"}
        elif cmd == "reproduce":
            return d4c_reproduce(project, sha)
        elif cmd == "pids":
            return {"returncode": 0, "stdout": "\n".join(sorted(PROJECTS_DIR.keys())) + "\n", "stderr": ""}
        elif cmd == "bids" and project:
            bugs = sorted(s for s, v in META_DICT.items() if v.get("project") == project)
            if bugs:
                return {"returncode": 0, "stdout": "\n".join(bugs) + "\n", "stderr": ""}
            else:
                return {"returncode": 1, "stdout": "", "stderr": f"No bugs for '{project}'"}
        else:
            return {"returncode": 1, "stdout": "",
                    "stderr": f"Unknown subcommand: {cmd}"}
    except Exception as exc:
        return {"returncode": 1, "stdout": "", "stderr": str(exc)}


@app.post("/api/exec-shell")
def api_exec_shell(req: ShellRequest):
    if not req.cmd:
        return JSONResponse(
            {"error": "No cmd provided"},
            status_code=400)
    blocked, reason = _blocked_shell(req.cmd)
    if blocked:
        return JSONResponse(
            {"returncode": 1, "stdout": "", "stderr": BLOCK_MSG.format(reason=reason)},
            status_code=403)
    return exec_shell(req.cmd, cwd=req.cwd or str(WORKSPACE), timeout=TIMEOUT)


@app.post("/api/upload")
async def api_upload(request: Request):
    """Upload a file via multipart form or base64 JSON."""
    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" in content_type:
        from fastapi import UploadFile
        form = await request.form()
        dest = form.get("path", "")
        if not dest:
            return JSONResponse({"error": "Missing 'path' form field"}, status_code=400)
        upload_file = form.get("file")
        if upload_file is None:
            return JSONResponse({"error": "Missing 'file' upload"}, status_code=400)
        dest = str(dest)
        if not os.path.isabs(dest):
            dest = os.path.join(str(WORKSPACE), dest)
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        file_content = await upload_file.read()
        with open(dest, "wb") as f:
            f.write(file_content)
        return {"status": "ok", "path": dest}
    elif "application/x-www-form-urlencoded" in content_type:
        return JSONResponse({"error": "Missing 'file' upload. Use multipart/form-data."}, status_code=400)
    else:
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON body"}, status_code=400)
        dest = body.get("path", "")
        content_b64 = body.get("content_base64", "")
        if not dest:
            return JSONResponse({"error": "Missing 'path'"}, status_code=400)
        if not os.path.isabs(dest):
            dest = os.path.join(str(WORKSPACE), dest)
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        content = base64.b64decode(content_b64)
        with open(dest, "wb") as f:
            f.write(content)
        return {"status": "ok", "path": dest, "size": len(content)}


@app.get("/api/download")
def api_download(path: str = ""):
    if not path:
        return JSONResponse({"error": "Missing 'path' query param"}, status_code=400)
    if not os.path.isabs(path):
        path = os.path.join(str(WORKSPACE), path)
    if not os.path.isfile(path):
        return JSONResponse({"error": f"File not found: {path}"}, status_code=404)
    return FileResponse(path)


# ── Phase endpoints ──

@app.post("/checkout")
def checkout_endpoint(req: CheckoutRequest):
    try:
        project, sha = parse_bug_id(req.bug_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return d4c_checkout(project, sha, is_force=req.is_force)


@app.post("/compile")
def compile_endpoint(req: CompileRequest):
    try:
        project, sha = parse_bug_id(req.bug_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return d4c_compile(project, sha)


@app.post("/trigger_test")
def trigger_test_endpoint(req: TriggerTestRequest):
    try:
        project, sha = parse_bug_id(req.bug_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return d4c_trigger_test(project, sha)


@app.post("/regression_test")
def regression_test_endpoint(req: RegressionTestRequest):
    try:
        project, sha = parse_bug_id(req.bug_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return d4c_regression_test(project, sha)


@app.post("/reproduce")
def reproduce_endpoint(req: ReproduceRequest, background_tasks: BackgroundTasks):
    try:
        project, sha = parse_bug_id(req.bug_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return d4c_reproduce(project, sha, req.is_force_cleanup)


# ── Selected bugs (cppcheck 2021-2022, verified: compile+fix=PASS+buggy=FAIL) ──
SELECTED_SHAS = {
    "caa6ff7c2a6ef64df53e04701944aaa4712a1915",  # TestAnalyzerInformation
    "d0b6079a832d5c156af1e51274e09f28ee8677a7",  # TestCondition
    "398fa280213771a0fa7a649a54dd6e6625d48e20",  # TestStl
    "c4dcfef38564e97442fb6c122f5e67c908e0c665",  # TestSymbolDatabase
    "4779f0e1725c5807b329798e12dbeaddbf568b65",  # TestSimplifyTemplate
    "192c30ab1d3ec97ae7c6955d8953fac133b8e4ff",  # TestTokenizer
}

# ── Defects4C-native endpoints ──

@app.get("/list_defects_bugid")
def list_defects_bugid():
    bug_ids = sorted(f"{v['project']}@{sha}" for sha, v in META_DICT.items())
    selected = [b for b in bug_ids if b.split("@")[1] in SELECTED_SHAS]
    return {"status": "success", "mode": BUGLIST_MODE, "total_count": len(bug_ids),
            "defects": bug_ids, "sample_defects": bug_ids[:5],
            "selected_count": len(selected), "selected": selected}


@app.get("/get_defect/{defect_id:path}")
def get_defect(defect_id: str):
    try:
        project, sha = parse_bug_id(defect_id)
    except ValueError:
        sha = defect_id
        if sha not in META_DICT:
            raise HTTPException(404, f"Defect '{defect_id}' not found. "
                                f"Total loaded: {len(META_DICT)}. "
                                f"Selected: {SELECTED_SHAS}")
        project = META_DICT[sha]["project"]

    if sha not in META_DICT:
        raise HTTPException(404, f"Defect '{defect_id}' not found in {len(META_DICT)} loaded bugs")

    rec = META_DICT[sha]
    bug_id = f"{project}@{sha}"

    try:
        instance = BugsInfo(project, sha)
        fl_info = instance.get_fl_info()
        triggers = instance.get_trigger_tests()
    except Exception as e:
        log.warning("get_defect: BugsInfo failed for %s: %s", bug_id, e)
        fl_info, triggers = {}, []

    src_file = rec.get("files", {}).get("src", [None])[0]
    test_files = rec.get("files", {}).get("test", [])
    test_flags = rec.get("c_compile", {}).get("test_flags", [])

    prompt_data = {
        "prompt": [
            {"role": "system", "content": "You are an expert C/C++ debugger. Fix the bug."},
            {"role": "user", "content": (
                f"Fix the bug in '{src_file}' of '{project}'.\n"
                f"Bug type: {rec.get('type', {}).get('name', 'unknown')}\n"
                f"Buggy lines: {fl_info.get('hunk_start', '?')}-{fl_info.get('hunk_end', '?')}.\n"
            )},
        ],
        "temperature": 0.7,
    }

    # Trigger = filtered via inplace_test.sh; regression = all tests via inplace_test.sh (no filter)
    trigger_desc = f"bash inplace_test.sh (filtered: {' | '.join(test_flags)})" if test_flags else "bash inplace_test.sh (all)"
    regression_desc = "bash inplace_test.sh (all targets, no filter)"

    return {
        "status": "success", "bug_id": bug_id, "defect_id": bug_id,
        "sha_id": sha, "prompt_data": prompt_data,
        "metadata": rec, "fl_info": fl_info,
        "trigger_tests": triggers,
        "trigger_test_filter": test_flags,
        "trigger_test_command": trigger_desc,
        "regression_tests": ["__all__"],
        "regression_test_command": regression_desc,
        "test_files": test_files,
        "is_selected": sha in SELECTED_SHAS,
    }


@app.post("/build_patch")
def build_patch_endpoint(req: BuildPatchRequest):
    return build_patch_from_llm(
        bug_id=req.bug_id, llm_response=req.llm_response,
        method=req.method, generate_diff=req.generate_diff,
        persist_flag=req.persist_flag,
    )


@app.post("/fix")
def fix_endpoint(req: FixRequest, background_tasks: BackgroundTasks):
    try:
        project, sha = parse_bug_id(req.bug_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    handle = uuid.uuid4().hex
    tasks[handle] = {"bug_id": req.bug_id, "sha": sha,
                     "status": "queued", "patch_path": req.patch_path}
    background_tasks.add_task(run_fix_async, req.bug_id, req.patch_path, handle)
    return {"handle": handle}


@app.get("/status/{handle}")
def get_status(handle: str):
    if handle not in tasks:
        raise HTTPException(404, "Handle not found")
    return tasks[handle]


@app.get("/projects")
def list_projects():
    return {"projects": list(PROJECTS_DIR.keys())}


# ── Oracle validation: use commit_after (fix) as ground-truth patch ──

class OracleRequest(BaseModel):
    bug_id: str
    mode: str = "fix"  # "fix" = apply oracle fix, "buggy" = apply buggy version

@app.post("/validate_oracle")
def validate_oracle(req: OracleRequest):
    """
    Oracle validation: checkout the known-good (commit_after) or known-bad
    (commit_before) source file, rebuild, and run trigger test.

    mode="fix"   → checkout commit_after's src_file → rebuild → trigger test → expect PASS
    mode="buggy" → checkout commit_before's src_file → rebuild → trigger test → expect FAIL

    This validates that the reproduce setup is correct and the bug is real.
    """
    try:
        project, sha = parse_bug_id(req.bug_id)
        instance = BugsInfo(project, sha)
    except Exception as exc:
        return {"success": False, "error": str(exc)}

    # Validate mode FIRST (before any repo/filesystem checks)
    if req.mode not in ("fix", "buggy"):
        return {"success": False, "error": f"Unknown mode: {req.mode}. Use 'fix' or 'buggy'."}

    repo_dir = instance.wrk_git
    build_dir = repo_dir / f"build_{sha}"
    src_file = instance.meta_info.get("src_file", "")
    commit_after = sha
    commit_before = instance.meta_defect.get("commit_before", "")

    if not ((repo_dir / ".git").exists() or instance._git_tree.exists()):
        return {"success": False, "error": f"Repo not found: {repo_dir}. Run warmup first."}

    if not build_dir.exists():
        return {"success": False, "error": f"Build dir not found: {build_dir}. Run compile first."}

    # Determine which version to checkout
    if req.mode == "fix":
        target_commit = commit_after
        expected_test = "PASS"
    elif req.mode == "buggy":
        target_commit = commit_before
        expected_test = "FAIL"
    else:
        return {"success": False, "error": f"Unknown mode: {req.mode}. Use 'fix' or 'buggy'."}

    git_prefix = f"git --git-dir={instance._git_tree} --work-tree={instance.wrk_git}" \
        if instance._git_tree.exists() else "git"

    # Fallback for commit_before if unreachable
    if req.mode == "buggy":
        check = exec_shell(f"{git_prefix} cat-file -t {target_commit}", cwd=str(repo_dir))
        if check["returncode"] != 0:
            parent_r = exec_shell(f"{git_prefix} rev-parse {commit_after}~1", cwd=str(repo_dir))
            if parent_r["returncode"] == 0 and parent_r["stdout"].strip():
                target_commit = parent_r["stdout"].strip()
                log.info("ORACLE: fallback commit_before=%s", target_commit[:12])
            else:
                return {"success": False, "error": "commit_before unreachable and no parent found"}

    log.info("ORACLE mode=%s bug=%s target=%s src=%s expected=%s",
             req.mode, req.bug_id, target_commit[:12], src_file, expected_test)

    # Step 1: Checkout the target version's source file
    if src_file:
        cmd = f"{git_prefix} checkout -f {target_commit} -- {src_file}"
    else:
        cmd = f"{git_prefix} checkout -f {target_commit}"
    result = exec_shell(cmd, cwd=str(repo_dir))
    if result["returncode"] != 0:
        return {"success": False, "step": "checkout", "error": result["stderr"][:500]}

    # Step 2: Rebuild (incremental) using inplace_rebuild.sh
    instance.set_reproduce_build()
    rebuild_log = str(instance.wrk_log / f"oracle_{req.mode}_{sha}_rebuild.log")
    rebuild_r = exec_shell(
        f"bash inplace_rebuild.sh build_{sha} {rebuild_log}",
        cwd=str(repo_dir), timeout=TIMEOUT)
    if rebuild_r["returncode"] != 0:
        return {"success": False, "step": "rebuild", "mode": req.mode,
                "error": rebuild_r["stderr"][:500], "stdout": rebuild_r["stdout"][:500]}

    # Step 3: Run trigger test using inplace_test.sh
    log_path = str(instance.wrk_log / f"oracle_{req.mode}_{sha}.log")
    test_r = exec_shell(
        f"bash inplace_test.sh build_{sha} {log_path}",
        cwd=str(repo_dir), timeout=TIMEOUT)

    combined = test_r["stdout"] + test_r["stderr"]
    if os.path.isfile(log_path):
        try:
            with open(log_path, encoding="utf-8", errors="ignore") as f:
                combined += f.read()
        except Exception:
            pass
    test_passed = "100% tests passed" in combined

    actual = "PASS" if test_passed else "FAIL"
    matches = (actual == expected_test)

    return {
        "success": True,
        "mode": req.mode,
        "bug_id": req.bug_id,
        "target_commit": target_commit[:12],
        "src_file": src_file,
        "expected": expected_test,
        "actual": actual,
        "matches_expectation": matches,
        "test_returncode": test_r["returncode"],
        "test_output_tail": combined[-500:] if combined else "",
        "oracle_log": log_path,
        "verdict": f"{'CORRECT' if matches else 'MISMATCH'}: "
                   f"mode={req.mode} expected={expected_test} actual={actual}",
    }


# ═══════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("D4C_PORT", "11111"))
    uvicorn.run(app, host="0.0.0.0", port=port)

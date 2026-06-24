"""
bug_helper_v1_out2.py — Defects4C bug helper (controller).

All build/test logic lives here. webapp.py wraps these as HTTP endpoints.

Commands:
  reproduce  <bug_id>               Full reproduce (warmup, run ONCE)
  fix        <bug_id> <patch_path>  Copy patch → src_file, rebuild, test
  checkout   <bug_id>               Acquire pool slot, restore buggy src_file
  compile    <bug_id>               Check build_dir exists (warmup already built)
  test       <bug_id>               Pure rebuild + test (src_file already edited)
  info       <bug_id>               Print bug metadata
  release_slot <bug_id> <slot_path> Release a previously acquired pool slot

bug_id format: project@sha  (short sha supported)

Architecture (workspace pool):
  warmup (reproduce) runs ONCE on the golden directory.
  After warmup, create_pool_slots() pre-creates __s0/__s1/__s2 via rsync.
  The flow is always:
    checkout (acquire slot) → edit src_file → test (rebuild+run) → release_slot
  - cmd_checkout: acquires a pool slot, restores buggy src from golden's git
  - cmd_fix: copies patch file into src, then rebuild+test
  - cmd_puretest: just rebuild+test (src already in place, do NOT touch it)
  - cmd_release_slot: releases the pool slot lock
"""

import sys
import os
import json
import shlex
import subprocess
import fcntl
from os.path import join as opj

import signal

def _run_with_pgkill(cmd, cwd, stdout=None, stderr=None, timeout=1800, encoding="utf-8", errors="replace"):
    """Run a subprocess in its own process group; on timeout, kill the entire group.
    This prevents orphan grandchildren (e.g. autotest) from surviving."""
    env = os.environ.copy()
    env["ASAN_OPTIONS"] = "abort_on_error=1:detect_leaks=0"
    proc = subprocess.Popen(
        shlex.split(cmd) if isinstance(cmd, str) else cmd,
        cwd=cwd, stdout=stdout, stderr=stderr,
        encoding=encoding, errors=errors,
        env=env,
        start_new_session=True,  # new process group
    )
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        # Kill the entire process group
        pgid = os.getpgid(proc.pid)
        print(f"[_run_with_pgkill] TIMEOUT ({timeout}s) — killing pgid {pgid}", file=sys.stderr)
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()
        raise
    return proc

import jmespath
from jinja2 import Environment, FileSystemLoader


ROOT_DIR = os.environ.get("ROOT_DIR", "/out/")
SRC_DIR = os.path.dirname(os.path.abspath(__file__))

PROJECTS_DIRS = {
    "v0": os.path.join(SRC_DIR, "projects"),
    "v1": os.path.join(SRC_DIR, "projects_v1"),
}

COMMON_META_INFO = dict(
    repo_dir=None, log_dir=None, build_dir=None, test_log=None,
    commit_after=None, commit_before=None, src_files=None,
)


def _gittree_dir(project, sha):
    """Sibling-of-golden path holding the relocated .git for oracle history.

    Layout: out/<project>/_gittree_<sha>/.git/  (sibling of git_repo_dir_<sha>/).
    """
    return os.path.join(ROOT_DIR, project, f"_gittree_{sha}")


def _init_slot_git(slot):
    """Init a slot as an empty git repo with a single baseline-buggy commit.

    Lets the agent run `git add`/`git commit`/`git diff` from the slot WITHOUT
    seeing oracle history. Idempotent: skips if .git already present.
    """
    if os.path.isdir(os.path.join(slot, ".git")):
        return
    subprocess.run(["git", "-C", slot, "init", "-q"], check=False, timeout=60)
    subprocess.run(["git", "-C", slot, "add", "-A"], check=False, timeout=300)
    subprocess.run(
        ["git", "-C", slot,
         "-c", "user.email=d4c@local", "-c", "user.name=d4c",
         "commit", "-q", "-m", "baseline_buggy"],
        check=False, timeout=120,
    )


def apt_install_tool():
    return """
apt_install_fn() {
    library=$1
    if dpkg -s "$library" &> /dev/null; then
        echo "$library is already installed"
    elif which "$library" >/dev/null 2>&1; then
        echo "$library is already installed"
    else
        echo "$library is not installed, attempting to install..."
        sudo apt-get update -y
        sudo apt-get install -y "$library"
    fi
}
    """


def detect_version(project):
    for version, base_dir in PROJECTS_DIRS.items():
        candidate = os.path.join(base_dir, project)
        if os.path.isdir(candidate):
            return version, base_dir
    raise ValueError(f"Project '{project}' not found in: {list(PROJECTS_DIRS.values())}")


def collect_all_projects():
    result = []
    for version, base_dir in PROJECTS_DIRS.items():
        if os.path.isdir(base_dir):
            result.extend(d for d in os.listdir(base_dir) if "___" in d)
    return result


def resolve_sha(project, short_sha):
    if len(short_sha) >= 40:
        return short_sha
    version, base_dir = detect_version(project)
    src_project = os.path.join(base_dir, project)
    for name in ("bugs_list_new.json", "bugs_list.json"):
        p = os.path.join(src_project, name)
        if os.path.exists(p):
            with open(p) as f:
                for b in json.load(f):
                    if b.get("commit_after", "").startswith(short_sha):
                        return b["commit_after"]
    return short_sha


def parse_bug_id(bug_id):
    bug_id = bug_id.replace(":", "@")
    project, _, sha = bug_id.partition("@")
    if not project or not sha:
        raise ValueError(f"Bug ID must be 'project@sha', got '{bug_id}'")
    sha = sha.rstrip("bf")
    sha = resolve_sha(project, sha)
    return project, sha


# ═══════════════════════════════════════════════════════════════
#  Workspace Pool Manager
# ═══════════════════════════════════════════════════════════════

POOL_SIZE = 3  # slots per bug

def _slot_dir(golden_dir, slot_idx):
    """Return path to slot directory."""
    return os.path.join(golden_dir, f"__s{slot_idx}")

def _lock_path(golden_dir, slot_idx):
    """Return path to slot lock file."""
    return os.path.join(golden_dir, f"__s{slot_idx}.lock")

def acquire_slot(project, sha):
    """Acquire a pool slot for the given bug. Returns (slot_path, lock_fd) or raises."""
    golden = os.path.join(ROOT_DIR, project, f"git_repo_dir_{sha}")
    for i in range(POOL_SIZE):
        lock_file = _lock_path(golden, i)
        try:
            fd = open(lock_file, 'w')
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # Got the lock — restore slot from golden
            slot = _slot_dir(golden, i)
            try:
                _restore_slot(golden, slot, project, sha)
            except Exception as _rest_exc:
                print(f"[acquire_slot] _restore_slot failed for slot {i}: {_rest_exc}", file=sys.stderr)
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                    fd.close()
                except:
                    pass
                continue
            return slot, fd
        except (IOError, OSError) as _slot_exc:
            # Slot busy or open/flock failed, try next
            try:
                fd.close()
            except:
                pass
            continue
    raise RuntimeError(f"All {POOL_SIZE} pool slots busy for {project}@{sha}")

def release_slot(lock_fd):
    """Release a pool slot."""
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()
    except:
        pass

def _fixup_cmake_paths(golden, slot, sha):
    """After rsyncing from golden to slot, fix build-system-generated absolute
    paths so they point to the slot instead of the golden.

    Handles two cases:
    1. cmake projects (build_{sha}/ subdir): fix build.ninja, CMakeCache.txt, etc.
       Also neuters cmake's auto-regeneration rule in build.ninja.
    2. configure/make projects (in-place): fix Makefile, libtool, config.status, etc.
    """
    import re as _re
    targets = []

    # ── Case 1: cmake build dir ──
    build_dir = os.path.join(slot, f"build_{sha}")
    if os.path.isdir(build_dir):
        for pattern in ["build.ninja", "CMakeCache.txt", "DartConfiguration.tcl",
                        "CTestTestfile.cmake", "cmake_install.cmake"]:
            for root, dirs, files in os.walk(build_dir):
                if pattern in files:
                    targets.append(os.path.join(root, pattern))
        # Also fix gtest-discovery cmake files (*_include.cmake, *_tests.cmake)
        # These have brackets in filenames like test[1]_include.cmake and
        # contain absolute paths to test binaries.
        for root, dirs, files in os.walk(build_dir):
            for fn in files:
                if fn.endswith("_include.cmake") or fn.endswith("_tests.cmake"):
                    targets.append(os.path.join(root, fn))

    # ── Case 2: in-place configure/make (Makefile, libtool, config.status) ──
    for pattern in ["Makefile", "libtool", "config.status", "config.log"]:
        fpath = os.path.join(slot, pattern)
        if os.path.isfile(fpath):
            targets.append(fpath)

    if not targets:
        return
    # Replace golden path with slot path in all config files
    # Preserve original timestamps so ninja/make don't trigger full rebuilds.
    for fpath in targets:
        try:
            st = os.stat(fpath)
            orig_times = (st.st_atime, st.st_mtime)
            with open(fpath, 'r', encoding='utf-8', errors='surrogateescape') as f:
                content = f.read()
            if golden not in content:
                continue
            content = content.replace(golden, slot)
            # For build.ninja: neuter the cmake regeneration rule so ninja
            # doesn't re-run cmake (which would write golden paths back).
            if fpath.endswith("build.ninja"):
                # Remove "build build.ninja: RERUN_CMAKE ..." block
                content = _re.sub(
                    r'^build build\.ninja: RERUN_CMAKE[^\n]*\n(?:  [^\n]*\n)*',
                    '# cmake regeneration disabled for pool slot\n',
                    content, flags=_re.MULTILINE)
                # Also remove "build CMakeFiles/cmake.check_cache: ..." block
                content = _re.sub(
                    r'^build CMakeFiles/cmake\.check_cache[^\n]*\n(?:  [^\n]*\n)*',
                    '', content, flags=_re.MULTILINE)
            with open(fpath, 'w', encoding='utf-8', errors='surrogateescape') as f:
                f.write(content)
            # Restore original timestamps to avoid spurious rebuilds
            os.utime(fpath, orig_times)
        except Exception as e:
            print(f"[_fixup_cmake_paths] WARN: {fpath}: {e}", file=sys.stderr)
    print(f"[_fixup_cmake_paths] fixed {len(targets)} files in {slot}", file=sys.stderr)


def _restore_slot(golden, slot, project, sha):
    """Restore a slot to the buggy state by rsyncing from golden."""
    # Find the src_file from metadata
    version, src_project_dir = detect_version(project)
    src_project = os.path.join(src_project_dir, project)
    bugs_file = os.path.join(src_project, "bugs_list_new.json")
    if not os.path.exists(bugs_file):
        bugs_file = os.path.join(src_project, "bugs_list.json")
    with open(bugs_file) as f:
        meta_bugs = json.load(f)
    meta = jmespath.search(f"[?commit_after=='{sha}']", meta_bugs)
    if not meta:
        raise RuntimeError(f"Bug {sha} not found in metadata")
    src_file = jmespath.search("files.src[0]", meta[0]) or ""
    commit_before = meta[0].get("commit_before", "")

    # Pick the git source for the buggy src_file: relocated _gittree wins,
    # legacy in-place golden/.git is the fallback for unmigrated bugs.
    gittree = _gittree_dir(project, sha)
    gittree_git = os.path.join(gittree, ".git")
    if os.path.isdir(gittree_git):
        git_show_cmd = ["git", f"--git-dir={gittree_git}", "show", f"{commit_before}:{src_file}"]
    elif os.path.isdir(os.path.join(golden, ".git")):
        git_show_cmd = ["git", "-C", golden, "show", f"{commit_before}:{src_file}"]
    else:
        git_show_cmd = None  # neither tree exists; will be reported as failure below

    if not os.path.isdir(slot):
        # First time — full rsync from golden (excluding .git and slots)
        subprocess.run(
            ["rsync", "-a", "--exclude=.git", "--exclude=__s*", "--exclude=*.lock",
             golden + "/", slot + "/"],
            check=True, timeout=600
        )
        # Fix cmake absolute paths: golden → slot
        _fixup_cmake_paths(golden, slot, sha)
        # Restore buggy src_file via the chosen git source BEFORE init,
        # so the baseline commit captures the buggy state.
        if src_file and commit_before and git_show_cmd is not None:
            r = subprocess.run(
                git_show_cmd,
                capture_output=True, timeout=60
            )
            if r.returncode == 0:
                dst = os.path.join(slot, src_file)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                with open(dst, 'wb') as f:
                    f.write(r.stdout)
        # Now init the slot's own git tree with baseline-buggy commit.
        _init_slot_git(slot)
    else:
        # Slot exists — just restore the src_file to buggy state
        if src_file and commit_before and git_show_cmd is not None:
            r = subprocess.run(
                git_show_cmd,
                capture_output=True, timeout=60
            )
            if r.returncode == 0:
                dst = os.path.join(slot, src_file)
                with open(dst, 'wb') as f:
                    f.write(r.stdout)
    # Touch src files to invalidate build cache
    if src_file:
        fp = os.path.join(slot, src_file)
        if os.path.isfile(fp):
            os.utime(fp, None)

def create_pool_slots(project, sha):
    """Pre-create pool slots during warmup. Called after reproduce completes."""
    golden = os.path.join(ROOT_DIR, project, f"git_repo_dir_{sha}")
    if not os.path.isdir(golden):
        print(f"[create_pool_slots] golden not found: {golden}", file=sys.stderr)
        return
    for i in range(POOL_SIZE):
        slot = _slot_dir(golden, i)
        if os.path.isdir(slot):
            print(f"[create_pool_slots] slot already exists: {slot}", file=sys.stderr)
            continue
        print(f"[create_pool_slots] creating slot {i}: {slot}", file=sys.stderr)
        subprocess.run(
            ["rsync", "-a", "--exclude=.git", "--exclude=__s*", "--exclude=*.lock",
             golden + "/", slot + "/"],
            check=True, timeout=600
        )
        _fixup_cmake_paths(golden, slot, sha)
        # Give the slot its own baseline-buggy git tree so the agent can run
        # git add/commit/diff without seeing oracle history.
        _init_slot_git(slot)
    print(f"[create_pool_slots] done {project}@{sha}: {POOL_SIZE} slots", file=sys.stderr)


# Global registry of active slot locks
_active_locks = {}


class BugsInfo:
    def __init__(self, project, sha, work_dir=None):
        self.sha = sha
        self.project = project
        self.version, self.src_project_dir = detect_version(project)
        self.src_project = os.path.join(self.src_project_dir, project)

        self.golden_dir = os.path.join(ROOT_DIR, project, f"git_repo_dir_{self.sha}")
        # work_dir overrides the effective working directory (for pool slots)
        self.wrk_git = work_dir if work_dir else self.golden_dir

        if self.version == "v0":
            if not os.path.isdir(self.golden_dir):
                self.golden_dir = os.path.join(ROOT_DIR, project, "git_repo_dir")
                self.wrk_git = work_dir if work_dir else self.golden_dir
        else:
            assert os.path.isdir(self.golden_dir), \
                f"v1 repo dir must exist: {self.golden_dir}. Run warmup first."

        self.wrk_log = os.path.join(ROOT_DIR, project, "logs")
        self.wrk_log_fn = os.path.join(ROOT_DIR, project, "logs", f"{self.sha}.log")
        os.makedirs(self.wrk_git, exist_ok=True)
        os.makedirs(self.wrk_log, exist_ok=True)

        bugs_file = os.path.join(self.src_project, "bugs_list_new.json")
        if not os.path.exists(bugs_file):
            bugs_file = os.path.join(self.src_project, "bugs_list.json")
        with open(bugs_file) as f:
            meta_bugs = json.load(f)
        with open(os.path.join(self.src_project, "project.json")) as f:
            self.meta_project = json.load(f)

        self.meta_defect = jmespath.search(f"[?commit_after=='{self.sha}']", meta_bugs)
        assert len(self.meta_defect) == 1, f"Bug {sha} not found or ambiguous"
        self.meta_defect = self.meta_defect[0]

        self.meta_info = {
            "apt_install_fn": apt_install_tool(),
            "cpu_count": max((os.cpu_count() or 2) - 1, 1),
        }
        self.meta_info.update({k: v for k, v in self.meta_defect.items() if k in COMMON_META_INFO})
        self.meta_info.update({"repo_dir": self.wrk_git, "log_dir": self.wrk_log})

        system_compile = jmespath.search("c_compile", self.meta_project) or {}
        defect_compile = jmespath.search("c_compile", self.meta_defect) or {}
        b_flags = ((jmespath.search("c_compile.build_flags", self.meta_project) or []) +
                   (jmespath.search("c_compile.build_flags", self.meta_defect) or []))
        t_flags = ((jmespath.search("c_compile.test_flags", self.meta_project) or []) +
                   (jmespath.search("c_compile.test_flags", self.meta_defect) or []))
        compile_kwargs = {"build_flags": b_flags, "test_flags": t_flags}
        if self.version == "v0":
            e_flags = ((jmespath.search("env", self.meta_project) or []) +
                       (jmespath.search("c_compile.env", self.meta_defect) or []))
            compile_kwargs["env"] = e_flags

        defect_compile = {x: y for x, y in defect_compile.items() if y is not None and len(y) > 0}
        self.meta_info.update({**self.meta_project, **system_compile, **defect_compile, **compile_kwargs})
        self.meta_info.update({
            "build_dir":   f"build_{sha}",
            "test_log":    os.path.join(self.wrk_log, f"test_{sha}_fix.log"),
            "test_files":  jmespath.search("files.test", self.meta_defect),
            "src_file":    jmespath.search("files.src[0]", self.meta_defect),
            "gittree_dir": _gittree_dir(self.project, sha),
        })

    def _build_tpl(self, tpl_path, dict_info, save_path):
        loader_dir = self.src_project
        if tpl_path.startswith("/"):
            loader_dir = os.path.dirname(tpl_path)
        env = Environment(loader=FileSystemLoader(loader_dir))
        template = env.get_template(os.path.basename(tpl_path))
        with open(save_path, "w") as f:
            f.write(template.render(**dict_info))

    def _build_tpl_path(self):
        val = self.meta_info.get("build", "")
        return val if ".jinja" in str(val) else os.path.abspath(opj(self.src_project_dir, "common_build_tpl.jinja"))

    def _test_tpl_path(self):
        val = self.meta_info.get("test", "")
        return val if ".jinja" in str(val) else os.path.abspath(opj(self.src_project_dir, "common_test_tpl.jinja"))

    def _workflow_reproduce_tpl(self):
        if self.version == "v0":
            return os.path.join(SRC_DIR, "projects", "workflow_tpl.jinja")
        return os.path.join(SRC_DIR, "projects_v1", "workflow_cmake_tpl.jinja")

    def _workflow_patch_tpl(self):
        if self.version == "v0":
            return os.path.join(SRC_DIR, "projects", "workflow_cmake_rebuild_tpl.jinja")
        return os.path.join(SRC_DIR, "projects_v1", "workflow_cmake_rebuild_tpl.jinja")

    def _workflow_puretest_tpl(self):
        if self.version == "v0":
            return os.path.join(SRC_DIR, "projects", "workflow_cmake_compile_test_tpl.jinja")
        return os.path.join(SRC_DIR, "projects_v1", "workflow_cmake_compile_test_tpl.jinja")

    def set_reproduce_build(self):
        rebuild_info = {"is_rebuild": True,
                        "test_log": os.path.join(self.wrk_log, f"test_{self.sha}_fix.log"),
                        **self.meta_info}
        reproduce_info = {**self.meta_info}
        self._build_tpl(self._build_tpl_path(), self.meta_info,
                        os.path.join(self.wrk_git, "inplace_build.sh"))
        self._build_tpl(self._build_tpl_path(), rebuild_info,
                        os.path.join(self.wrk_git, "inplace_rebuild.sh"))
        self._build_tpl(self._test_tpl_path(), self.meta_info,
                        os.path.join(self.wrk_git, "inplace_test.sh"))
        self._build_tpl(self._workflow_reproduce_tpl(), reproduce_info,
                        os.path.join(self.wrk_git, "run_reproduce.sh"))

    def set_puretest_build(self):
        """Render inplace_rebuild.sh + inplace_test.sh + run_puretest.sh for trigger test."""
        rebuild_info = {"is_rebuild": True, **self.meta_info}
        puretest_info = {**self.meta_info}
        self._build_tpl(self._build_tpl_path(), rebuild_info,
                        os.path.join(self.wrk_git, "inplace_rebuild.sh"))
        self._build_tpl(self._test_tpl_path(), self.meta_info,
                        os.path.join(self.wrk_git, "inplace_test.sh"))
        self._build_tpl(self._workflow_puretest_tpl(), puretest_info,
                        os.path.join(self.wrk_git, "run_puretest.sh"))


    def set_patch_build(self):
        rebuild_info = {"is_rebuild": True, **self.meta_info,
                        "test_log": os.path.join(self.wrk_log, f"test_{self.sha}_fix.log")}
        patch_info = {**self.meta_info,
                      "test_log": os.path.join(self.wrk_log, f"patch_{self.sha}_fix.log")}
        self._build_tpl(self._build_tpl_path(), rebuild_info,
                        os.path.join(self.wrk_git, "inplace_rebuild.sh"))
        self._build_tpl(self._test_tpl_path(), self.meta_info,
                        os.path.join(self.wrk_git, "inplace_test.sh"))
        self._build_tpl(self._workflow_patch_tpl(), patch_info,
                        os.path.join(self.wrk_git, "run_patch.sh"))

    def get_fl_info(self):
        loc = self.meta_defect.get("files", {}).get("src0_location", {})
        return {
            "src_file": jmespath.search("files.src[0]", self.meta_defect),
            "line_is_single": loc.get("line_is_single", False),
            "line_number": loc.get("line_number"),
            "hunk_is_single": loc.get("hunk_is_single", False),
            "hunk_start": loc.get("hunk_start"),
            "hunk_end": loc.get("hunk_end"),
            "func_is_single": loc.get("func_is_single", False),
            "func_start": loc.get("func_start"),
            "func_end": loc.get("func_end"),
        }

    def get_trigger_tests(self):
        names = self.meta_defect.get("unittest", {}).get("name", [])
        return [names] if isinstance(names, str) else (names or [])

    def get_regression_test_flags(self):
        return ""

    def get_log_paths(self, prefix="trigger"):
        """Return dict of log/status/msg paths for a given prefix."""
        base = os.path.join(self.wrk_log, f"{prefix}_{self.sha}")
        return {
            "log":    f"{base}.log",
            "msg":    f"{base}.msg",
            "status": f"{base}.status",
        }


def exec_cmd(cmd_info):
    one_cmd = cmd_info.pop("cmd")
    one_cmd = shlex.split(one_cmd)
    return subprocess.run(one_cmd, **cmd_info)


def read_status_file(path):
    if os.path.isfile(path):
        try:
            return open(path).read().strip()
        except Exception:
            pass
    return ""


# ═══════════════════════════════════════════════════════════════
#  Command implementations
# ═══════════════════════════════════════════════════════════════
def cmd_reproduce_soft(bug_id):
    """Full reproduce via run_reproduce.sh. Run ONCE during warmup."""
    project, sha = parse_bug_id(bug_id)
    instance = BugsInfo(project=project, sha=sha)
    print(f"[cmd_reproduce] START bug_id={bug_id} cwd={instance.wrk_git}", file=sys.stderr)
    with open(instance.wrk_log_fn, "w") as log_f:
        instance.set_reproduce_build()
        try:
            timeout = 3600 if ("llvm" in project or "njs" in project or "nginx" in project or "SPIRV" in project or "arrow" in project or "rocksdb" in project or "uncrustify" in project) else 1800
            print(f"[cmd_reproduce] EXEC: bash run_reproduce.sh (timeout={timeout}s)", file=sys.stderr)
            _run_with_pgkill("bash run_reproduce.sh", cwd=instance.wrk_git,
                       stdout=log_f, stderr=log_f, timeout=timeout)
        except subprocess.TimeoutExpired:
            print(f"[cmd_reproduce] TIMEOUT bug_id={bug_id}", file=sys.stderr)
    print(f"[cmd_reproduce] DONE bug_id={bug_id} log={instance.wrk_log_fn}", file=sys.stderr)
    return {"returncode": 0, "log_file": instance.wrk_log_fn}

def cmd_reproduce(bug_id):
    """Full reproduce via run_reproduce.sh. Run ONCE during warmup."""
    project, sha = parse_bug_id(bug_id)
    instance = BugsInfo(project=project, sha=sha)
    print(f"[cmd_reproduce] START bug_id={bug_id} cwd={instance.wrk_git}", file=sys.stderr)
    with open(instance.wrk_log_fn, "w") as log_f:
        exec_cmd({"cmd": f"git -C {instance.wrk_git} clean -dfx",
                   "cwd": instance.wrk_git, "stdout": log_f, "stderr": log_f})
        instance.set_reproduce_build()
        try:
            timeout = 3600 if ("llvm" in project or "njs" in project or "nginx" in project or "SPIRV" in project or "arrow" in project or "rocksdb" in project or "uncrustify" in project) else 1800
            print(f"[cmd_reproduce] EXEC: bash run_reproduce.sh (timeout={timeout}s)", file=sys.stderr)
            _run_with_pgkill("bash run_reproduce.sh", cwd=instance.wrk_git,
                       stdout=log_f, stderr=log_f, timeout=timeout)
        except subprocess.TimeoutExpired:
            print(f"[cmd_reproduce] TIMEOUT bug_id={bug_id}", file=sys.stderr)
    print(f"[cmd_reproduce] DONE bug_id={bug_id} log={instance.wrk_log_fn}", file=sys.stderr)
    return {"returncode": 0, "log_file": instance.wrk_log_fn}


def cmd_fix(bug_id, patch_path):
    """Copy patch → src_file, rebuild, test. Uses rendered run_patch.sh."""
    project, sha = parse_bug_id(bug_id)
    assert os.path.isfile(patch_path), f"Patch not found: {patch_path}"
    instance = BugsInfo(project=project, sha=sha)
    print(f"[cmd_fix] START bug_id={bug_id} patch={patch_path} cwd={instance.wrk_git}", file=sys.stderr)
    instance.set_patch_build()
    with open(instance.wrk_log_fn, "a") as log_f:
        try:
            print(f"[cmd_fix] EXEC: bash run_patch.sh {patch_path}", file=sys.stderr)
            _run_with_pgkill(f"bash run_patch.sh {patch_path}", cwd=instance.wrk_git,
                       stdout=log_f, stderr=log_f, timeout=1800)
        except subprocess.TimeoutExpired:
            print(f"[cmd_fix] TIMEOUT bug_id={bug_id}", file=sys.stderr)
    print(f"[cmd_fix] DONE bug_id={bug_id} log={instance.wrk_log_fn}", file=sys.stderr)
    return {"returncode": 0, "log_file": instance.wrk_log_fn}


def cmd_checkout(bug_id, work_dir=None):
    """Acquire a pool slot and restore buggy source. Returns dict with slot_path."""
    try:
        project, sha = parse_bug_id(bug_id)
    except Exception as exc:
        return {"returncode": 1, "stdout": "", "stderr": str(exc), "slot_path": ""}

    try:
        slot_path, lock_fd = acquire_slot(project, sha)
    except RuntimeError as exc:
        return {"returncode": 1, "stdout": "", "stderr": str(exc), "slot_path": ""}

    # Store the lock_fd globally so release_slot can be called later
    _active_locks[f"{project}@{sha}@{slot_path}"] = lock_fd

    return {"returncode": 0,
            "stdout": f"Checked out buggy source in slot: {slot_path}\n",
            "stderr": "",
            "slot_path": slot_path}


def cmd_release_slot(bug_id, slot_path):
    """Release a previously acquired pool slot."""
    key = f"{bug_id}@{slot_path}"
    # Try exact key first
    fd = _active_locks.pop(key, None)
    if fd is None:
        # Try with parsed bug_id
        try:
            project, sha = parse_bug_id(bug_id)
            key2 = f"{project}@{sha}@{slot_path}"
            fd = _active_locks.pop(key2, None)
        except Exception:
            pass
    if fd:
        release_slot(fd)
        return {"returncode": 0, "stdout": "Slot released\n", "stderr": ""}
    return {"returncode": 0, "stdout": "No active lock found (already released?)\n", "stderr": ""}


def cmd_compile(bug_id, work_dir=None):
    """Check build_dir exists. Warmup already compiled."""
    try:
        project, sha = parse_bug_id(bug_id)
        instance = BugsInfo(project=project, sha=sha, work_dir=work_dir)
    except Exception as exc:
        return {"returncode": 1, "stdout": "", "stderr": str(exc)}
    build_dir_path = os.path.join(instance.wrk_git, f"build_{sha}")
    if os.path.isdir(build_dir_path):
        return {"returncode": 0, "stdout": f"Build dir exists: {build_dir_path}\n", "stderr": ""}
    return {"returncode": 1, "stdout": "",
            "stderr": f"Build dir not found: {build_dir_path}. Run warmup first.\n"}



def cmd_puretest(bug_id, work_dir=None):
    """Pure rebuild + test. Does NOT touch src_file — it's already in place.

    Call cmd_checkout first to restore buggy, or cmd_fix to apply a patch.
    This just does: inplace_rebuild.sh → inplace_test.sh → read status.

    Returns dict with: returncode, passed, log_file, status, log_content, stderr.
    The log/status/xml files are left on disk.
    """
    try:
        project, sha = parse_bug_id(bug_id)
        instance = BugsInfo(project=project, sha=sha, work_dir=work_dir)
    except Exception as exc:
        print(f"[cmd_puretest] ERROR parsing bug_id={bug_id}: {exc}", file=sys.stderr)
        return {"returncode": 1, "passed": False, "log_file": "",
                "status": "", "log_content": "", "stderr": str(exc)}

    wrapper_log = instance.wrk_log_fn   # {sha}.log — wrapper output
    log_dir = instance.wrk_log

    print(f"[cmd_puretest] START bug_id={bug_id} cwd={instance.wrk_git}", file=sys.stderr)
    instance.set_puretest_build()
    print(f"[cmd_puretest] EXEC: bash run_puretest.sh  cwd={instance.wrk_git}", file=sys.stderr)

    rc = 1
    stderr_text = ""
    # Large cmake projects (SPIRV-Tools) may need a full rebuild in pool slots
    puretest_timeout = 3600 if "SPIRV" in project or "llvm" in project or "arrow" in project or "rocksdb" in project else 1800
    with open(wrapper_log, "w") as log_f:
        try:
            proc = _run_with_pgkill(
                "bash run_puretest.sh",
                cwd=instance.wrk_git,
                stdout=log_f, stderr=log_f,
                timeout=puretest_timeout,
            )
            rc = proc.returncode
        except subprocess.TimeoutExpired:
            print(f"[cmd_puretest] TIMEOUT bug_id={bug_id}", file=sys.stderr)
            stderr_text = f"Timeout after {puretest_timeout}s"

    print(f"[cmd_puretest] DONE bug_id={bug_id} rc={rc} wrapper_log={wrapper_log}", file=sys.stderr)

    # ── Find the REAL test log/status/msg files ──
    # inplace_test.sh writes: test_{sha}_{md5}.log / .status / .msg
    # where md5 is the MD5 of the src_file content.
    import hashlib as _hashlib

    test_log_file = ""
    test_status_file = ""
    test_msg_file = ""
    status_text = ""
    log_content = ""

    # Compute MD5 of the current src_file to find the correct status file
    src_file = instance.meta_info.get("src_file", "")
    src_md5 = ""
    if src_file:
        src_path = os.path.join(instance.wrk_git, src_file)
        if os.path.isfile(src_path):
            h = _hashlib.md5()
            with open(src_path, "rb") as _f:
                for _chunk in iter(lambda: _f.read(8192), b""):
                    h.update(_chunk)
            src_md5 = h.hexdigest()
            print(f"[cmd_puretest] src_file={src_file} md5={src_md5}", file=sys.stderr)

    # Strategy 1: Look for status file matching the src_file MD5
    if src_md5:
        exact_status = os.path.join(log_dir, f"test_{sha}_{src_md5}.status")
        if os.path.isfile(exact_status):
            test_status_file = exact_status
            base = test_status_file.rsplit(".status", 1)[0]
            test_log_file = base + ".log"
            test_msg_file = base + ".msg"
            status_text = read_status_file(test_status_file)
            print(f"[cmd_puretest] Found status_file (md5 match)={test_status_file} status={status_text!r}", file=sys.stderr)

    # No fallback: if exact MD5 status file not found, the test didn't run
    # (e.g., build failed). Returning a stale status file from a previous run
    # would cause false positives.
    if not test_status_file:
        print(f"[cmd_puretest] No status_file for md5={src_md5}. Test likely did not run (build failure?).", file=sys.stderr)
        test_log_file = wrapper_log
        status_text = "FAILED"

    # Read the actual test log content
    actual_log = test_log_file if os.path.isfile(test_log_file) else wrapper_log
    try:
        with open(actual_log, "r", encoding="utf-8", errors="ignore") as f:
            log_content = f.read()
    except Exception:
        pass

    passed = "success" in status_text.lower()
    # Override: if returncode indicates a crash signal (e.g., SIGSEGV=139, SIGABRT=134),
    # treat as FAIL even if the status file says "success" (the test script may not
    # have captured the shell's crash message in the test_log file).
    if rc and rc > 128:
        # 255 is a generic shell error, not necessarily a signal
        if rc != 255:
            passed = False
            status_text = "FAILED"

    return {
        "returncode": rc,
        "passed": passed,
        "log_file": actual_log,
        "status_file": test_status_file or "",
        "msg_file": test_msg_file if os.path.isfile(test_msg_file) else "",
        "status": status_text,
        "log_content": log_content,
        "stderr": stderr_text,
    }


def cmd_info(bug_id):
    project, sha = parse_bug_id(bug_id)
    instance = BugsInfo(project=project, sha=sha)
    fl = instance.get_fl_info()
    triggers = instance.get_trigger_tests()
    print(f"Project: {project}")
    print(f"Bug SHA (commit_after): {sha}")
    print(f"Commit before (buggy): {instance.meta_defect.get('commit_before', '')}")
    print(f"Source file: {fl['src_file']}")
    print(f"Hunk range: [{fl.get('hunk_start', '?')}, {fl.get('hunk_end', '?')}]")
    print(f"Func range: [{fl.get('func_start', '?')}, {fl.get('func_end', '?')}]")
    print(f"Trigger tests: {triggers}")
    print(f"Bug type: {instance.meta_defect.get('type', {}).get('name', 'unknown')}")
    print(f"Test flags: {instance.meta_info.get('test_flags', [])}")
    print(f"Repo dir: {instance.wrk_git}")
    print(f"Build dir: {instance.meta_info['build_dir']}")
    return {"returncode": 0}


# ═══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import argparse
    project_list = collect_all_projects()
    parser = argparse.ArgumentParser(prog='bug_helper',
        description='Defects4C bug helper',
        epilog='bug_id format: project@sha')
    subparsers = parser.add_subparsers(title="Commands", dest="command")
    for name in ["reproduce", "checkout", "compile", "info"]:
        sp = subparsers.add_parser(name)
        sp.add_argument("bug_id")
    test_parser = subparsers.add_parser("test")
    test_parser.add_argument("bug_id")
    test_parser.add_argument("-r", "--regression", action="store_true")
    fix_parser = subparsers.add_parser("fix")
    fix_parser.add_argument("bug_id")
    fix_parser.add_argument("patch_path")
    release_parser = subparsers.add_parser("release_slot")
    release_parser.add_argument("bug_id")
    release_parser.add_argument("slot_path")
    args = parser.parse_args()
    if not args.command:
        parser.print_help(); sys.exit(1)
    _project = args.bug_id.split("@")[0]
    assert _project in project_list, (_project, project_list)
    if args.command == "reproduce": cmd_reproduce(args.bug_id)
    elif args.command == "fix": cmd_fix(args.bug_id, args.patch_path)
    elif args.command == "checkout": cmd_checkout(args.bug_id)
    elif args.command == "compile": cmd_compile(args.bug_id)
    elif args.command == "test": cmd_puretest(args.bug_id)
    elif args.command == "info": cmd_info(args.bug_id)
    elif args.command == "release_slot": cmd_release_slot(args.bug_id, args.slot_path)

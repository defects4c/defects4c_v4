"""
bug_helper_v1_out2.py — Defects4C bug helper (controller).

All build/test logic lives here. webapp.py wraps these as HTTP endpoints.

Commands:
  reproduce  <bug_id>               Full reproduce (warmup, run ONCE)
  fix        <bug_id> <patch_path>  Copy patch → src_file, rebuild, test
  checkout   <bug_id>               Acquire pool slot, FULL-reset it to buggy baseline
  compile    <bug_id>               Check build_dir exists (warmup already built)
  test       <bug_id>               Pure rebuild + test (src_file already edited)
  info       <bug_id>               Print bug metadata
  release_slot <bug_id> <slot_path> Release a previously acquired pool slot

bug_id format: project@sha  (short sha supported)

Architecture (workspace pool):
  warmup (reproduce) runs ONCE on the golden directory.
  After warmup, create_pool_slots() builds ONE pristine per-bug backup (buggy
  worktree + pre-built build_<sha>/ + baseline_buggy git) and provisions
  __s0/__s1/__s2 from it. The flow is always:
    checkout (acquire slot) → edit src_file → test (rebuild+run) → release_slot
  - cmd_checkout: acquires a pool slot and FULLY resets it to the buggy baseline
      by rsyncing the whole backup (worktree + git tree) over it with --delete,
      so no leftover from a previous holder can leak into the next verdict.
  - cmd_fix: copies patch file into src, then rebuild+test
  - cmd_puretest: just rebuild+test (src already in place, do NOT touch it)
  - cmd_release_slot: releases the pool slot lock
"""

import sys
import os
import json
import shlex
import shutil
import subprocess
import fcntl
import time
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

# Ownership of a pool slot is recorded as JSON *content* inside __s{i}.lock
# (an "owner file"), NOT as a POSIX flock held open across HTTP requests. The
# old scheme kept the flock fd in a per-worker dict and relied on the *release*
# request landing on the same gunicorn worker that served *checkout*; when it
# didn't (7 of 8 times) the fd leaked and pinned the slot for the worker's whole
# life. With an owner file, release just empties the file and can run on ANY
# worker, so the slot can never be leaked by a cross-worker handoff.
#
# SLOT_TTL is only a backstop for a client that crashes without releasing. It
# must comfortably exceed the longest time an agent legitimately holds a slot
# between checkout and release (AGENT_TIMEOUT is ~4200s), so a live session is
# never reclaimed out from under itself. Normal releases are immediate.
SLOT_TTL = 14400  # 4h

def _read_owner(meta_fd):
    """Parse the owner record from an open lock file, or None if free/garbage."""
    try:
        meta_fd.seek(0)
        raw = meta_fd.read().strip()
    except Exception:
        return None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None  # empty / legacy / corrupt → treat as free

def _write_owner(meta_fd):
    """Stamp this process + wall-clock time as the slot owner."""
    meta_fd.seek(0)
    meta_fd.truncate()
    meta_fd.write(json.dumps({"pid": os.getpid(), "ts": time.time()}))
    meta_fd.flush()
    try:
        os.fsync(meta_fd.fileno())
    except Exception:
        pass

def _clear_owner_file(lock_file):
    """Mark a slot free by emptying its owner record. Worker-independent: the
    brief flock is taken and released inside this call, never held across
    requests, so it cannot leak."""
    try:
        with open(lock_file, 'a+') as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.seek(0)
                f.truncate()
                f.flush()
                try:
                    os.fsync(f.fileno())
                except Exception:
                    pass
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
        return True
    except Exception as exc:
        print(f"[release] clear owner failed for {lock_file}: {exc}", file=sys.stderr)
        return False

def acquire_slot(project, sha):
    """Acquire a pool slot for the given bug. Returns (slot_path, lock_handle)
    or raises. `lock_handle` is the lock-file path (a plain string), passed back
    to release_slot(). A brief flock is used only to make each claim atomic and
    is released before this function returns — nothing is held across requests."""
    golden = os.path.join(ROOT_DIR, project, f"git_repo_dir_{sha}")
    now = time.time()
    for i in range(POOL_SIZE):
        lock_file = _lock_path(golden, i)
        claimed = False
        try:
            meta_fd = open(lock_file, 'a+')
        except (IOError, OSError):
            continue
        try:
            fcntl.flock(meta_fd, fcntl.LOCK_EX)  # brief, in-call only
            owner = _read_owner(meta_fd)
            busy = owner is not None and (now - float(owner.get("ts", 0) or 0)) < SLOT_TTL
            if not busy:
                _write_owner(meta_fd)  # claim: fresh slot or reclaim a stale one
                claimed = True
        finally:
            try:
                fcntl.flock(meta_fd, fcntl.LOCK_UN)
            except Exception:
                pass
            meta_fd.close()
        if not claimed:
            continue
        # We logically own slot i now — restore buggy source into it.
        slot = _slot_dir(golden, i)
        try:
            _restore_slot(golden, slot, project, sha)
        except Exception as _rest_exc:
            print(f"[acquire_slot] _restore_slot failed for slot {i}: {_rest_exc}", file=sys.stderr)
            _clear_owner_file(lock_file)  # give the claim back, try next slot
            continue
        return slot, lock_file
    raise RuntimeError(f"All {POOL_SIZE} pool slots busy for {project}@{sha}")

def release_slot(lock_handle):
    """Release a pool slot. `lock_handle` is the lock-file path returned by
    acquire_slot; emptying it frees the slot from any worker. A legacy raw file
    object is still accepted for safety (unlocked + closed)."""
    if not lock_handle:
        return
    if hasattr(lock_handle, "fileno"):  # back-compat: legacy fd object
        try:
            fcntl.flock(lock_handle, fcntl.LOCK_UN)
            lock_handle.close()
        except Exception:
            pass
        return
    _clear_owner_file(lock_handle)

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


# ═══════════════════════════════════════════════════════════════
#  Pristine backup (the "golden slot") + full-reset semantics
# ═══════════════════════════════════════════════════════════════
#
# A slot must be a CLEAN buggy checkout every time an agent acquires it, so
# leftovers from a previous verification (edits to files other than src_file,
# the agent's own git commits, stray build artifacts) can never leak into the
# next patch's verdict. Defects4J gets this for free by checking out a brand
# new tree into a fresh tmp folder; Defects4C is git-based and reuses slots, so
# we keep ONE pristine per-bug backup and rsync the whole thing — worktree AND
# git tree — over the slot on every reset.
#
# Backup layout (sibling of golden, so it is never nested inside a slot and is
# never matched by the `__s*` slot excludes):
#     out/<project>/git_repo_dir_<sha>__backup/
#         <worktree, buggy src, pre-built build_<sha>/>
#         .git/                          # baseline_buggy, self-contained
#         .gitignore                     # ignores build_<sha>/ + generated scripts
# The backup is built ONCE from golden (oracle .git excluded, buggy src_file
# restored, its own baseline git initialised). Each slot is then just
# `rsync --delete backup/ slot/` + a cmake absolute-path fixup (backup→slot).


def _slot_backup_dir(golden):
    """Pristine per-bug backup dir. Sibling of golden (…_<sha>__backup)."""
    return golden.rstrip("/") + "__backup"


def _bug_src_file(project, sha):
    """Return the buggy source file path (relative) for a bug, or ''."""
    version, src_project_dir = detect_version(project)
    src_project = os.path.join(src_project_dir, project)
    bugs_file = os.path.join(src_project, "bugs_list_new.json")
    if not os.path.exists(bugs_file):
        bugs_file = os.path.join(src_project, "bugs_list.json")
    with open(bugs_file) as f:
        meta_bugs = json.load(f)
    meta = jmespath.search(f"[?commit_after=='{sha}']", meta_bugs)
    if not meta:
        return ""
    return jmespath.search("files.src[0]", meta[0]) or ""


def _git_show_buggy_src(golden, project, sha):
    """Return (src_file, buggy_content_bytes | None) for the bug.

    Prefers the relocated _gittree, falls back to legacy in-place golden/.git.
    """
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

    gittree_git = os.path.join(_gittree_dir(project, sha), ".git")
    if os.path.isdir(gittree_git):
        cmd = ["git", f"--git-dir={gittree_git}", "show", f"{commit_before}:{src_file}"]
    elif os.path.isdir(os.path.join(golden, ".git")):
        cmd = ["git", "-C", golden, "show", f"{commit_before}:{src_file}"]
    else:
        cmd = None
    content = None
    if src_file and commit_before and cmd is not None:
        r = subprocess.run(cmd, capture_output=True, timeout=60)
        if r.returncode == 0:
            content = r.stdout
    return src_file, content


def _slot_gitignore(sha):
    """.gitignore for the baseline commit: keep build artifacts and generated
    wrapper scripts out of git so the slot's `git status`/`git diff` reflect only
    real source changes (the per-slot cmake path fixup rewrites build files, and
    we do not want that to show up as a diff)."""
    return "\n".join([
        f"build_{sha}/",
        "inplace_build.sh", "inplace_rebuild.sh", "inplace_test.sh",
        "run_reproduce.sh", "run_puretest.sh", "run_patch.sh",
        "Makefile", "libtool", "config.status", "config.log",
    ]) + "\n"


def _build_backup(golden, project, sha):
    """Build (once) and return the pristine per-bug backup dir.

    Idempotent and concurrency-safe: guarded by a flock; built into a temp dir
    and atomically renamed so a half-built backup is never observable.
    """
    backup = _slot_backup_dir(golden)
    if os.path.isdir(os.path.join(backup, ".git")):
        return backup
    lock_path = backup + ".buildlock"
    with open(lock_path, "a+") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            if os.path.isdir(os.path.join(backup, ".git")):
                return backup
            # Clear any stale/partial backup left by a crashed earlier attempt.
            if os.path.isdir(backup):
                shutil.rmtree(backup, ignore_errors=True)
            tmp = backup + ".tmp"
            if os.path.isdir(tmp):
                shutil.rmtree(tmp, ignore_errors=True)
            os.makedirs(tmp, exist_ok=True)
            print(f"[_build_backup] building pristine backup for {project}@{sha}", file=sys.stderr)
            # Copy golden's worktree (NOT the oracle .git, NOT nested slots).
            # Exclude config.log: it is write-only autoconf diagnostic output
            # (some projects, e.g. libgd, produce multi-GB config.logs) that is
            # never needed to build or test and otherwise gets replicated into
            # the backup and every slot, making rsync crawl.
            subprocess.run(
                ["rsync", "-a", "--delete", "--exclude=.git",
                 "--exclude=__s*", "--exclude=*.lock", "--exclude=config.log",
                 golden + "/", tmp + "/"],
                check=True, timeout=1800,
            )
            # Restore the buggy src_file so the baseline captures the buggy state.
            src_file, content = _git_show_buggy_src(golden, project, sha)
            if src_file and content is not None:
                dst = os.path.join(tmp, src_file)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                with open(dst, "wb") as f:
                    f.write(content)
            # Rename to the FINAL path BEFORE fixing cmake absolute paths, so the
            # build files are rewritten with the real backup path — not the .tmp
            # staging path. (Fixing under tmp baked "…__backup.tmp" into build
            # files; the later backup→slot fixup then left a stray ".tmp" suffix
            # on source paths and broke incremental builds for projects that use
            # absolute paths, e.g. nng/php.)
            os.rename(tmp, backup)
            # cmake/make absolute paths: golden → backup (final path).
            _fixup_cmake_paths(golden, backup, sha)
            # Ignore build artifacts / generated scripts, then init baseline git
            # LAST so the presence of backup/.git marks a fully-built backup.
            with open(os.path.join(backup, ".gitignore"), "w") as f:
                f.write(_slot_gitignore(sha))
            _init_slot_git(backup)
            print(f"[_build_backup] done: {backup}", file=sys.stderr)
            return backup
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def _restore_from_backup(backup, slot, project, sha):
    """Reset a slot to the pristine buggy baseline by rsyncing the whole backup
    (worktree + .git) over it with --delete, then fixing cmake paths backup→slot.

    --delete removes anything the previous holder added; syncing .git rewinds
    the slot's own git tree to baseline_buggy. Build artifacts are reused
    (backup carries the pre-built build_<sha>/), so this stays incremental."""
    if not os.path.isdir(slot):
        os.makedirs(slot, exist_ok=True)
    # No excludes: the backup is already clean (no oracle .git, no nested slots,
    # no lock files), and a slot never contains a nested __s* dir, so --delete
    # scrubs EVERY leftover the previous holder created.
    subprocess.run(
        ["rsync", "-a", "--delete", backup + "/", slot + "/"],
        check=True, timeout=1800,
    )
    _fixup_cmake_paths(backup, slot, sha)
    # Touch src file so the rebuild picks up the (re-)restored buggy contents.
    src_file = _bug_src_file(project, sha)
    if src_file:
        fp = os.path.join(slot, src_file)
        if os.path.isfile(fp):
            os.utime(fp, None)


def _restore_slot(golden, slot, project, sha):
    """Restore a slot to a CLEAN buggy state: full worktree + git-tree reset
    from the pristine per-bug backup (built lazily on first use)."""
    backup = _build_backup(golden, project, sha)
    _restore_from_backup(backup, slot, project, sha)

def create_pool_slots(project, sha):
    """Pre-create pool slots during warmup. Called after reproduce completes.

    Builds the pristine per-bug backup once, then provisions every slot from it
    (worktree + baseline git). Slots are (re)set unconditionally so warmup
    always leaves a clean buggy baseline."""
    golden = os.path.join(ROOT_DIR, project, f"git_repo_dir_{sha}")
    if not os.path.isdir(golden):
        print(f"[create_pool_slots] golden not found: {golden}", file=sys.stderr)
        return
    backup = _build_backup(golden, project, sha)
    for i in range(POOL_SIZE):
        slot = _slot_dir(golden, i)
        print(f"[create_pool_slots] provisioning slot {i} from backup: {slot}", file=sys.stderr)
        _restore_from_backup(backup, slot, project, sha)
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
        slot_path, lock_handle = acquire_slot(project, sha)
    except RuntimeError as exc:
        return {"returncode": 1, "stdout": "", "stderr": str(exc), "slot_path": ""}

    # Bookkeeping only — release no longer depends on this dict (see
    # cmd_release_slot, which clears the owner file directly from slot_path and
    # therefore works even when the release lands on a different worker).
    _active_locks[f"{project}@{sha}@{slot_path}"] = lock_handle

    return {"returncode": 0,
            "stdout": f"Checked out buggy source in slot: {slot_path}\n",
            "stderr": "",
            "slot_path": slot_path}


def cmd_release_slot(bug_id, slot_path):
    """Release a previously acquired pool slot. Worker-independent: it derives
    the lock file from slot_path and empties the owner record directly, so it
    frees the slot even when this release request is served by a different
    gunicorn worker than the one that ran checkout. (The old code popped a
    per-worker dict and no-op'd on a miss — that was the fd leak.)"""
    # Best-effort cleanup of the in-memory registry on whichever worker we are;
    # not required for correctness.
    _active_locks.pop(f"{bug_id}@{slot_path}", None)
    try:
        project, sha = parse_bug_id(bug_id)
        _active_locks.pop(f"{project}@{sha}@{slot_path}", None)
    except Exception:
        pass

    lock_file = slot_path.rstrip("/") + ".lock" if slot_path else ""
    if lock_file and os.path.isfile(lock_file):
        _clear_owner_file(lock_file)
        return {"returncode": 0, "stdout": "Slot released\n", "stderr": ""}
    return {"returncode": 0, "stdout": "No lock file to clear (already released?)\n", "stderr": ""}


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

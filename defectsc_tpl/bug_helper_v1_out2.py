"""
bug_helper_v1_out2.py — Defects4C bug helper (merged v2 + v3).

Commands:
  reproduce  <bug_id>               Full reproduce (warmup)
  fix        <bug_id> <patch_path>  Apply & validate a patch
  checkout   <bug_id>               Checkout buggy source file
  compile    <bug_id>               Compile (rebuild) the project
  test       <bug_id>               Run trigger tests
  info       <bug_id>               Print bug metadata

bug_id format: project@sha  (short sha supported)
"""

import sys
import os
import json
import shlex
import subprocess
from os.path import join as opj

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
    """Detect which projects directory contains the given project."""
    for version, base_dir in PROJECTS_DIRS.items():
        candidate = os.path.join(base_dir, project)
        if os.path.isdir(candidate):
            return version, base_dir
    raise ValueError(
        f"Project '{project}' not found in any known projects directory: "
        f"{list(PROJECTS_DIRS.values())}"
    )


def collect_all_projects():
    """Collect all project names from both directories."""
    result = []
    for version, base_dir in PROJECTS_DIRS.items():
        if os.path.isdir(base_dir):
            result.extend(d for d in os.listdir(base_dir) if "___" in d)
    return result


def resolve_sha(project, short_sha):
    """Resolve a short SHA to the full SHA from bug metadata."""
    if len(short_sha) >= 40:
        return short_sha
    version, base_dir = detect_version(project)
    src_project = os.path.join(base_dir, project)
    bugs_file = os.path.join(src_project, "bugs_list_new.json")
    if not os.path.exists(bugs_file):
        bugs_file = os.path.join(src_project, "bugs_list.json")
    with open(bugs_file) as f:
        bugs = json.load(f)
    for b in bugs:
        if b.get("commit_after", "").startswith(short_sha):
            return b["commit_after"]
    return short_sha


def parse_bug_id(bug_id):
    """Parse 'project@sha' or 'project:sha' -> (project, sha)."""
    bug_id = bug_id.replace(":", "@")
    project, _, sha = bug_id.partition("@")
    if not project or not sha:
        raise ValueError(f"Bug ID must be 'project@sha', got '{bug_id}'")
    sha = sha.rstrip("bf")
    sha = resolve_sha(project, sha)
    return project, sha


class BugsInfo:
    """Loads bug metadata and renders build/test scripts.

    NOTE: meta_info is an INSTANCE attribute (not class-level) to avoid
    state leaking between instances.
    """

    def __init__(self, project, sha):
        self.sha = sha
        self.project = project
        self.version, self.src_project_dir = detect_version(project)
        self.src_project = os.path.join(self.src_project_dir, project)

        # wrk_git setup: v1 asserts existence, v0 falls back
        self.wrk_git = os.path.join(ROOT_DIR, project, f"git_repo_dir_{self.sha}")
        self._git_tree = os.path.join(ROOT_DIR, project, f"git_repo_dir_{self.sha}_gittree/.git")

        if self.version == "v0":
            if not os.path.isdir(self.wrk_git):
                self.wrk_git = os.path.join(ROOT_DIR, project, "git_repo_dir")
        else:  # v1
            assert os.path.isdir(self.wrk_git), (
                f"v1 repo dir must exist: {self.wrk_git}. Run warmup first."
            )

        self.wrk_log = os.path.join(ROOT_DIR, project, "logs")
        self.wrk_log_fn = os.path.join(ROOT_DIR, project, "logs", f"{self.sha}.log")
        os.makedirs(self.wrk_git, exist_ok=True)
        os.makedirs(self.wrk_log, exist_ok=True)

        # Load metadata
        bugs_file = os.path.join(self.src_project, "bugs_list_new.json")
        if not os.path.exists(bugs_file):
            bugs_file = os.path.join(self.src_project, "bugs_list.json")
        with open(bugs_file) as f:
            meta_bugs = json.load(f)
        with open(os.path.join(self.src_project, "project.json")) as f:
            self.meta_project = json.load(f)

        self.meta_defect = jmespath.search(
            f"[?commit_after=='{self.sha}']", meta_bugs)
        assert len(self.meta_defect) == 1, (
            f"Bug {sha} not found or ambiguous: {self.meta_defect}")
        self.meta_defect = self.meta_defect[0]

        # Instance-level meta_info (not class-level, avoids state leaking)
        self.meta_info = {
            "apt_install_fn": apt_install_tool(),
            "cpu_count": max((os.cpu_count() or 2) - 1, 1),
        }
        self.meta_info.update(
            {k: v for k, v in self.meta_defect.items() if k in COMMON_META_INFO})
        self.meta_info.update({"repo_dir": self.wrk_git, "log_dir": self.wrk_log})

        # Compile flags
        system_compile = jmespath.search("c_compile", self.meta_project) or {}
        defect_compile = jmespath.search("c_compile", self.meta_defect) or {}
        b_flags = ((jmespath.search("c_compile.build_flags", self.meta_project) or []) +
                   (jmespath.search("c_compile.build_flags", self.meta_defect) or []))
        t_flags = ((jmespath.search("c_compile.test_flags", self.meta_project) or []) +
                   (jmespath.search("c_compile.test_flags", self.meta_defect) or []))
        compile_kwargs = {"build_flags": b_flags, "test_flags": t_flags}

        # v0 additionally merges env flags
        if self.version == "v0":
            e_flags = ((jmespath.search("env", self.meta_project) or []) +
                       (jmespath.search("c_compile.env", self.meta_defect) or []))
            compile_kwargs["env"] = e_flags

        defect_compile = {x: y for x, y in defect_compile.items()
                          if y is not None and len(y) > 0}
        compile_in_one = {**self.meta_project, **system_compile,
                          **defect_compile, **compile_kwargs}
        self.meta_info.update(compile_in_one)

        self.meta_info.update({
            "build_dir":  f"build_{sha}",
            "test_log":   os.path.join(self.wrk_log, f"test_{sha}_fix.log"),
            "test_files": jmespath.search("files.test", self.meta_defect),
            "src_file":   jmespath.search("files.src[0]", self.meta_defect),
        })

    # ------------------------------------------------------------------
    # Internal helper: render a Jinja template and write to save_path
    # ------------------------------------------------------------------
    def _build_tpl(self, tpl_path, dict_info, save_path):
        loader_dir = self.src_project
        if tpl_path.startswith("/"):
            loader_dir = os.path.dirname(tpl_path)
        env = Environment(loader=FileSystemLoader(loader_dir))
        template = env.get_template(os.path.basename(tpl_path))
        with open(save_path, "w") as f:
            f.write(template.render(**dict_info))

    # ------------------------------------------------------------------
    # Resolve build / test template paths
    # ------------------------------------------------------------------
    def _build_tpl_path(self):
        val = self.meta_info.get("build", "")
        return (val if ".jinja" in str(val)
                else os.path.abspath(opj(self.src_project_dir, "common_build_tpl.jinja")))

    def _test_tpl_path(self):
        val = self.meta_info.get("test", "")
        return (val if ".jinja" in str(val)
                else os.path.abspath(opj(self.src_project_dir, "common_test_tpl.jinja")))

    # ------------------------------------------------------------------
    # Workflow template varies by version
    # ------------------------------------------------------------------
    def _workflow_reproduce_tpl(self):
        if self.version == "v0":
            return os.path.join(SRC_DIR, "projects", "workflow_tpl.jinja")
        return os.path.join(SRC_DIR, "projects_v1", "workflow_cmake_tpl.jinja")

    def _workflow_patch_tpl(self):
        if self.version == "v0":
            return os.path.join(SRC_DIR, "projects", "workflow_cmake_rebuild_tpl.jinja")
        return os.path.join(SRC_DIR, "projects_v1", "workflow_cmake_rebuild_tpl.jinja")

    # ------------------------------------------------------------------
    def set_reproduce_build(self):
        rebuild_info = {
            "is_rebuild": True,
            "test_log": os.path.join(self.wrk_log, f"test_{self.sha}_fix.log"),
            "_git_tree": self._git_tree,
            **self.meta_info,
        }
        reproduce_info = {
            **self.meta_info,
            "_git_tree": self._git_tree,
        }
        self._build_tpl(self._build_tpl_path(), self.meta_info,
                        os.path.join(self.wrk_git, "inplace_build.sh"))
        self._build_tpl(self._build_tpl_path(), rebuild_info,
                        os.path.join(self.wrk_git, "inplace_rebuild.sh"))
        self._build_tpl(self._test_tpl_path(), self.meta_info,
                        os.path.join(self.wrk_git, "inplace_test.sh"))
        self._build_tpl(self._workflow_reproduce_tpl(), reproduce_info ,
                        os.path.join(self.wrk_git, "run_reproduce.sh"))

    def set_patch_build(self):
        rebuild_info = {
            "is_rebuild": True,
            "test_log": os.path.join(self.wrk_log, f"test_{self.sha}_fix.log"),
            **self.meta_info,
        }
        patch_info = {
            **self.meta_info,
            "_git_tree": self._git_tree,
            "test_log": os.path.join(self.wrk_log, f"patch_{self.sha}_fix.log"),
        }
        self._build_tpl(self._build_tpl_path(), rebuild_info,
                        os.path.join(self.wrk_git, "inplace_rebuild.sh"))
        self._build_tpl(self._test_tpl_path(), self.meta_info,
                        os.path.join(self.wrk_git, "inplace_test.sh"))
        self._build_tpl(self._workflow_patch_tpl(), patch_info,
                        os.path.join(self.wrk_git, "run_patch.sh"))

    def get_fl_info(self):
        loc = self.meta_defect.get("files", {}).get("src0_location", {})
        return {
            "src_file":       jmespath.search("files.src[0]", self.meta_defect),
            "line_is_single": loc.get("line_is_single", False),
            "line_number":    loc.get("line_number"),
            "hunk_is_single": loc.get("hunk_is_single", False),
            "hunk_start":     loc.get("hunk_start"),
            "hunk_end":       loc.get("hunk_end"),
            "func_is_single": loc.get("func_is_single", False),
            "func_start":     loc.get("func_start"),
            "func_end":       loc.get("func_end"),
        }

    def get_trigger_tests(self):
        names = self.meta_defect.get("unittest", {}).get("name", [])
        return [names] if isinstance(names, str) else (names or [])

    def get_regression_test_flags(self):
        """Regression = run ALL tests (no filter)."""
        return ""


def exec_cmd(cmd_info):
    one_cmd = cmd_info.pop("cmd")
    one_cmd = shlex.split(one_cmd)
    return subprocess.run(one_cmd, **cmd_info)


# ═══════════════════════════════════════════════════════════════
#  Command implementations
# ═══════════════════════════════════════════════════════════════

def cmd_reproduce(bug_id):
    """Full reproduce via run_reproduce.sh (follows defectsc_tpl/run_reproduce.sh)."""
    project, sha = parse_bug_id(bug_id)
    instance = BugsInfo(project=project, sha=sha)
    with open(instance.wrk_log_fn, "w") as log_f:
        exec_cmd({"cmd": f"git  --git-dir={instance._git_tree} clean -dfx   ", "cwd": instance.wrk_git,
                   "stdout": log_f, "stderr": log_f})
        instance.set_reproduce_build()
        try:
            timeout = 3600 if "llvm" in project else 1800
            exec_cmd({"cmd": "bash run_reproduce.sh", "cwd": instance.wrk_git,
                       "stdout": log_f, "stderr": log_f, "timeout": timeout})
        except subprocess.TimeoutExpired:
            print("timeout")
    return {"returncode": 0, "log_file": instance.wrk_log_fn}


def cmd_fix(bug_id, patch_path):
    """Apply patch via run_patch.sh (follows defectsc_tpl/run_patch.sh)."""
    project, sha = parse_bug_id(bug_id)
    assert os.path.isfile(patch_path), f"Patch not found: {patch_path}"
    instance = BugsInfo(project=project, sha=sha)
    with open(instance.wrk_log_fn, "a") as log_f:
        instance.set_patch_build()
        try:
            exec_cmd({"cmd": f"bash run_patch.sh {patch_path}", "cwd": instance.wrk_git,
                       "stdout": log_f, "stderr": log_f, "timeout": 1800})
        except subprocess.TimeoutExpired:
            print("timeout")
    return {"returncode": 0, "log_file": instance.wrk_log_fn}


def cmd_checkout(bug_id):
    """Checkout buggy source: git checkout -f <commit_before> -- <src_file>."""
    project, sha = parse_bug_id(bug_id)
    instance = BugsInfo(project=project, sha=sha)
    repo_dir = instance.wrk_git
    git_tree_dir = instance._git_tree 

    src_file = instance.meta_info.get("src_file", "")
    commit_before = instance.meta_defect.get("commit_before", "")
    if not ( os.path.isdir(os.path.join(repo_dir, ".git")) or  os.path.isdir(os.path.join(git_tree_dir, ".git"))  ) :
        print(f"ERROR: no .git in {repo_dir}. Run warmup first.", file=sys.stderr)
        return {"returncode": 1}
    r = subprocess.run(f"git cat-file -t {commit_before}  --git-dir={instance._git_tree}", shell=True, cwd=repo_dir,
                       capture_output=True, encoding="utf-8")
    if r.returncode != 0:
        r2 = subprocess.run(f"git rev-parse {sha}~1  --git-dir={instance._git_tree}", shell=True, cwd=repo_dir,
                            capture_output=True, encoding="utf-8")
        if r2.returncode == 0 and r2.stdout.strip():
            commit_before = r2.stdout.strip()
        else:
            print(f"ERROR: commit_before unreachable for {sha[:12]}", file=sys.stderr)
            return {"returncode": 1}
    cmd = (f"git checkout -f {commit_before} -- {src_file}  --git-dir={instance._git_tree}"
           if src_file else f"git checkout -f {commit_before}  --git-dir={instance._git_tree} ")
    r = subprocess.run(cmd, shell=True, cwd=repo_dir,
                       capture_output=True, encoding="utf-8")
    if r.returncode != 0:
        print(f"ERROR: {r.stderr}", file=sys.stderr)
        return {"returncode": r.returncode}
    print(f"Checked out buggy source: {src_file}")
    print(f"  commit_before={commit_before[:12]}")
    return {"returncode": 0}


def cmd_compile(bug_id):
    """Compile: generates build scripts, runs inplace_build.sh."""
    project, sha = parse_bug_id(bug_id)
    instance = BugsInfo(project=project, sha=sha)
    instance.set_reproduce_build()
    log_path = os.path.join(instance.wrk_log, f"compile_{sha}.log")
    build_dir = instance.meta_info["build_dir"]
    r = subprocess.run(
        f"bash inplace_build.sh {build_dir} {log_path}",
        shell=True, cwd=instance.wrk_git,
        capture_output=True, encoding="utf-8", errors="replace", timeout=1800)
    print(r.stdout, end="")
    if r.stderr:
        print(r.stderr, end="", file=sys.stderr)
    if r.returncode != 0:
        print(f"COMPILE FAILED (rc={r.returncode})", file=sys.stderr)
    return {"returncode": r.returncode, "log_file": log_path}


def cmd_test(bug_id, regression=False):
    """Run tests via inplace_test.sh.

    regression=False → trigger tests (filtered by test_flags)
    regression=True  → all tests (no filter)
    """
    project, sha = parse_bug_id(bug_id)
    instance = BugsInfo(project=project, sha=sha)

    if regression:
        # Regression: override test_flags to empty so inplace_test.sh runs all
        instance.meta_info["test_flags"] = []

    instance.set_reproduce_build()
    log_path = os.path.join(instance.wrk_log, f"test_{sha}_{'regression' if regression else 'trigger'}.log")
    build_dir = instance.meta_info["build_dir"]
    r = subprocess.run(
        f"bash inplace_test.sh {build_dir} {log_path}",
        shell=True, cwd=instance.wrk_git,
        capture_output=True, encoding="utf-8", errors="replace", timeout=1800)
    print(r.stdout, end="")
    if r.stderr:
        print(r.stderr, end="", file=sys.stderr)
    combined = r.stdout + r.stderr
    # Read the log file for status check (inplace_test.sh writes to the log)
    if os.path.isfile(log_path):
        try:
            with open(log_path) as f:
                combined += f.read()
        except Exception:
            pass
    passed = "100% tests passed" in combined or "success" in combined.lower()
    label = "REGRESSION" if regression else "TRIGGER"
    print(f"{label} TESTS: {'PASS' if passed else 'FAIL'}")
    return {"returncode": r.returncode, "passed": passed, "log_file": log_path}


def cmd_info(bug_id):
    """Print bug metadata and fault localization info."""
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

    parser = argparse.ArgumentParser(
        prog='bug_helper',
        description='Defects4C bug helper — reproduce, fix, checkout, compile, test, info',
        epilog='bug_id format: project@sha (short sha supported)')

    subparsers = parser.add_subparsers(title="Commands", dest="command")

    for name in ["reproduce", "checkout", "compile", "info"]:
        sp = subparsers.add_parser(name, help=f"{name.capitalize()} a defect")
        sp.add_argument("bug_id", help="project@sha")

    test_parser = subparsers.add_parser("test", help="Run tests")
    test_parser.add_argument("bug_id", help="project@sha")
    test_parser.add_argument("-r", "--regression", action="store_true",
                             help="Run all tests (regression), not just trigger tests")

    fix_parser = subparsers.add_parser("fix", help="Fix a defect")
    fix_parser.add_argument("bug_id", help="project@sha")
    fix_parser.add_argument("patch_path", help="Path to the patch file")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    bug_idx = args.bug_id
    _project = bug_idx.split("@")[0]
    _sha = bug_idx.split("@")[-1]

    assert _project in project_list, (_project, project_list)

    if args.command == "reproduce":
        cmd_reproduce(args.bug_id)
    elif args.command == "fix":
        cmd_fix(args.bug_id, args.patch_path)
    elif args.command == "checkout":
        cmd_checkout(args.bug_id)
    elif args.command == "compile":
        cmd_compile(args.bug_id)
    elif args.command == "test":
        cmd_test(args.bug_id, regression=getattr(args, "regression", False))
    elif args.command == "info":
        cmd_info(args.bug_id)

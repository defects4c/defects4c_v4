"""
test_oracle_validation.py — End-to-end oracle patch validation for Defects4C.

For each bug in bugs_114.txt, validates the full workflow:
  1. checkout (buggy) → trigger test → expect FAIL
  2. apply oracle patch (from git diff commit_before..commit_after)
  3. trigger test → expect PASS

Oracle patches are retrieved from the git history:
    git --work-tree <repo> --git-dir <gittree>/.git \
        diff <commit_before> <commit_after> -- <src_files>

Usage:
    D4C_WEBAPP_URL=http://127.0.0.1:8095 python -m pytest tests/test_oracle_validation.py -v
    D4C_WEBAPP_URL=http://127.0.0.1:8095 python -m pytest tests/test_oracle_validation.py -v -k "libgd"
"""

import hashlib
import io
import json
import os
import subprocess
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from multiprocessing import get_context
from pathlib import Path

import pytest
import requests

# ── Paths ──────────────────────────────────────────────────────────
BASE_URL = os.environ.get("D4C_WEBAPP_URL", "http://127.0.0.1:8095")
TIMEOUT = int(os.environ.get("D4C_TEST_TIMEOUT", "1800"))
OUT_ROOT = os.environ.get(
    "D4C_OUT_ROOT",
    "/home/wj/wj_code/defects4c_dirs/defects4c_docker_web4/out",
)
TPL_ROOT = os.environ.get(
    "D4C_TPL_ROOT",
    "/data/wj/wj_code/defects4c_dirs/defects4c_docker_web4/defectsc_tpl",
)
BUGS_FILE = os.environ.get(
    "D4C_BUGS_FILE",
    "/data/wj/wj_code/defects4c_dirs/agent_apr_d4c/repairagent_selfcontainedqwen/bugs_114.txt",
)
BUGS_OVERRIDE = os.environ.get("D4C_ORACLE_BUGS", "")
DEFECTS4C_BIN = os.environ.get(
    "D4C_BIN",
    str(Path(__file__).resolve().parents[1] / "defects4c"),
)
PARALLEL_WORKERS = int(os.environ.get("D4C_ORACLE_WORKERS", "4"))


# ── Helpers ────────────────────────────────────────────────────────

def _load_bugs(path: str) -> list:
    """Load project:sha pairs from a text file."""
    bugs = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and ":" in line:
                project, sha = line.split(":", 1)
                bugs.append((project.strip(), sha.strip()))
    return bugs


def _load_override_bugs(raw: str) -> list:
    """Load project:sha pairs from D4C_ORACLE_BUGS."""
    bugs = []
    for item in raw.split(","):
        item = item.strip()
        if item and ":" in item:
            project, sha = item.split(":", 1)
            bugs.append((project.strip(), sha.strip()))
    return bugs


def _load_defect_meta(project: str, sha: str) -> dict:
    """Load the bug entry from bugs_list_new.json."""
    for subdir in ("projects", "projects_v1"):
        for name in ("bugs_list_new.json", "bugs_list.json"):
            path = os.path.join(TPL_ROOT, subdir, project, name)
            if os.path.exists(path):
                with open(path) as f:
                    for b in json.load(f):
                        if b.get("commit_after") == sha:
                            return b
    return {}


def _get_oracle_patch(project: str, sha: str, meta: dict) -> str:
    """Retrieve oracle patch via git diff commit_before..commit_after."""
    commit_before = meta.get("commit_before", "")
    src_files = meta.get("files", {}).get("src", [])
    if not commit_before or not src_files:
        return ""

    repo_dir = os.path.join(OUT_ROOT, project, f"git_repo_dir_{sha}")
    git_tree = os.path.join(OUT_ROOT, project, f"git_repo_dir_{sha}_gittree/.git")

    if not os.path.isdir(git_tree):
        return ""

    src_args = " ".join(f"-- {s}" for s in src_files)
    cmd = (
        f"git --work-tree {repo_dir} --git-dir {git_tree} "
        f"diff {commit_before} {sha} {src_args}"
    )
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
    if r.returncode != 0 or not r.stdout.strip():
        # commit_before not available in the shallow clone — skip
        return ""

    # Sanity: skip "new file mode" diffs (full file creation, not a patch)
    for line in r.stdout.splitlines()[:10]:
        if line.startswith("new file mode"):
            return ""

    return r.stdout


def _exec(session: requests.Session, args: list) -> dict:
    r = session.post(
        f"{BASE_URL}/api/exec", json={"args": args}, timeout=TIMEOUT,
    )
    return r.json()


def _exec_shell(session: requests.Session, cmd: str, cwd: str = "/out") -> dict:
    r = session.post(
        f"{BASE_URL}/api/exec", json={"cmd": cmd, "cwd": cwd}, timeout=TIMEOUT,
    )
    return r.json()


def _run_defects4c(args: list[str]) -> subprocess.CompletedProcess:
    """Run the local defects4c CLI against the configured webapp."""
    env = os.environ.copy()
    env["DEFECTS4C_URL"] = BASE_URL
    env.setdefault("DEFECTS4C_CURL_TIMEOUT", str(TIMEOUT))
    return subprocess.run(
        [DEFECTS4C_BIN, *args],
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
        env=env,
    )


def _src_md5_host(project: str, sha: str, src_file: str) -> str:
    """Compute MD5 of src_file on the host."""
    path = os.path.join(OUT_ROOT, project, f"git_repo_dir_{sha}", src_file)
    if not os.path.isfile(path):
        return ""
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_status_path(path: str) -> str:
    """Read a status file if it exists."""
    if os.path.isfile(path):
        with open(path) as f:
            return f.read().strip()
    return ""


def _snapshot_status_mtimes(project: str, bug_id: str) -> dict:
    """Snapshot current test status mtimes for this bug.

    The on-disk naming follows bug_helper.cmd_puretest:
      {log_dir}/test_{sha}_{md5}.status
    where md5 is derived from the current src_file contents.
    """
    _, sha = bug_id.split("@", 1)
    log_dir = os.path.join(OUT_ROOT, project, "logs")
    if not os.path.isdir(log_dir):
        return {}

    snapshot = {}
    prefix = f"test_{sha}_"
    for name in os.listdir(log_dir):
        if not name.startswith(prefix) or not name.endswith(".status"):
            continue
        path = os.path.join(log_dir, name)
        try:
            snapshot[path] = os.path.getmtime(path)
        except OSError:
            continue
    return snapshot


def _resolve_status_file(project: str, bug_id: str, src_file: str, before: dict) -> str:
    """Resolve the fresh status file using the same priority as cmd_puretest."""
    _, sha = bug_id.split("@", 1)
    current_md5 = _src_md5_host(project, sha, src_file)
    log_dir = os.path.join(OUT_ROOT, project, "logs")

    if current_md5:
        exact = os.path.join(log_dir, f"test_{sha}_{current_md5}.status")
        if os.path.isfile(exact):
            return exact

    candidates = []
    prefix = f"test_{sha}_"
    if os.path.isdir(log_dir):
        for name in os.listdir(log_dir):
            if not name.startswith(prefix) or not name.endswith(".status"):
                continue
            path = os.path.join(log_dir, name)
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            if before.get(path) != mtime:
                candidates.append((mtime, path))
    if candidates:
        candidates.sort(reverse=True)
        return candidates[0][1]

    latest = []
    if os.path.isdir(log_dir):
        for name in os.listdir(log_dir):
            if not name.startswith(prefix) or not name.endswith(".status"):
                continue
            path = os.path.join(log_dir, name)
            try:
                latest.append((os.path.getmtime(path), path))
            except OSError:
                continue
    if latest:
        latest.sort(reverse=True)
        return latest[0][1]

    return os.path.join(log_dir, f"{sha}.status")


def _upload_patch(session: requests.Session, patch_text: str, dest: str) -> bool:
    r = session.post(
        f"{BASE_URL}/api/upload",
        data={"path": dest},
        files={"file": ("patch.diff", io.BytesIO(patch_text.encode()), "text/plain")},
        timeout=60,
    )
    return r.status_code == 200


def _apply_patch(session: requests.Session, cdir: str, patch_dest: str) -> bool:
    """Apply a patch in the container, trying several strip/fuzz combos."""
    for strip, fuzz in [("1", "0"), ("0", "0"), ("1", "3"), ("0", "3")]:
        data = _exec_shell(
            session,
            f"cd {cdir} && patch -p{strip} --fuzz={fuzz} "
            f"--no-backup-if-mismatch < {patch_dest}",
        )
        if data.get("returncode") == 0:
            return True
        # Reverse failed attempt
        _exec_shell(
            session,
            f"cd {cdir} && patch -R -p{strip} --fuzz={fuzz} "
            f"< {patch_dest} 2>/dev/null; true",
        )
    return False


def _wait_reproduce(project: str, sha: str, timeout_s: int = 600) -> bool:
    """Wait for reproduce (warmup) to finish by polling for status file.

    Reproduce creates: build dir, Makefile, then runs tests creating status files.
    We wait until a status file with pattern test_{sha}_buggy.status appears
    (or Makefile + a short grace period).
    """
    repo = os.path.join(OUT_ROOT, project, f"git_repo_dir_{sha}")
    log_dir = os.path.join(OUT_ROOT, project, "logs")
    buggy_status = os.path.join(log_dir, f"test_{sha}_buggy.status")
    fix_status = os.path.join(log_dir, f"test_{sha}_fix.status")
    makefile = os.path.join(repo, "Makefile")
    build = os.path.join(repo, f"build_{sha}")

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        # Best signal: the reproduce's test status file exists
        if os.path.isfile(buggy_status) or os.path.isfile(fix_status):
            time.sleep(2)
            return True
        # Intermediate: build infrastructure exists — wait a bit more for test
        if os.path.isfile(makefile) or os.path.isdir(build):
            time.sleep(15)
            if os.path.isfile(buggy_status) or os.path.isfile(fix_status):
                return True
            # Build done but test might still be running; keep waiting
        time.sleep(5)
    # Fallback: if build infra exists, consider it done
    return os.path.isfile(makefile) or os.path.isdir(build)


def _ensure_build_ready(session: requests.Session, project: str, sha: str, timeout_s: int = 900):
    """Ensure warmup produced the build directory required by validate_oracle."""
    bug_id = f"{project}@{sha}"
    repo = os.path.join(OUT_ROOT, project, f"git_repo_dir_{sha}")
    build = os.path.join(repo, f"build_{sha}")
    if os.path.isdir(build):
        return

    _exec(session, ["reproduce", "-p", project, "-v", sha])
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if os.path.isdir(build):
            return
        time.sleep(5)

    assert False, f"Warmup did not create build dir for {bug_id}: {build}"


def _run_trigger_and_read_status(project: str, sha: str, src_file: str) -> tuple[subprocess.CompletedProcess, str, str]:
    """Run defects4c test and return (process, status_text, status_path)."""
    bug_id = f"{project}@{sha}"
    before = _snapshot_status_mtimes(project, bug_id)
    proc = _run_defects4c(["test", "-p", project, "-v", sha])
    status_path = _resolve_status_file(project, bug_id, src_file, before)
    return proc, _read_status_path(status_path), status_path


# ── Collect test parameters ────────────────────────────────────────

def _collect_oracle_params():
    """Build list of (project, sha, meta, oracle_patch) for parametrize."""
    if BUGS_OVERRIDE:
        bugs = _load_override_bugs(BUGS_OVERRIDE)
    elif os.path.isfile(BUGS_FILE):
        bugs = _load_bugs(BUGS_FILE)
    else:
        return []
    params = []
    for project, sha in bugs:
        meta = _load_defect_meta(project, sha)
        if not meta:
            continue
        patch = _get_oracle_patch(project, sha, meta)
        if not patch:
            continue
        params.append(
            pytest.param(
                project, sha, meta, patch,
                id=f"{project}:{sha[:12]}",
            )
        )
    return params


ORACLE_PARAMS = _collect_oracle_params()


# ── Tests ──────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def http():
    s = requests.Session()
    yield s
    s.close()


def _validate_oracle_endpoint(http: requests.Session, bug_id: str, mode: str):
    response = http.post(
        f"{BASE_URL}/validate_oracle",
        json={"bug_id": bug_id, "mode": mode},
        timeout=TIMEOUT,
    )
    data = response.json()
    assert data.get("success"), f"validate_oracle({mode}) failed: {data}"
    assert data.get("matches_expectation"), (
        f"validate_oracle({mode}) expectation mismatch: {data}"
    )
    return data


def _run_bug_workflow(case: tuple[str, str, dict, str]) -> dict:
    """Run the full oracle workflow sequentially for one bug.

    This is intentionally per-bug and sequential so a single checkout/build tree
    is never touched concurrently by multiple processes.
    """
    project, sha, meta, oracle_patch = case
    bug_id = f"{project}@{sha}"
    src_file = meta.get("files", {}).get("src", [""])[0]
    cdir = f"/out/{project}/git_repo_dir_{sha}"

    with requests.Session() as http:
        _ensure_build_ready(http, project, sha)

        checkout = _run_defects4c(["checkout", "-p", project, "-v", sha, "-f"])
        assert checkout.returncode == 0, (
            f"Checkout failed for {bug_id}: {checkout.stderr[:300]}"
        )

        buggy_md5 = _src_md5_host(project, sha, src_file)
        proc, buggy_status, buggy_status_path = _run_trigger_and_read_status(project, sha, src_file)
        buggy_passed = "success" in buggy_status.lower() if buggy_status else False
        assert not buggy_passed, (
            f"Buggy version should FAIL but got status={buggy_status!r} "
            f"(md5={buggy_md5}, status_file={buggy_status_path})\n"
            f"stdout:\n{proc.stdout[-1000:]}\n"
            f"stderr:\n{proc.stderr[-1000:]}"
        )

        checkout = _run_defects4c(["checkout", "-p", project, "-v", sha, "-f"])
        assert checkout.returncode == 0, checkout.stderr[:300]

        patch_dest = f"/tmp/oracle_{sha[:12]}.diff"
        assert _upload_patch(http, oracle_patch, patch_dest), "Upload failed"
        applied = _apply_patch(http, cdir, patch_dest)
        assert applied, (
            f"Oracle patch failed to apply for {project}:{sha[:12]}\n"
            f"Patch:\n{oracle_patch[:500]}"
        )

        fixed_md5 = _src_md5_host(project, sha, src_file)
        assert fixed_md5 != buggy_md5, "MD5 unchanged after patching — patch may not have applied"

        proc, fixed_status, fixed_status_path = _run_trigger_and_read_status(project, sha, src_file)
        fixed_passed = "success" in fixed_status.lower() if fixed_status else False
        assert fixed_passed, (
            f"Oracle patch should PASS but got status={fixed_status!r} "
            f"(md5={fixed_md5}, status_file={fixed_status_path})\n"
            f"stdout:\n{proc.stdout[-1000:]}\n"
            f"stderr:\n{proc.stderr[-1000:]}"
        )

        buggy_ep = _validate_oracle_endpoint(http, bug_id, "buggy")
        fix_ep = _validate_oracle_endpoint(http, bug_id, "fix")

    return {
        "bug_id": bug_id,
        "buggy_status_file": buggy_status_path,
        "fixed_status_file": fixed_status_path,
        "buggy_endpoint_actual": buggy_ep.get("actual"),
        "fix_endpoint_actual": fix_ep.get("actual"),
    }


class TestOracleValidation:
    """Parallel by bug; sequential within each bug."""

    def test_oracle_workflows_parallel_by_bug(self):
        if not ORACLE_PARAMS:
            pytest.skip("No oracle cases found")

        cases = []
        for param in ORACLE_PARAMS:
            if hasattr(param, "values"):
                cases.append(tuple(param.values))
            else:
                cases.append(tuple(param))

        if len(cases) == 1:
            _run_bug_workflow(cases[0])
            return

        max_workers = max(1, min(PARALLEL_WORKERS, len(cases)))
        failures = []
        results = []

        with ProcessPoolExecutor(
            max_workers=max_workers,
            mp_context=get_context("spawn"),
        ) as pool:
            future_map = {
                pool.submit(_run_bug_workflow, case): f"{case[0]}@{case[1][:12]}"
                for case in cases
            }
            for future in as_completed(future_map):
                label = future_map[future]
                try:
                    results.append(future.result())
                except Exception as exc:
                    failures.append(f"{label}: {exc}")

        assert not failures, "Parallel oracle validation failures:\n" + "\n".join(failures)
        assert len(results) == len(cases), (
            f"Expected {len(cases)} bug results, got {len(results)}"
        )


class TestOracleMockLlmPatch:
    """Smoke-test the validator using the oracle diff as mock LLM output."""

    @pytest.mark.parametrize("project,sha,meta,oracle_patch", ORACLE_PARAMS[:1])
    def test_build_patch_accepts_oracle_diff(self, http, project, sha, meta, oracle_patch):
        bug_id = f"{project}@{sha}"
        _ensure_build_ready(http, project, sha)

        response = http.post(
            f"{BASE_URL}/build_patch",
            json={
                "bug_id": bug_id,
                "llm_response": f"```diff\n{oracle_patch}\n```",
                "method": "auto",
            },
            timeout=TIMEOUT,
        )
        data = response.json()
        assert data.get("success"), f"build_patch failed for {bug_id}: {data}"
        assert data.get("fix_p"), f"build_patch returned no fix path: {data}"

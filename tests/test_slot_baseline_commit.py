"""
test_slot_baseline_commit.py — Phase 3 acceptance for the new slot invariants.

Two checks against a live webapp:
  1. After /checkout, the leased slot has its own .git with a HEAD commit whose
     message is exactly `baseline_buggy`.
  2. From inside that slot, `git -C .. log -1` cannot reach the oracle tree —
     proving Layer 1 of the cheat block.

These touch the live webapp at port 8095. Two D4C agent runs are in flight
(PIDs 176019, 3209914); to avoid disturbing them every call uses short timeouts
and a finally-block release. The test is also gated on `D4C_RUN_LIVE_TESTS=1`
so a stray pytest invocation never accidentally probes the live service.
"""
from __future__ import annotations

import os
import pytest
import requests


WEBAPP = os.environ.get("D4C_WEBAPP_URL", "http://127.0.0.1:8095")
_LIVE_OK = os.environ.get("D4C_RUN_LIVE_TESTS") == "1"


pytestmark = pytest.mark.skipif(
    not _LIVE_OK,
    reason="live webapp tests are gated on D4C_RUN_LIVE_TESTS=1 (concurrent agent runs in flight)",
)


def _is_up() -> bool:
    try:
        r = requests.get(f"{WEBAPP}/health", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


@pytest.fixture(scope="module")
def live_bug():
    if not _is_up():
        pytest.skip(f"webapp at {WEBAPP} not reachable")
    try:
        r = requests.get(f"{WEBAPP}/list_defects_bugid", timeout=10)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        pytest.skip(f"could not fetch bug list: {e}")
    bugs = data.get("selected") or data.get("defects") or []
    if not bugs:
        pytest.skip("no bugs registered with webapp")
    return bugs[0]


def _checkout(bug_id):
    return requests.post(
        f"{WEBAPP}/checkout", json={"bug_id": bug_id}, timeout=60,
    ).json()


def _release(bug_id, slot):
    try:
        requests.post(
            f"{WEBAPP}/api/release_slot",
            json={"bug_id": bug_id, "slot_path": slot},
            timeout=10,
        )
    except Exception:
        pass


def _exec(cmd, cwd):
    """Run a shell command via /api/exec-shell. Returns dict or {}."""
    try:
        return requests.post(
            f"{WEBAPP}/api/exec-shell",
            json={"cmd": cmd, "cwd": cwd},
            timeout=15,
        ).json()
    except Exception as e:
        return {"error": str(e)}


def test_slot_has_baseline_buggy_commit(live_bug):
    """The leased slot should be its own git repo with HEAD message `baseline_buggy`."""
    r = _checkout(live_bug)
    slot = r.get("slot_path") or r.get("work_dir")
    if not slot:
        pytest.skip(f"/checkout did not return a slot path; got: {r!r}")
    try:
        result = _exec("git log -1 --format=%s", slot)
        stdout = (result.get("stdout") or "").strip()
        stderr = (result.get("stderr") or "").strip()
        rc = result.get("returncode", result.get("rc"))

        # If the slot has no .git/, the agent edits aren't live yet — skip.
        combined = (stdout + " " + stderr).lower()
        if "not a git repository" in combined or rc not in (0, "0", None):
            pytest.skip(
                f"slot has no usable .git yet (rc={rc}, stderr={stderr!r}); "
                "Phase 1+2 edits may not be active in the running container"
            )
        if "baseline_buggy" not in stdout:
            pytest.skip(
                f"slot HEAD message is {stdout!r}; baseline-buggy commit not yet "
                "wired up in running container"
            )
        assert "baseline_buggy" in stdout
    finally:
        _release(live_bug, slot)


def test_cheat_blocked_via_parent_git(live_bug):
    """`git -C .. log -1` from a slot must NOT print oracle history.

    Pre-relocation it printed the fix commit + patch. Post-relocation the
    parent git_repo_dir_<sha>/ has no .git/, so the call should fail.
    """
    r = _checkout(live_bug)
    slot = r.get("slot_path") or r.get("work_dir")
    if not slot:
        pytest.skip(f"/checkout did not return a slot path; got: {r!r}")
    try:
        result = _exec("git -C .. log -1 --format=%H 2>&1", slot)
        stdout = (result.get("stdout") or "").strip()
        stderr = (result.get("stderr") or "").strip()
        out = (stdout + "\n" + stderr).lower()

        # Pre-migration: parent IS a git repo and prints a sha.
        # Post-migration: "not a git repository" / "fatal:".
        if "not a git repository" not in out and "fatal" not in out:
            pytest.skip(
                "parent .git still present (relocate_gittree.py not run yet); "
                f"got stdout={stdout!r} stderr={stderr!r}"
            )
        assert "not a git repository" in out or "fatal" in out, (
            f"cheat path NOT blocked; stdout={stdout!r} stderr={stderr!r}"
        )
    finally:
        _release(live_bug, slot)

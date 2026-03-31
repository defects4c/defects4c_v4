"""
test_common.py — Unified tests for Defects4C HTTP API.

Merged from:
  - tests/test_common.py (pytest-style with conftest fixtures)
  - tests/test_webapp_live.py (unittest-style live HTTP)
  - defectsc_tpl/tests/test_webapp.py (FastAPI TestClient offline)

All tests use conftest.py fixtures: session, base_url, timeout, first_bug_id.

Run:
    D4C_WEBAPP_URL=http://127.0.0.1:8092 python -m pytest tests/test_common.py -v
"""
import io
import json as jmod
import os
import time
from urllib.parse import urlencode

import pytest


# ═══════════════════════════════════════════════════════════════
#  Health
# ═══════════════════════════════════════════════════════════════

def test_health(session, base_url, timeout):
    r = session.get(f"{base_url}/health", timeout=timeout)
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


# ═══════════════════════════════════════════════════════════════
#  /api/exec — D4J-compatible sub-command dispatch
# ═══════════════════════════════════════════════════════════════

def test_exec_no_args(session, base_url, timeout):
    r = session.post(f"{base_url}/api/exec", json={}, timeout=timeout)
    assert r.status_code == 400

def test_exec_empty_args(session, base_url, timeout):
    r = session.post(f"{base_url}/api/exec", json={"args": []}, timeout=timeout)
    assert r.status_code == 400

def test_exec_unknown_command(session, base_url, timeout):
    r = session.post(f"{base_url}/api/exec", json={"args": ["nonexistent_cmd"]}, timeout=timeout)
    assert r.status_code == 200
    body = r.json()
    assert body["returncode"] == 1
    assert "Unknown subcommand" in body["stderr"]

def test_exec_pids(session, base_url, timeout):
    r = session.post(f"{base_url}/api/exec", json={"args": ["pids"]}, timeout=timeout)
    body = r.json()
    assert body["returncode"] == 0
    assert "___" in body["stdout"]

def test_exec_info_requires_project(session, base_url, timeout):
    r = session.post(f"{base_url}/api/exec", json={"args": ["info"]}, timeout=timeout)
    body = r.json()
    assert body["returncode"] != 0

def test_exec_info_project_and_sha(session, base_url, timeout, first_bug_id):
    if not first_bug_id:
        pytest.skip("No bugs loaded")
    project, sha = first_bug_id.split("@")
    r = session.post(f"{base_url}/api/exec",
                     json={"args": ["info", "-p", project, "-v", sha]}, timeout=timeout)
    body = r.json()
    assert body["returncode"] == 0
    assert "Source file" in body["stdout"]
    assert "Project:" in body["stdout"]
    assert "defects4c compile" in body["stdout"]
    assert "defects4c test" in body["stdout"]
    assert "defects4c test -r" in body["stdout"]

def test_exec_info_via_bug_id_flag(session, base_url, timeout, first_bug_id):
    if not first_bug_id:
        pytest.skip("No bugs loaded")
    r = session.post(f"{base_url}/api/exec",
                     json={"args": ["info", "-b", first_bug_id]}, timeout=timeout)
    body = r.json()
    assert body["returncode"] == 0
    assert "Source file" in body["stdout"]

def test_exec_bids(session, base_url, timeout, first_bug_id):
    if not first_bug_id:
        pytest.skip("No bugs loaded")
    project = first_bug_id.split("@")[0]
    r = session.post(f"{base_url}/api/exec",
                     json={"args": ["bids", "-p", project]}, timeout=timeout)
    body = r.json()
    assert body["returncode"] == 0

def test_exec_checkout(session, base_url, timeout, first_bug_id):
    if not first_bug_id:
        pytest.skip("No bugs loaded")
    project, sha = first_bug_id.split("@")
    r = session.post(f"{base_url}/api/exec",
                     json={"args": ["checkout", "-p", project, "-v", sha]}, timeout=timeout)
    body = r.json()
    assert "returncode" in body


# ═══════════════════════════════════════════════════════════════
#  /api/exec-shell — raw shell commands
# ═══════════════════════════════════════════════════════════════

def test_exec_shell_no_cmd(session, base_url, timeout):
    r = session.post(f"{base_url}/api/exec-shell", json={}, timeout=timeout)
    assert r.status_code == 400
    assert "error" in r.json()

def test_exec_shell_empty_cmd(session, base_url, timeout):
    r = session.post(f"{base_url}/api/exec-shell", json={"cmd": ""}, timeout=timeout)
    assert r.status_code == 400
    assert "error" in r.json()

def test_exec_shell_echo(session, base_url, timeout):
    r = session.post(f"{base_url}/api/exec-shell",
                     json={"cmd": "echo hello-from-d4c", "cwd": "/tmp"}, timeout=timeout)
    assert r.status_code == 200
    body = r.json()
    assert body["returncode"] == 0
    assert "hello-from-d4c" in body["stdout"]

def test_exec_shell_cwd_fallback(session, base_url, timeout):
    r = session.post(f"{base_url}/api/exec-shell",
                     json={"cmd": "echo ok", "cwd": "/nonexistent"}, timeout=timeout)
    assert r.json()["returncode"] == 0

def test_exec_shell_returncode_on_failure(session, base_url, timeout):
    r = session.post(f"{base_url}/api/exec-shell",
                     json={"cmd": "false", "cwd": "/tmp"}, timeout=timeout)
    assert r.json()["returncode"] != 0

def test_exec_shell_returns_stderr(session, base_url, timeout):
    r = session.post(f"{base_url}/api/exec-shell",
                     json={"cmd": "echo errout >&2", "cwd": "/tmp"}, timeout=timeout)
    assert "errout" in r.json()["stderr"]


# ═══════════════════════════════════════════════════════════════
#  Git mutation blocking
# ═══════════════════════════════════════════════════════════════

def test_git_block_commit(session, base_url, timeout):
    r = session.post(f"{base_url}/api/exec",
                     json={"args": ["git", "commit", "-m", "x"]}, timeout=timeout)
    assert r.status_code == 403
    assert "Command blocked" in r.json()["stderr"]

def test_git_block_push(session, base_url, timeout):
    r = session.post(f"{base_url}/api/exec-shell",
                     json={"cmd": "git push origin main"}, timeout=timeout)
    assert r.status_code == 403

def test_git_block_add(session, base_url, timeout):
    r = session.post(f"{base_url}/api/exec-shell",
                     json={"cmd": "git add ."}, timeout=timeout)
    assert r.status_code == 403

def test_git_block_merge(session, base_url, timeout):
    r = session.post(f"{base_url}/api/exec-shell",
                     json={"cmd": "git merge feature"}, timeout=timeout)
    assert r.status_code == 403

def test_git_block_tag(session, base_url, timeout):
    r = session.post(f"{base_url}/api/exec-shell",
                     json={"cmd": "git tag v1.0"}, timeout=timeout)
    assert r.status_code == 403

def test_git_block_branch(session, base_url, timeout):
    r = session.post(f"{base_url}/api/exec-shell",
                     json={"cmd": "git branch new-branch"}, timeout=timeout)
    assert r.status_code == 403

def test_git_block_cherry_pick(session, base_url, timeout):
    r = session.post(f"{base_url}/api/exec-shell",
                     json={"cmd": "git cherry-pick abc123"}, timeout=timeout)
    assert r.status_code == 403

def test_git_allow_log(session, base_url, timeout):
    r = session.post(f"{base_url}/api/exec-shell",
                     json={"cmd": "git log --oneline -1", "cwd": "/tmp"}, timeout=timeout)
    assert r.status_code != 403

def test_git_allow_diff(session, base_url, timeout):
    r = session.post(f"{base_url}/api/exec-shell",
                     json={"cmd": "git diff HEAD", "cwd": "/tmp"}, timeout=timeout)
    assert r.status_code != 403

def test_git_allow_status(session, base_url, timeout):
    r = session.post(f"{base_url}/api/exec-shell",
                     json={"cmd": "git status", "cwd": "/tmp"}, timeout=timeout)
    assert r.status_code != 403

def test_git_allow_checkout(session, base_url, timeout):
    r = session.post(f"{base_url}/api/exec-shell",
                     json={"cmd": "git checkout main", "cwd": "/tmp"}, timeout=timeout)
    assert r.status_code != 403

def test_git_allow_show(session, base_url, timeout):
    r = session.post(f"{base_url}/api/exec-shell",
                     json={"cmd": "git show HEAD", "cwd": "/tmp"}, timeout=timeout)
    assert r.status_code != 403


# ═══════════════════════════════════════════════════════════════
#  /api/upload + /api/download
# ═══════════════════════════════════════════════════════════════

def test_upload_download_roundtrip(session, base_url, timeout):
    path = f"unittest/{os.getpid()}_test.txt"
    content = b"test content\n"
    r = session.post(f"{base_url}/api/upload", data={"path": path},
                     files={"file": ("t.txt", io.BytesIO(content), "text/plain")},
                     timeout=timeout)
    assert r.json()["status"] == "ok"
    r2 = session.get(f"{base_url}/api/download?{urlencode({'path': path})}",
                     timeout=timeout)
    assert r2.content == content

def test_upload_binary_roundtrip(session, base_url, timeout):
    path = f"unittest/{os.getpid()}_binary.bin"
    content = bytes(range(256))
    r = session.post(f"{base_url}/api/upload", data={"path": path},
                     files={"file": ("b.bin", io.BytesIO(content), "application/octet-stream")},
                     timeout=timeout)
    assert r.status_code == 200
    r2 = session.get(f"{base_url}/api/download?{urlencode({'path': path})}",
                     timeout=timeout)
    assert r2.content == content

def test_upload_missing_path(session, base_url, timeout):
    r = session.post(f"{base_url}/api/upload", data={},
                     files={"file": ("t.txt", io.BytesIO(b"data"), "text/plain")},
                     timeout=timeout)
    assert r.status_code == 400
    assert "path" in r.json()["error"].lower()

def test_upload_missing_file(session, base_url, timeout):
    r = session.post(f"{base_url}/api/upload",
                     data={"path": "test.txt"}, timeout=timeout)
    assert r.status_code == 400
    assert "error" in r.json()

def test_download_missing_path_param(session, base_url, timeout):
    r = session.get(f"{base_url}/api/download", timeout=timeout)
    assert r.status_code == 400

def test_download_missing_file_returns_404(session, base_url, timeout):
    r = session.get(
        f"{base_url}/api/download?{urlencode({'path': 'unittest/does-not-exist.txt'})}",
        timeout=timeout)
    assert r.status_code == 404
    data = r.json()
    assert "error" in data
    assert "not found" in data["error"].lower()


# ═══════════════════════════════════════════════════════════════
#  /list_defects_bugid + /projects
# ═══════════════════════════════════════════════════════════════

def test_list_defects(session, base_url, timeout):
    r = session.get(f"{base_url}/list_defects_bugid", timeout=timeout)
    d = r.json()
    assert d["status"] == "success"
    assert d["total_count"] > 0
    for bug_id in d["defects"][:5]:
        assert "@" in bug_id

def test_list_defects_has_selected(session, base_url, timeout):
    r = session.get(f"{base_url}/list_defects_bugid", timeout=timeout)
    d = r.json()
    assert "selected_count" in d
    assert "selected" in d

def test_list_defects_has_sample(session, base_url, timeout):
    r = session.get(f"{base_url}/list_defects_bugid", timeout=timeout)
    d = r.json()
    assert "sample_defects" in d
    assert isinstance(d["sample_defects"], list)

def test_projects(session, base_url, timeout):
    r = session.get(f"{base_url}/projects", timeout=timeout)
    assert r.status_code == 200
    data = r.json()
    assert "projects" in data
    assert len(data["projects"]) > 0


# ═══════════════════════════════════════════════════════════════
#  /get_defect/{defect_id}
# ═══════════════════════════════════════════════════════════════

def test_get_defect_success(session, base_url, timeout, first_bug_id):
    if not first_bug_id:
        pytest.skip("No bugs loaded")
    r = session.get(f"{base_url}/get_defect/{first_bug_id}", timeout=timeout)
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "success"
    assert data["bug_id"] == first_bug_id
    for key in ("prompt_data", "fl_info", "trigger_tests", "regression_tests",
                "metadata", "sha_id", "defect_id"):
        assert key in data

def test_get_defect_has_prompt_structure(session, base_url, timeout, first_bug_id):
    if not first_bug_id:
        pytest.skip("No bugs loaded")
    r = session.get(f"{base_url}/get_defect/{first_bug_id}", timeout=timeout)
    prompts = r.json()["prompt_data"]["prompt"]
    assert len(prompts) == 2
    assert prompts[0]["role"] == "system"
    assert prompts[1]["role"] == "user"

def test_get_defect_fl_info_fields(session, base_url, timeout, first_bug_id):
    if not first_bug_id:
        pytest.skip("No bugs loaded")
    r = session.get(f"{base_url}/get_defect/{first_bug_id}", timeout=timeout)
    fl = r.json()["fl_info"]
    for key in ("src_file", "line_is_single", "hunk_is_single",
                "func_is_single", "hunk_start", "hunk_end"):
        assert key in fl

def test_get_defect_has_test_info(session, base_url, timeout, first_bug_id):
    if not first_bug_id:
        pytest.skip("No bugs loaded")
    r = session.get(f"{base_url}/get_defect/{first_bug_id}", timeout=timeout)
    data = r.json()
    for key in ("trigger_test_command", "regression_test_command",
                "trigger_test_filter", "test_files", "is_selected"):
        assert key in data

def test_get_defect_not_found(session, base_url, timeout):
    r = session.get(f"{base_url}/get_defect/fake___project@0000000000", timeout=timeout)
    assert r.status_code == 404


# ═══════════════════════════════════════════════════════════════
#  Phase endpoints — invalid input (400 not 500)
# ═══════════════════════════════════════════════════════════════

def test_checkout_invalid_bug_id(session, base_url, timeout):
    r = session.post(f"{base_url}/checkout",
                     json={"bug_id": "no_at_sign"}, timeout=timeout)
    assert r.status_code in (400, 422)

def test_compile_invalid_bug_id(session, base_url, timeout):
    r = session.post(f"{base_url}/compile",
                     json={"bug_id": "no_at"}, timeout=timeout)
    assert r.status_code in (400, 422)

def test_trigger_test_invalid_bug_id(session, base_url, timeout):
    r = session.post(f"{base_url}/trigger_test",
                     json={"bug_id": "bad"}, timeout=timeout)
    assert r.status_code in (400, 422)

def test_regression_test_invalid_bug_id(session, base_url, timeout):
    r = session.post(f"{base_url}/regression_test",
                     json={"bug_id": "bad"}, timeout=timeout)
    assert r.status_code in (400, 422)

def test_reproduce_invalid_bug_id(session, base_url, timeout):
    r = session.post(f"{base_url}/reproduce",
                     json={"bug_id": "bad"}, timeout=timeout)
    assert r.status_code in (400, 422)

def test_fix_invalid_bug_id(session, base_url, timeout):
    r = session.post(f"{base_url}/fix", json={
        "bug_id": "no_at_sign", "patch_path": "/tmp/fake.cpp"}, timeout=timeout)
    assert r.status_code in (400, 422)


# ═══════════════════════════════════════════════════════════════
#  Phase endpoints — unknown project (returns error, not crash)
# ═══════════════════════════════════════════════════════════════

def test_checkout_unknown_project(session, base_url, timeout):
    r = session.post(f"{base_url}/checkout",
                     json={"bug_id": "fake___project@00000"}, timeout=timeout)
    body = r.json()
    assert body.get("returncode", 1) != 0

def test_compile_unknown_project(session, base_url, timeout):
    r = session.post(f"{base_url}/compile",
                     json={"bug_id": "fake___project@00000"}, timeout=timeout)
    body = r.json()
    assert body.get("returncode", 1) != 0

def test_trigger_test_unknown_project(session, base_url, timeout):
    r = session.post(f"{base_url}/trigger_test",
                     json={"bug_id": "fake@00000"}, timeout=timeout)
    body = r.json()
    assert body.get("returncode", 1) != 0

def test_regression_test_returns_pass(session, base_url, timeout, first_bug_id):
    """Regression test currently returns pass-through."""
    if not first_bug_id:
        pytest.skip("No bugs loaded")
    r = session.post(f"{base_url}/regression_test",
                     json={"bug_id": first_bug_id}, timeout=timeout)
    body = r.json()
    assert body["returncode"] == 0

def test_reproduce_unknown_project(session, base_url, timeout):
    r = session.post(f"{base_url}/reproduce",
                     json={"bug_id": "fake@00000"}, timeout=timeout)
    body = r.json()
    assert body.get("returncode", 1) != 0


# ═══════════════════════════════════════════════════════════════
#  /build_patch
# ═══════════════════════════════════════════════════════════════

def test_build_patch_direct(session, base_url, timeout, first_bug_id):
    if not first_bug_id:
        pytest.skip("No bugs loaded")
    r = session.post(f"{base_url}/build_patch", json={
        "bug_id": first_bug_id, "llm_response": "```cpp\nint x = 0;\n```",
        "method": "direct", "generate_diff": True}, timeout=timeout)
    data = r.json()
    assert "success" in data
    if data["success"]:
        assert data.get("method") == "direct"
        assert "md5_hash" in data

def test_build_patch_diff(session, base_url, timeout, first_bug_id):
    if not first_bug_id:
        pytest.skip("No bugs loaded")
    r = session.post(f"{base_url}/build_patch", json={
        "bug_id": first_bug_id,
        "llm_response": "--- a/old.cpp\n+++ b/new.cpp\n@@ -1,1 +1,1 @@\n-old\n+new",
        "method": "diff"}, timeout=timeout)
    assert "success" in r.json()

def test_build_patch_replace_json(session, base_url, timeout, first_bug_id):
    if not first_bug_id:
        pytest.skip("No bugs loaded")
    r = session.post(f"{base_url}/build_patch", json={
        "bug_id": first_bug_id,
        "llm_response": jmod.dumps({"line_start": 1, "line_end": 2, "content": "// replaced\n"}),
        "method": "replace_json"}, timeout=timeout)
    data = r.json()
    assert "success" in data
    if data["success"]:
        assert data.get("method") == "replace_json"

def test_build_patch_full_file(session, base_url, timeout, first_bug_id):
    if not first_bug_id:
        pytest.skip("No bugs loaded")
    r = session.post(f"{base_url}/build_patch", json={
        "bug_id": first_bug_id,
        "llm_response": "// full file\nint main() { return 0; }\n",
        "method": "full_file"}, timeout=timeout)
    data = r.json()
    assert "success" in data
    if data["success"]:
        assert data.get("method") == "full_file"

def test_build_patch_auto_detects_diff(session, base_url, timeout, first_bug_id):
    if not first_bug_id:
        pytest.skip("No bugs loaded")
    r = session.post(f"{base_url}/build_patch", json={
        "bug_id": first_bug_id,
        "llm_response": "--- a/x.cpp\n+++ b/x.cpp\n@@ -1 +1 @@\n-a\n+b",
        "method": "auto"}, timeout=timeout)
    assert "success" in r.json()

def test_build_patch_invalid_bug(session, base_url, timeout):
    r = session.post(f"{base_url}/build_patch", json={
        "bug_id": "fake@0000", "llm_response": "x", "method": "direct"}, timeout=timeout)
    assert r.json()["success"] is False

def test_build_patch_replace_json_bad_format(session, base_url, timeout, first_bug_id):
    if not first_bug_id:
        pytest.skip("No bugs loaded")
    r = session.post(f"{base_url}/build_patch", json={
        "bug_id": first_bug_id, "llm_response": "not json",
        "method": "replace_json"}, timeout=timeout)
    assert r.json()["success"] is False

def test_build_patch_invalid_bug_id_format(session, base_url, timeout):
    r = session.post(f"{base_url}/build_patch", json={
        "bug_id": "no_at_sign", "llm_response": "fix"}, timeout=timeout)
    data = r.json()
    assert data["success"] is False
    assert "error" in data


# ═══════════════════════════════════════════════════════════════
#  /fix + /status
# ═══════════════════════════════════════════════════════════════

def test_fix_returns_handle(session, base_url, timeout, first_bug_id):
    if not first_bug_id:
        pytest.skip("No bugs loaded")
    r = session.post(f"{base_url}/fix", json={
        "bug_id": first_bug_id, "patch_path": "/tmp/fake_patch.cpp"}, timeout=timeout)
    data = r.json()
    assert "handle" in data
    assert len(data["handle"]) > 0

def test_fix_and_poll_status(session, base_url, timeout, first_bug_id):
    if not first_bug_id:
        pytest.skip("No bugs loaded")
    r = session.post(f"{base_url}/fix", json={
        "bug_id": first_bug_id, "patch_path": "/tmp/fake_nonexistent_patch.cpp"},
        timeout=timeout)
    data = r.json()
    assert "handle" in data
    handle = data["handle"]
    status_data = None
    for _ in range(10):
        time.sleep(0.5)
        r2 = session.get(f"{base_url}/status/{handle}", timeout=timeout)
        assert r2.status_code == 200
        status_data = r2.json()
        assert "status" in status_data
        assert "bug_id" in status_data
        if status_data["status"] in ("completed", "failed"):
            break
    assert status_data["status"] in ("queued", "running", "completed", "failed")

def test_status_not_found(session, base_url, timeout):
    r = session.get(f"{base_url}/status/nonexistent_handle", timeout=timeout)
    assert r.status_code == 404


# ═══════════════════════════════════════════════════════════════
#  /validate_oracle
# ═══════════════════════════════════════════════════════════════

def test_validate_oracle_fix(session, base_url, timeout, first_bug_id):
    if not first_bug_id:
        pytest.skip("No bugs loaded")
    r = session.post(f"{base_url}/validate_oracle",
                     json={"bug_id": first_bug_id, "mode": "fix"}, timeout=timeout)
    data = r.json()
    assert "success" in data

def test_validate_oracle_buggy(session, base_url, timeout, first_bug_id):
    if not first_bug_id:
        pytest.skip("No bugs loaded")
    r = session.post(f"{base_url}/validate_oracle",
                     json={"bug_id": first_bug_id, "mode": "buggy"}, timeout=timeout)
    data = r.json()
    assert "success" in data

def test_validate_oracle_bad_mode(session, base_url, timeout, first_bug_id):
    if not first_bug_id:
        pytest.skip("No bugs loaded")
    r = session.post(f"{base_url}/validate_oracle",
                     json={"bug_id": first_bug_id, "mode": "bad"}, timeout=timeout)
    assert r.json()["success"] is False

def test_validate_oracle_invalid_bug(session, base_url, timeout):
    r = session.post(f"{base_url}/validate_oracle",
                     json={"bug_id": "fake@00000", "mode": "fix"}, timeout=timeout)
    assert r.json()["success"] is False

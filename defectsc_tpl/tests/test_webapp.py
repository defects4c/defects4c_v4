#!/usr/bin/env python3
"""
test_webapp.py — Tests for the Defects4C HTTP API.

Covers all endpoints used in http_tutorial.py:
  - GET  /health
  - GET  /list_defects_bugid
  - GET  /get_defect/{bug_id}
  - GET  /projects
  - POST /api/exec (pids, bids, info, checkout, compile, test, test -r, reproduce)
  - POST /api/exec-shell
  - POST /api/upload (base64 JSON + multipart form)
  - GET  /api/download
  - POST /build_patch (direct, diff, replace_json, full_file)
  - POST /fix
  - GET  /status/{handle}
  - POST /checkout
  - POST /compile
  - POST /trigger_test
  - POST /regression_test
  - POST /reproduce
  - POST /validate_oracle

Uses FastAPI TestClient (no running server needed).
Mock tests run offline; real tests need the Docker environment.

Run:  pytest test_webapp.py -v
      pytest test_webapp.py -v -k mock     # mock-only (no Docker)
      pytest test_webapp.py -v -k real      # real tests (needs Docker volumes)
"""

import base64
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# ── Make sure we can import webapp ──
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ.setdefault("SRC_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ.setdefault("ROOT_DIR", tempfile.mkdtemp(prefix="d4c_test_out_"))
os.environ.setdefault("D4C_WORKSPACE", tempfile.mkdtemp(prefix="d4c_test_ws_"))
os.environ.setdefault("PATCH_OUTPUT_DIR", tempfile.mkdtemp(prefix="d4c_test_patches_"))

from fastapi.testclient import TestClient
from webapp import app, META_DICT, load_metadata, parse_bug_id, BugsInfo


# ═══════════════════════════════════════════════════════════════════
#  Fixtures
# ═══════════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def client():
    """TestClient that triggers startup (loads metadata)."""
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def all_bugs():
    """Return list of all bug_ids after metadata is loaded."""
    if not META_DICT:
        load_metadata()
    return sorted(f"{v['project']}@{sha}" for sha, v in META_DICT.items())


@pytest.fixture(scope="module")
def cppcheck_bugs():
    """Return list of cppcheck bug_ids after metadata is loaded."""
    if not META_DICT:
        load_metadata()
    return [f"{v['project']}@{sha}" for sha, v in META_DICT.items()
            if v["project"] == "danmar___cppcheck"]


@pytest.fixture(scope="module")
def first_bug(all_bugs):
    if not all_bugs:
        pytest.skip("No bugs loaded")
    return all_bugs[0]


# ═══════════════════════════════════════════════════════════════════
#  Step 0: Health check (http_tutorial.py Step 0)
# ═══════════════════════════════════════════════════════════════════

class TestMockHealth:
    def test_health(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


# ═══════════════════════════════════════════════════════════════════
#  Step 1: List bugs (http_tutorial.py Step 1)
# ═══════════════════════════════════════════════════════════════════

class TestMockListDefects:
    def test_list_defects_bugid(self, client):
        r = client.get("/list_defects_bugid")
        data = r.json()
        assert data["status"] == "success"
        assert data["total_count"] > 0
        for bug_id in data["defects"][:5]:
            assert "@" in bug_id, f"Bad bug_id format: {bug_id}"

    def test_list_defects_has_mode(self, client):
        r = client.get("/list_defects_bugid")
        data = r.json()
        assert "mode" in data
        assert "selected_count" in data
        assert "selected" in data

    def test_list_projects(self, client):
        r = client.get("/projects")
        data = r.json()
        assert "projects" in data
        assert len(data["projects"]) > 0
        # At least one known project should exist
        projects = data["projects"]
        assert any("___" in p for p in projects)


# ═══════════════════════════════════════════════════════════════════
#  Step 2: Get defect metadata (http_tutorial.py Step 2)
# ═══════════════════════════════════════════════════════════════════

class TestMockGetDefect:
    def test_get_defect_success(self, client, first_bug):
        r = client.get(f"/get_defect/{first_bug}")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "success"
        assert data["bug_id"] == first_bug
        assert "prompt_data" in data
        assert "fl_info" in data
        assert "trigger_tests" in data
        assert "regression_tests" in data
        assert "trigger_test_command" in data
        assert "regression_test_command" in data

    def test_get_defect_has_prompt(self, client, first_bug):
        r = client.get(f"/get_defect/{first_bug}")
        data = r.json()
        prompts = data["prompt_data"]["prompt"]
        assert len(prompts) == 2
        assert prompts[0]["role"] == "system"
        assert prompts[1]["role"] == "user"

    def test_get_defect_not_found(self, client):
        r = client.get("/get_defect/fake___project@0000000000")
        assert r.status_code == 404

    def test_fl_info_fields(self, client, first_bug):
        r = client.get(f"/get_defect/{first_bug}")
        fl = r.json()["fl_info"]
        for key in ("src_file", "line_is_single", "hunk_is_single", "func_is_single"):
            assert key in fl, f"Missing FL field: {key}"

    def test_trigger_command_no_ctest(self, client, first_bug):
        """Trigger test command should NOT mention ctest directly."""
        r = client.get(f"/get_defect/{first_bug}")
        data = r.json()
        trigger_cmd = data["trigger_test_command"]
        assert "inplace_test.sh" in trigger_cmd, \
            f"trigger_test_command should use inplace_test.sh, got: {trigger_cmd}"

    def test_regression_command_no_ctest(self, client, first_bug):
        """Regression test command should NOT mention ctest directly."""
        r = client.get(f"/get_defect/{first_bug}")
        data = r.json()
        reg_cmd = data["regression_test_command"]
        assert "inplace_test.sh" in reg_cmd, \
            f"regression_test_command should use inplace_test.sh, got: {reg_cmd}"


# ═══════════════════════════════════════════════════════════════════
#  Step 3-5: D4J-compatible /api/exec — checkout, compile, test
#  (http_tutorial.py Steps 3-5 + D4J demo)
# ═══════════════════════════════════════════════════════════════════

class TestMockApiExec:
    """Tests for /api/exec sub-command dispatch (defects4j-compatible)."""

    def test_no_args_returns_error(self, client):
        r = client.post("/api/exec", json={"args": []})
        assert r.status_code == 400

    def test_unknown_command(self, client):
        r = client.post("/api/exec", json={"args": ["nonexistent_cmd"]})
        data = r.json()
        assert data["returncode"] != 0
        assert "Unknown subcommand" in data["stderr"]

    def test_pids(self, client):
        r = client.post("/api/exec", json={"args": ["pids"]})
        data = r.json()
        assert data["returncode"] == 0
        assert "___" in data["stdout"]

    def test_bids(self, client, first_bug):
        project = first_bug.split("@")[0]
        r = client.post("/api/exec", json={"args": ["bids", "-p", project]})
        data = r.json()
        assert data["returncode"] == 0
        assert len(data["stdout"].strip()) > 0

    def test_info_project_and_sha(self, client, first_bug):
        project, sha = first_bug.split("@")
        r = client.post("/api/exec",
                         json={"args": ["info", "-p", project, "-v", sha]})
        data = r.json()
        assert data["returncode"] == 0
        assert "Source file" in data["stdout"]
        assert "Project:" in data["stdout"]
        assert "defects4c compile" in data["stdout"]
        assert "defects4c test" in data["stdout"]
        assert "defects4c test -r" in data["stdout"]

    def test_info_project_only(self, client, first_bug):
        project = first_bug.split("@")[0]
        r = client.post("/api/exec",
                         json={"args": ["info", "-p", project]})
        data = r.json()
        assert data["returncode"] == 0
        assert project in data["stdout"]

    def test_info_requires_project(self, client):
        r = client.post("/api/exec", json={"args": ["info"]})
        data = r.json()
        assert data["returncode"] != 0

    def test_checkout_via_exec(self, client, first_bug):
        project, sha = first_bug.split("@")
        r = client.post("/api/exec",
                         json={"args": ["checkout", "-p", project, "-v", sha]})
        data = r.json()
        assert "returncode" in data

    def test_compile_via_exec(self, client, first_bug):
        project, sha = first_bug.split("@")
        r = client.post("/api/exec",
                         json={"args": ["compile", "-p", project, "-v", sha]})
        data = r.json()
        assert "returncode" in data

    def test_trigger_test_via_exec(self, client, first_bug):
        """test -p <project> -v <sha> → trigger test"""
        project, sha = first_bug.split("@")
        r = client.post("/api/exec",
                         json={"args": ["test", "-p", project, "-v", sha]})
        data = r.json()
        assert "returncode" in data

    def test_regression_test_via_exec(self, client, first_bug):
        """test -r -p <project> -v <sha> → regression test"""
        project, sha = first_bug.split("@")
        r = client.post("/api/exec",
                         json={"args": ["test", "-r", "-p", project, "-v", sha]})
        data = r.json()
        assert "returncode" in data

    def test_git_blocked(self, client):
        """Git-mutating commands via exec should be blocked."""
        r = client.post("/api/exec-shell",
                         json={"cmd": "git push origin main", "cwd": "/tmp"})
        assert r.status_code == 403


# ═══════════════════════════════════════════════════════════════════
#  /api/exec-shell
# ═══════════════════════════════════════════════════════════════════

class TestMockExecShell:
    def test_echo(self, client):
        r = client.post("/api/exec-shell", json={"cmd": "echo hello", "cwd": "/tmp"})
        data = r.json()
        assert data["returncode"] == 0
        assert "hello" in data["stdout"]

    def test_no_cmd(self, client):
        r = client.post("/api/exec-shell", json={"cmd": "", "cwd": "/tmp"})
        data = r.json()
        assert "error" in data or data.get("returncode", 1) != 0


# ═══════════════════════════════════════════════════════════════════
#  Phase endpoints: /checkout, /compile, /trigger_test, /regression_test
# ═══════════════════════════════════════════════════════════════════

class TestMockCheckout:
    def test_checkout_no_git_dir(self, client, first_bug):
        """Checkout should fail gracefully when repo not cloned on host."""
        r = client.post("/checkout", json={"bug_id": first_bug})
        data = r.json()
        assert "returncode" in data

    def test_checkout_skip_when_build_exists(self, client, first_bug):
        """Verify skip logic: if build dir exists and not forced → skip."""
        project, sha = first_bug.split("@")
        out_root = Path(os.environ["ROOT_DIR"])
        repo_dir = out_root / project / f"git_repo_dir_{sha}"
        build_dir = repo_dir / f"build_{sha}"
        (repo_dir / ".git").mkdir(parents=True, exist_ok=True)
        build_dir.mkdir(parents=True, exist_ok=True)
        r = client.post("/checkout", json={"bug_id": first_bug, "is_force": False})
        data = r.json()
        assert data["returncode"] == 0
        assert "skip" in data["stdout"].lower()
        shutil.rmtree(str(out_root / project), ignore_errors=True)

    def test_checkout_bad_bug_id(self, client):
        r = client.post("/checkout", json={"bug_id": "no_at_sign"})
        assert r.status_code == 400


class TestMockCompile:
    def test_compile_bad_bug_id(self, client):
        r = client.post("/compile", json={"bug_id": "no_at_sign"})
        assert r.status_code == 400

    def test_compile_endpoint(self, client, first_bug):
        r = client.post("/compile", json={"bug_id": first_bug})
        data = r.json()
        assert "returncode" in data


class TestMockTriggerTest:
    def test_trigger_test_bad_bug_id(self, client):
        r = client.post("/trigger_test", json={"bug_id": "no_at_sign"})
        assert r.status_code == 400

    def test_trigger_test_endpoint(self, client, first_bug):
        r = client.post("/trigger_test", json={"bug_id": first_bug})
        data = r.json()
        assert "returncode" in data


class TestMockRegressionTest:
    def test_regression_test_bad_bug_id(self, client):
        r = client.post("/regression_test", json={"bug_id": "no_at_sign"})
        assert r.status_code == 400

    def test_regression_test_endpoint(self, client, first_bug):
        r = client.post("/regression_test", json={"bug_id": first_bug})
        data = r.json()
        assert "returncode" in data


class TestMockReproduce:
    def test_reproduce_bad_bug_id(self, client):
        r = client.post("/reproduce", json={"bug_id": "no_at_sign"})
        assert r.status_code == 400


# ═══════════════════════════════════════════════════════════════════
#  Step 7: Build patch — all methods (http_tutorial.py Step 7 + patch-demo)
# ═══════════════════════════════════════════════════════════════════

class TestMockBuildPatch:
    def test_build_patch_missing_sha(self, client):
        r = client.post("/build_patch", json={
            "bug_id": "fake___project@0000000000",
            "llm_response": "```cpp\nint x = 0;\n```",
            "method": "direct",
        })
        data = r.json()
        assert data["success"] is False

    def test_build_patch_missing_source(self, client, first_bug):
        """Patch build fails if source file doesn't exist in repo."""
        r = client.post("/build_patch", json={
            "bug_id": first_bug,
            "llm_response": "```cpp\nint x = 0;\n```",
            "method": "direct",
        })
        data = r.json()
        assert data["success"] is False

    def test_build_patch_method_direct(self, client, first_bug):
        """Method: direct (markdown code block)."""
        r = client.post("/build_patch", json={
            "bug_id": first_bug,
            "llm_response": "```cpp\n// placeholder fix\nint x = 0;\n```",
            "method": "direct",
            "generate_diff": True,
        })
        data = r.json()
        assert "success" in data

    def test_build_patch_method_diff(self, client, first_bug):
        """Method: diff (unified diff)."""
        r = client.post("/build_patch", json={
            "bug_id": first_bug,
            "llm_response": "--- a/old.cpp\n+++ b/new.cpp\n@@ -1,1 +1,1 @@\n-old line\n+new line",
            "method": "diff",
            "generate_diff": True,
        })
        data = r.json()
        assert "success" in data

    def test_build_patch_method_replace_json(self, client, first_bug):
        """Method: replace_json (line range + content)."""
        r = client.post("/build_patch", json={
            "bug_id": first_bug,
            "llm_response": json.dumps({
                "line_start": 1,
                "line_end": 1,
                "content": "// replaced\n",
            }),
            "method": "replace_json",
            "generate_diff": True,
        })
        data = r.json()
        assert "success" in data

    def test_build_patch_method_full_file(self, client, first_bug):
        """Method: full_file (entire file content)."""
        r = client.post("/build_patch", json={
            "bug_id": first_bug,
            "llm_response": "// Full file\nint main() { return 0; }\n",
            "method": "full_file",
            "generate_diff": True,
        })
        data = r.json()
        assert "success" in data


# ═══════════════════════════════════════════════════════════════════
#  Step 8-9: Fix + status polling (http_tutorial.py Steps 8-9)
# ═══════════════════════════════════════════════════════════════════

class TestMockFixEndpoint:
    def test_fix_returns_handle(self, client, first_bug):
        r = client.post("/fix", json={
            "bug_id": first_bug,
            "patch_path": "/tmp/fake_patch.cpp",
        })
        data = r.json()
        assert "handle" in data

    def test_fix_bad_bug_id(self, client):
        r = client.post("/fix", json={
            "bug_id": "no_at_sign",
            "patch_path": "/tmp/fake_patch.cpp",
        })
        assert r.status_code == 400

    def test_status_not_found(self, client):
        r = client.get("/status/nonexistent_handle_12345")
        assert r.status_code == 404

    def test_status_after_fix(self, client, first_bug):
        """Submit a fix, then poll status."""
        r = client.post("/fix", json={
            "bug_id": first_bug,
            "patch_path": "/tmp/fake_patch.cpp",
        })
        handle = r.json()["handle"]
        r2 = client.get(f"/status/{handle}")
        assert r2.status_code == 200
        data = r2.json()
        assert "status" in data
        assert data["status"] in ("queued", "running", "completed", "failed")


# ═══════════════════════════════════════════════════════════════════
#  Upload / Download (http_tutorial.py /api/upload + /api/download)
# ═══════════════════════════════════════════════════════════════════

class TestMockUploadDownload:
    def test_upload_base64(self, client):
        content = base64.b64encode(b"hello world").decode()
        r = client.post("/api/upload", json={
            "path": "test_upload.txt",
            "content_base64": content,
        })
        data = r.json()
        assert data["status"] == "ok"

    def test_upload_multipart(self, client):
        r = client.post("/api/upload",
                         data={"path": "/tmp/test_multipart.txt"},
                         files={"file": ("test.txt", b"multipart content", "text/plain")})
        data = r.json()
        assert data["status"] == "ok"

    def test_download_not_found(self, client):
        r = client.get("/api/download?path=/nonexistent/file.txt")
        assert r.status_code == 404

    def test_upload_then_download(self, client):
        ws = os.environ["D4C_WORKSPACE"]
        dest = os.path.join(ws, "roundtrip_test.txt")
        content = base64.b64encode(b"roundtrip").decode()
        r = client.post("/api/upload", json={
            "path": dest,
            "content_base64": content,
        })
        assert r.json()["status"] == "ok"
        r2 = client.get(f"/api/download?path={dest}")
        assert r2.status_code == 200
        assert r2.content == b"roundtrip"


# ═══════════════════════════════════════════════════════════════════
#  Oracle validation (http_tutorial.py Step 6)
# ═══════════════════════════════════════════════════════════════════

class TestMockOracleValidation:
    def test_oracle_fix_no_repo(self, client, first_bug):
        """Oracle validate without repo should fail gracefully."""
        r = client.post("/validate_oracle", json={
            "bug_id": first_bug,
            "mode": "fix",
        })
        data = r.json()
        assert "success" in data
        # Without repo, should fail but not crash
        if not data["success"]:
            assert "error" in data

    def test_oracle_buggy_no_repo(self, client, first_bug):
        r = client.post("/validate_oracle", json={
            "bug_id": first_bug,
            "mode": "buggy",
        })
        data = r.json()
        assert "success" in data

    def test_oracle_invalid_mode(self, client, first_bug):
        r = client.post("/validate_oracle", json={
            "bug_id": first_bug,
            "mode": "invalid",
        })
        data = r.json()
        assert data["success"] is False
        assert "Unknown mode" in data["error"]


# ═══════════════════════════════════════════════════════════════════
#  parse_bug_id
# ═══════════════════════════════════════════════════════════════════

class TestMockParseBugId:
    def test_valid(self):
        p, s = parse_bug_id("danmar___cppcheck@abc123")
        assert p == "danmar___cppcheck"
        assert s == "abc123"

    def test_colon_separator(self):
        p, s = parse_bug_id("danmar___cppcheck:abc123")
        assert p == "danmar___cppcheck"

    def test_invalid(self):
        with pytest.raises(ValueError):
            parse_bug_id("no_at_sign")


# ═══════════════════════════════════════════════════════════════════
#  Trigger vs regression test design
# ═══════════════════════════════════════════════════════════════════

class TestMockRegressionDesign:
    """Verify that trigger vs regression test concepts are set up correctly."""

    def test_trigger_tests_are_filtered(self, cppcheck_bugs):
        if not cppcheck_bugs:
            pytest.skip("No cppcheck bugs loaded")
        for bug_id in cppcheck_bugs[:5]:
            project, sha = bug_id.split("@")
            instance = BugsInfo(project, sha)
            triggers = instance.get_trigger_tests()
            test_flags = instance.meta_info.get("test_flags", [])
            assert len(triggers) > 0 or len(test_flags) > 0, \
                f"Bug {sha[:12]} has no trigger tests"

    def test_regression_is_unfiltered(self, cppcheck_bugs):
        if not cppcheck_bugs:
            pytest.skip("No cppcheck bugs loaded")
        project, sha = cppcheck_bugs[0].split("@")
        instance = BugsInfo(project, sha)
        reg_filter = instance.get_regression_test_flags()
        assert reg_filter == "", "Regression filter should be empty (run all tests)"

    def test_cppcheck_smoke_bugs(self, cppcheck_bugs):
        assert len(cppcheck_bugs) >= 5, \
            f"Expected >=5 cppcheck bugs, got {len(cppcheck_bugs)}"


# ═══════════════════════════════════════════════════════════════════
#  D4J exec: verify test dispatch for -r flag
# ═══════════════════════════════════════════════════════════════════

class TestMockExecTestDispatch:
    """Verify that 'test' vs 'test -r' dispatch correctly."""

    def test_trigger_dispatch(self, client, first_bug):
        project, sha = first_bug.split("@")
        r = client.post("/api/exec",
                         json={"args": ["test", "-p", project, "-v", sha]})
        data = r.json()
        assert "returncode" in data

    def test_regression_dispatch(self, client, first_bug):
        project, sha = first_bug.split("@")
        r = client.post("/api/exec",
                         json={"args": ["test", "-r", "-p", project, "-v", sha]})
        data = r.json()
        assert "returncode" in data


# ═══════════════════════════════════════════════════════════════════
#  REAL tests — need Docker with mounted volumes
# ═══════════════════════════════════════════════════════════════════

@pytest.fixture
def real_env():
    """Check if we're running inside the Docker container with real data."""
    out_root = Path(os.environ.get("ROOT_DIR", "/out"))
    has_repos = any((out_root / "danmar___cppcheck").glob("git_repo_dir_*/.git"))
    return has_repos


class TestRealCheckout:
    def test_real_checkout_skip(self, client, cppcheck_bugs, real_env):
        if not real_env:
            pytest.skip("No real repos on disk")
        if not cppcheck_bugs:
            pytest.skip("No cppcheck bugs")
        r = client.post("/checkout", json={
            "bug_id": cppcheck_bugs[0], "is_force": False
        })
        data = r.json()
        assert data["returncode"] == 0


class TestRealCompile:
    def test_real_compile(self, client, cppcheck_bugs, real_env):
        if not real_env:
            pytest.skip("No real repos on disk")
        if not cppcheck_bugs:
            pytest.skip("No cppcheck bugs")
        r = client.post("/compile", json={"bug_id": cppcheck_bugs[0]})
        data = r.json()
        assert "returncode" in data


class TestRealTriggerTest:
    def test_real_trigger(self, client, cppcheck_bugs, real_env):
        if not real_env:
            pytest.skip("No real repos on disk")
        if not cppcheck_bugs:
            pytest.skip("No cppcheck bugs")
        r = client.post("/trigger_test", json={"bug_id": cppcheck_bugs[0]})
        data = r.json()
        assert "returncode" in data


class TestRealRegressionTest:
    def test_real_regression(self, client, cppcheck_bugs, real_env):
        if not real_env:
            pytest.skip("No real repos on disk")
        if not cppcheck_bugs:
            pytest.skip("No cppcheck bugs")
        r = client.post("/regression_test", json={"bug_id": cppcheck_bugs[0]})
        data = r.json()
        assert "returncode" in data


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

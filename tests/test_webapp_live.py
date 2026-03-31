import base64
import io
import os
import time
import unittest
from urllib.parse import urlencode

import requests


class Defects4CWebappLiveTests(unittest.TestCase):
    """
    Live HTTP tests for the running defects4c web service.

    These tests assume the service is already running at:
        http://127.0.0.1:8092

    Override with:
        D4C_WEBAPP_URL=http://127.0.0.1:8092 python -m unittest -v tests/test_webapp_live.py

    Test categories:
        test_health_*            — liveness
        test_exec_*              — /api/exec (D4J-compatible sub-commands)
        test_exec_shell_*        — /api/exec-shell (raw shell)
        test_git_block_*         — git mutation blocking
        test_upload_*            — /api/upload (multipart)
        test_download_*          — /api/download
        test_list_*              — /list_defects_bugid, /projects
        test_get_defect_*        — /get_defect/{id}
        test_checkout_*          — /checkout
        test_compile_*           — /compile
        test_trigger_test_*      — /trigger_test
        test_regression_test_*   — /regression_test
        test_reproduce_*         — /reproduce
        test_build_patch_*       — /build_patch
        test_fix_*               — /fix + /status
        test_validate_oracle_*   — /validate_oracle
    """

    BASE_URL = os.environ.get("D4C_WEBAPP_URL", "http://127.0.0.1:8092")
    TIMEOUT = float(os.environ.get("D4C_TEST_TIMEOUT", "20"))

    @classmethod
    def setUpClass(cls):
        cls.session = requests.Session()

    @classmethod
    def tearDownClass(cls):
        cls.session.close()

    def url(self, path: str) -> str:
        return f"{self.BASE_URL}{path}"

    def post_json(self, path: str, payload: dict):
        return self.session.post(self.url(path), json=payload, timeout=self.TIMEOUT)

    def _get_first_bug_id(self):
        resp = self.session.get(self.url("/list_defects_bugid"), timeout=self.TIMEOUT)
        data = resp.json()
        if data.get("selected"):
            return data["selected"][0]
        if data.get("defects"):
            return data["defects"][0]
        return None

    # ═══════════════════════════════════════════════════════════════
    #  /health
    # ═══════════════════════════════════════════════════════════════

    def test_health_returns_ok(self):
        response = self.session.get(self.url("/health"), timeout=self.TIMEOUT)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Content-Type"].split(";")[0], "application/json")
        self.assertEqual(response.json(), {"status": "ok"})

    # ═══════════════════════════════════════════════════════════════
    #  /api/exec — D4J-compatible sub-command dispatch
    # ═══════════════════════════════════════════════════════════════

    def test_exec_requires_args(self):
        response = self.post_json("/api/exec", {})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "No args provided"})

    def test_exec_empty_args(self):
        response = self.post_json("/api/exec", {"args": []})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "No args provided"})

    def test_exec_unknown_command(self):
        response = self.post_json("/api/exec", {"args": ["nonexistent_cmd"]})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["returncode"], 1)
        self.assertIn("Unknown subcommand", body["stderr"])

    def test_exec_info_requires_project_and_sha(self):
        response = self.post_json("/api/exec", {"args": ["info"]})
        body = response.json()
        self.assertNotEqual(body["returncode"], 0)

    def test_exec_info_via_d4j_args(self):
        """defects4c info -p <project> -v <sha> via /api/exec."""
        bug_id = self._get_first_bug_id()
        if not bug_id:
            self.skipTest("No bugs loaded")
        project, sha = bug_id.split("@")
        response = self.post_json("/api/exec",
                                  {"args": ["info", "-p", project, "-v", sha]})
        body = response.json()
        self.assertEqual(body["returncode"], 0)
        self.assertIn("Source file", body["stdout"])
        self.assertIn("Project:", body["stdout"])
        self.assertIn("defects4c compile", body["stdout"])
        self.assertIn("defects4c test -r", body["stdout"])
        self.assertIn("defects4c test", body["stdout"])

    def test_exec_info_via_bug_id_flag(self):
        """defects4c info -b project@sha via /api/exec."""
        bug_id = self._get_first_bug_id()
        if not bug_id:
            self.skipTest("No bugs loaded")
        response = self.post_json("/api/exec",
                                  {"args": ["info", "-b", bug_id]})
        body = response.json()
        self.assertEqual(body["returncode"], 0)
        self.assertIn("Source file", body["stdout"])

    def test_exec_checkout_via_d4j_args(self):
        bug_id = self._get_first_bug_id()
        if not bug_id:
            self.skipTest("No bugs loaded")
        project, sha = bug_id.split("@")
        response = self.post_json("/api/exec",
                                  {"args": ["checkout", "-p", project, "-v", sha]})
        body = response.json()
        self.assertIn("returncode", body)

    def test_exec_compile_via_d4j_args(self):
        bug_id = self._get_first_bug_id()
        if not bug_id:
            self.skipTest("No bugs loaded")
        project, sha = bug_id.split("@")
        response = self.post_json("/api/exec",
                                  {"args": ["compile", "-p", project, "-v", sha]})
        body = response.json()
        self.assertIn("returncode", body)

    def test_exec_test_r_via_d4j_args(self):
        """defects4c test -r (trigger tests) via /api/exec."""
        bug_id = self._get_first_bug_id()
        if not bug_id:
            self.skipTest("No bugs loaded")
        project, sha = bug_id.split("@")
        response = self.post_json("/api/exec",
                                  {"args": ["test", "-r", "true", "-p", project, "-v", sha]})
        body = response.json()
        self.assertIn("returncode", body)

    def test_exec_test_full_via_d4j_args(self):
        """defects4c test (full suite) via /api/exec."""
        bug_id = self._get_first_bug_id()
        if not bug_id:
            self.skipTest("No bugs loaded")
        project, sha = bug_id.split("@")
        response = self.post_json("/api/exec",
                                  {"args": ["test", "-p", project, "-v", sha]})
        body = response.json()
        self.assertIn("returncode", body)

    def test_exec_reproduce_via_d4j_args(self):
        bug_id = self._get_first_bug_id()
        if not bug_id:
            self.skipTest("No bugs loaded")
        project, sha = bug_id.split("@")
        response = self.post_json("/api/exec",
                                  {"args": ["reproduce", "-p", project, "-v", sha]})
        body = response.json()
        self.assertIn("returncode", body)

    # ═══════════════════════════════════════════════════════════════
    #  /api/exec-shell — raw shell commands
    # ═══════════════════════════════════════════════════════════════

    def test_exec_shell_requires_cmd(self):
        response = self.post_json("/api/exec-shell", {})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "No cmd provided"})

    def test_exec_shell_empty_cmd(self):
        response = self.post_json("/api/exec-shell", {"cmd": ""})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "No cmd provided"})

    def test_exec_shell_echo(self):
        response = self.post_json("/api/exec-shell",
                                  {"cmd": "echo hello-from-d4c", "cwd": "/tmp"})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["returncode"], 0)
        self.assertIn("hello-from-d4c", body["stdout"])

    def test_exec_shell_falls_back_when_cwd_does_not_exist(self):
        response = self.post_json(
            "/api/exec-shell",
            {"cmd": "python3 -c \"print('cwd-fallback-ok')\"",
             "cwd": "/path/that/does/not/exist"},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["returncode"], 0)
        self.assertIn("cwd-fallback-ok", body["stdout"])

    def test_exec_shell_returncode_on_failure(self):
        response = self.post_json("/api/exec-shell",
                                  {"cmd": "false", "cwd": "/tmp"})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertNotEqual(body["returncode"], 0)

    def test_exec_shell_returns_stderr(self):
        response = self.post_json("/api/exec-shell",
                                  {"cmd": "echo errout >&2", "cwd": "/tmp"})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("errout", body["stderr"])

    # ═══════════════════════════════════════════════════════════════
    #  Git mutation blocking
    # ═══════════════════════════════════════════════════════════════

    def test_git_block_exec_commit(self):
        response = self.post_json("/api/exec",
                                  {"args": ["git", "commit", "-m", "should-block"]})
        self.assertEqual(response.status_code, 403)
        body = response.json()
        self.assertEqual(body["returncode"], 1)
        self.assertEqual(body["stdout"], "")
        self.assertIn("Command blocked", body["stderr"])
        self.assertIn("git commit", body["stderr"])

    def test_git_block_shell_push(self):
        response = self.post_json("/api/exec-shell", {"cmd": "git push origin main"})
        self.assertEqual(response.status_code, 403)
        body = response.json()
        self.assertEqual(body["returncode"], 1)
        self.assertIn("Command blocked", body["stderr"])
        self.assertIn("git push", body["stderr"])

    def test_git_block_shell_add(self):
        response = self.post_json("/api/exec-shell", {"cmd": "git add ."})
        self.assertEqual(response.status_code, 403)
        self.assertIn("Command blocked", response.json()["stderr"])

    def test_git_block_shell_merge(self):
        response = self.post_json("/api/exec-shell", {"cmd": "git merge feature"})
        self.assertEqual(response.status_code, 403)

    def test_git_block_shell_tag(self):
        response = self.post_json("/api/exec-shell", {"cmd": "git tag v1.0"})
        self.assertEqual(response.status_code, 403)

    def test_git_block_shell_branch(self):
        response = self.post_json("/api/exec-shell", {"cmd": "git branch new-branch"})
        self.assertEqual(response.status_code, 403)

    def test_git_block_shell_cherry_pick(self):
        response = self.post_json("/api/exec-shell", {"cmd": "git cherry-pick abc123"})
        self.assertEqual(response.status_code, 403)

    def test_git_allow_log(self):
        response = self.post_json("/api/exec-shell",
                                  {"cmd": "git log --oneline -1", "cwd": "/tmp"})
        self.assertNotEqual(response.status_code, 403)

    def test_git_allow_diff(self):
        response = self.post_json("/api/exec-shell",
                                  {"cmd": "git diff HEAD", "cwd": "/tmp"})
        self.assertNotEqual(response.status_code, 403)

    def test_git_allow_status(self):
        response = self.post_json("/api/exec-shell",
                                  {"cmd": "git status", "cwd": "/tmp"})
        self.assertNotEqual(response.status_code, 403)

    def test_git_allow_checkout(self):
        response = self.post_json("/api/exec-shell",
                                  {"cmd": "git checkout main", "cwd": "/tmp"})
        self.assertNotEqual(response.status_code, 403)

    def test_git_allow_show(self):
        response = self.post_json("/api/exec-shell",
                                  {"cmd": "git show HEAD", "cwd": "/tmp"})
        self.assertNotEqual(response.status_code, 403)

    def test_git_allow_blame(self):
        response = self.post_json("/api/exec-shell",
                                  {"cmd": "git blame README.md", "cwd": "/tmp"})
        self.assertNotEqual(response.status_code, 403)

    # ═══════════════════════════════════════════════════════════════
    #  /api/upload + /api/download — file round-trip
    # ═══════════════════════════════════════════════════════════════

    def test_upload_and_download_round_trip(self):
        rel_path = f"unittest/{os.getpid()}_roundtrip.txt"
        content = b"hello from defects4c unittest\n"

        upload_response = self.session.post(
            self.url("/api/upload"),
            data={"path": rel_path},
            files={"file": ("roundtrip.txt", io.BytesIO(content), "text/plain")},
            timeout=self.TIMEOUT,
        )
        self.assertEqual(upload_response.status_code, 200)
        upload_body = upload_response.json()
        self.assertEqual(upload_body["status"], "ok")
        self.assertTrue(upload_body["path"].endswith(rel_path))

        download_response = self.session.get(
            self.url(f"/api/download?{urlencode({'path': rel_path})}"),
            timeout=self.TIMEOUT,
        )
        self.assertEqual(download_response.status_code, 200)
        self.assertEqual(download_response.content, content)

    def test_upload_binary_round_trip(self):
        """Upload and download binary content."""
        rel_path = f"unittest/{os.getpid()}_binary.bin"
        content = bytes(range(256))

        upload_response = self.session.post(
            self.url("/api/upload"),
            data={"path": rel_path},
            files={"file": ("binary.bin", io.BytesIO(content), "application/octet-stream")},
            timeout=self.TIMEOUT,
        )
        self.assertEqual(upload_response.status_code, 200)

        download_response = self.session.get(
            self.url(f"/api/download?{urlencode({'path': rel_path})}"),
            timeout=self.TIMEOUT,
        )
        self.assertEqual(download_response.status_code, 200)
        self.assertEqual(download_response.content, content)

    def test_upload_missing_path(self):
        upload_response = self.session.post(
            self.url("/api/upload"),
            data={},
            files={"file": ("test.txt", io.BytesIO(b"data"), "text/plain")},
            timeout=self.TIMEOUT,
        )
        self.assertEqual(upload_response.status_code, 400)
        self.assertIn("path", upload_response.json()["error"].lower())

    def test_upload_missing_file(self):
        upload_response = self.session.post(
            self.url("/api/upload"),
            data={"path": "test.txt"},
            timeout=self.TIMEOUT,
        )
        self.assertEqual(upload_response.status_code, 400)
        self.assertIn("file", upload_response.json()["error"].lower())

    def test_download_missing_path_param(self):
        response = self.session.get(self.url("/api/download"), timeout=self.TIMEOUT)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "Missing 'path' query param"})

    def test_download_missing_file_returns_404(self):
        response = self.session.get(
            self.url(f"/api/download?{urlencode({'path': 'unittest/does-not-exist.txt'})}"),
            timeout=self.TIMEOUT,
        )
        self.assertEqual(response.status_code, 404)
        self.assertIn("File not found", response.json()["error"])

    # ═══════════════════════════════════════════════════════════════
    #  /list_defects_bugid
    # ═══════════════════════════════════════════════════════════════

    def test_list_defects_bugid(self):
        response = self.session.get(self.url("/list_defects_bugid"), timeout=self.TIMEOUT)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "success")
        self.assertGreater(data["total_count"], 0)
        for bug_id in data["defects"][:5]:
            self.assertIn("@", bug_id, f"Bad bug_id format: {bug_id}")

    def test_list_defects_has_selected(self):
        response = self.session.get(self.url("/list_defects_bugid"), timeout=self.TIMEOUT)
        data = response.json()
        self.assertIn("selected_count", data)
        self.assertIn("selected", data)
        self.assertGreater(data["selected_count"], 0)

    def test_list_defects_has_sample(self):
        response = self.session.get(self.url("/list_defects_bugid"), timeout=self.TIMEOUT)
        data = response.json()
        self.assertIn("sample_defects", data)
        self.assertIsInstance(data["sample_defects"], list)

    # ═══════════════════════════════════════════════════════════════
    #  /projects
    # ═══════════════════════════════════════════════════════════════

    def test_projects(self):
        response = self.session.get(self.url("/projects"), timeout=self.TIMEOUT)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("projects", data)
        self.assertIsInstance(data["projects"], list)
        self.assertGreater(len(data["projects"]), 0)

    # ═══════════════════════════════════════════════════════════════
    #  /get_defect/{defect_id}
    # ═══════════════════════════════════════════════════════════════

    def test_get_defect_success(self):
        bug_id = self._get_first_bug_id()
        if not bug_id:
            self.skipTest("No bugs loaded")
        response = self.session.get(self.url(f"/get_defect/{bug_id}"), timeout=self.TIMEOUT)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["bug_id"], bug_id)
        self.assertIn("prompt_data", data)
        self.assertIn("fl_info", data)
        self.assertIn("trigger_tests", data)
        self.assertIn("regression_tests", data)
        self.assertIn("metadata", data)
        self.assertIn("sha_id", data)
        self.assertIn("defect_id", data)

    def test_get_defect_has_prompt_structure(self):
        bug_id = self._get_first_bug_id()
        if not bug_id:
            self.skipTest("No bugs loaded")
        response = self.session.get(self.url(f"/get_defect/{bug_id}"), timeout=self.TIMEOUT)
        data = response.json()
        prompts = data["prompt_data"]["prompt"]
        self.assertEqual(len(prompts), 2)
        self.assertEqual(prompts[0]["role"], "system")
        self.assertEqual(prompts[1]["role"], "user")
        self.assertIn("temperature", data["prompt_data"])

    def test_get_defect_fl_info_fields(self):
        bug_id = self._get_first_bug_id()
        if not bug_id:
            self.skipTest("No bugs loaded")
        response = self.session.get(self.url(f"/get_defect/{bug_id}"), timeout=self.TIMEOUT)
        fl = response.json()["fl_info"]
        for key in ("src_file", "line_is_single", "hunk_is_single",
                     "func_is_single", "hunk_start", "hunk_end"):
            self.assertIn(key, fl, f"Missing FL field: {key}")

    def test_get_defect_not_found(self):
        response = self.session.get(
            self.url("/get_defect/fake___project@0000000000"),
            timeout=self.TIMEOUT,
        )
        self.assertEqual(response.status_code, 404)

    def test_get_defect_has_test_info(self):
        bug_id = self._get_first_bug_id()
        if not bug_id:
            self.skipTest("No bugs loaded")
        response = self.session.get(self.url(f"/get_defect/{bug_id}"), timeout=self.TIMEOUT)
        data = response.json()
        self.assertIn("trigger_test_command", data)
        self.assertIn("regression_test_command", data)
        self.assertIn("trigger_test_filter", data)
        self.assertIn("test_files", data)
        self.assertIn("is_selected", data)

    # ═══════════════════════════════════════════════════════════════
    #  /checkout (phase endpoint)
    # ═══════════════════════════════════════════════════════════════

    def test_checkout_invalid_bug_id(self):
        response = self.post_json("/checkout", {"bug_id": "no_at_sign"})
        self.assertEqual(response.status_code, 400)

    def test_checkout_unknown_project(self):
        response = self.post_json("/checkout", {"bug_id": "fake___project@00000"})
        body = response.json()
        self.assertNotEqual(body.get("returncode", 1), 0)

    def test_checkout_no_git_dir(self):
        bug_id = self._get_first_bug_id()
        if not bug_id:
            self.skipTest("No bugs loaded")
        response = self.post_json("/checkout", {"bug_id": bug_id})
        body = response.json()
        self.assertIn("returncode", body)
        if body["returncode"] != 0:
            stderr = body.get("stderr", "")
            self.assertTrue(
                any(kw in stderr.lower() for kw in ("warmup", ".git", "run_warmup")),
                f"Unexpected stderr: {stderr[:200]}"
            )

    def test_checkout_with_force_flag(self):
        bug_id = self._get_first_bug_id()
        if not bug_id:
            self.skipTest("No bugs loaded")
        response = self.post_json("/checkout", {"bug_id": bug_id, "is_force": True})
        body = response.json()
        self.assertIn("returncode", body)

    # ═══════════════════════════════════════════════════════════════
    #  /compile (phase endpoint)
    # ═══════════════════════════════════════════════════════════════

    def test_compile_invalid_bug_id(self):
        response = self.post_json("/compile", {"bug_id": "no_at"})
        self.assertEqual(response.status_code, 400)

    def test_compile_unknown_project(self):
        response = self.post_json("/compile", {"bug_id": "fake___project@00000"})
        body = response.json()
        self.assertNotEqual(body.get("returncode", 1), 0)

    # ═══════════════════════════════════════════════════════════════
    #  /trigger_test (phase endpoint)
    # ═══════════════════════════════════════════════════════════════

    def test_trigger_test_invalid_bug_id(self):
        response = self.post_json("/trigger_test", {"bug_id": "bad"})
        self.assertEqual(response.status_code, 400)

    def test_trigger_test_unknown_project(self):
        response = self.post_json("/trigger_test", {"bug_id": "fake@00000"})
        body = response.json()
        self.assertNotEqual(body.get("returncode", 1), 0)

    # ═══════════════════════════════════════════════════════════════
    #  /regression_test (phase endpoint)
    # ═══════════════════════════════════════════════════════════════

    def test_regression_test_invalid_bug_id(self):
        response = self.post_json("/regression_test", {"bug_id": "bad"})
        self.assertEqual(response.status_code, 400)

    def test_regression_test_unknown_project(self):
        response = self.post_json("/regression_test", {"bug_id": "fake@00000"})
        body = response.json()
        self.assertNotEqual(body.get("returncode", 1), 0)

    # ═══════════════════════════════════════════════════════════════
    #  /reproduce (phase endpoint)
    # ═══════════════════════════════════════════════════════════════

    def test_reproduce_invalid_bug_id(self):
        response = self.post_json("/reproduce", {"bug_id": "bad"})
        self.assertEqual(response.status_code, 400)

    def test_reproduce_unknown_project(self):
        response = self.post_json("/reproduce", {"bug_id": "fake@00000"})
        body = response.json()
        self.assertNotEqual(body.get("returncode", 1), 0)

    def test_reproduce_with_force_cleanup(self):
        bug_id = self._get_first_bug_id()
        if not bug_id:
            self.skipTest("No bugs loaded")
        response = self.post_json("/reproduce",
                                  {"bug_id": bug_id, "is_force_cleanup": True})
        body = response.json()
        self.assertIn("returncode", body)

    # ═══════════════════════════════════════════════════════════════
    #  /build_patch
    # ═══════════════════════════════════════════════════════════════

    def test_build_patch_missing_sha(self):
        response = self.post_json("/build_patch", {
            "bug_id": "fake___project@0000000000",
            "llm_response": "```cpp\nint x = 0;\n```",
            "method": "direct",
        })
        data = response.json()
        self.assertFalse(data["success"])

    def test_build_patch_missing_source(self):
        bug_id = self._get_first_bug_id()
        if not bug_id:
            self.skipTest("No bugs loaded")
        response = self.post_json("/build_patch", {
            "bug_id": bug_id,
            "llm_response": "```cpp\nint x = 0;\n```",
            "method": "direct",
        })
        data = response.json()
        self.assertIn("success", data)

    def test_build_patch_invalid_bug_id(self):
        response = self.post_json("/build_patch", {
            "bug_id": "no_at_sign",
            "llm_response": "fix",
        })
        data = response.json()
        self.assertFalse(data["success"])
        self.assertIn("error", data)

    def test_build_patch_empty_llm_response(self):
        bug_id = self._get_first_bug_id()
        if not bug_id:
            self.skipTest("No bugs loaded")
        response = self.post_json("/build_patch", {
            "bug_id": bug_id,
            "llm_response": "",
            "method": "direct",
        })
        data = response.json()
        self.assertIn("success", data)

    # ═══════════════════════════════════════════════════════════════
    #  /fix + /status
    # ═══════════════════════════════════════════════════════════════

    def test_fix_returns_handle(self):
        bug_id = self._get_first_bug_id()
        if not bug_id:
            self.skipTest("No bugs loaded")
        response = self.post_json("/fix", {
            "bug_id": bug_id,
            "patch_path": "/tmp/fake_patch.cpp",
        })
        data = response.json()
        self.assertIn("handle", data)
        self.assertTrue(len(data["handle"]) > 0)

    def test_fix_invalid_bug_id(self):
        response = self.post_json("/fix", {
            "bug_id": "no_at_sign",
            "patch_path": "/tmp/fake.cpp",
        })
        self.assertEqual(response.status_code, 400)

    def test_status_not_found(self):
        response = self.session.get(
            self.url("/status/nonexistent_handle_12345"),
            timeout=self.TIMEOUT,
        )
        self.assertEqual(response.status_code, 404)

    def test_fix_and_poll_status(self):
        bug_id = self._get_first_bug_id()
        if not bug_id:
            self.skipTest("No bugs loaded")
        fix_resp = self.post_json("/fix", {
            "bug_id": bug_id,
            "patch_path": "/tmp/fake_nonexistent_patch.cpp",
        })
        handle = fix_resp.json()["handle"]

        status_data = None
        for _ in range(10):
            time.sleep(0.5)
            status_resp = self.session.get(
                self.url(f"/status/{handle}"), timeout=self.TIMEOUT)
            self.assertEqual(status_resp.status_code, 200)
            status_data = status_resp.json()
            self.assertIn("status", status_data)
            self.assertIn("bug_id", status_data)
            if status_data["status"] in ("completed", "failed"):
                break

        self.assertIn(status_data["status"],
                      ("queued", "running", "completed", "failed"))

    # ═══════════════════════════════════════════════════════════════
    #  /validate_oracle
    # ═══════════════════════════════════════════════════════════════

    def test_validate_oracle_invalid_bug_id(self):
        response = self.post_json("/validate_oracle", {
            "bug_id": "fake@00000",
            "mode": "fix",
        })
        data = response.json()
        self.assertFalse(data["success"])

    def test_validate_oracle_invalid_mode(self):
        bug_id = self._get_first_bug_id()
        if not bug_id:
            self.skipTest("No bugs loaded")
        response = self.post_json("/validate_oracle", {
            "bug_id": bug_id,
            "mode": "invalid_mode",
        })
        data = response.json()
        self.assertFalse(data["success"])

    def test_validate_oracle_no_repo(self):
        bug_id = self._get_first_bug_id()
        if not bug_id:
            self.skipTest("No bugs loaded")
        response = self.post_json("/validate_oracle", {
            "bug_id": bug_id,
            "mode": "fix",
        })
        data = response.json()
        if not data["success"]:
            self.assertIn("error", data)

    def test_validate_oracle_buggy_mode_no_repo(self):
        bug_id = self._get_first_bug_id()
        if not bug_id:
            self.skipTest("No bugs loaded")
        response = self.post_json("/validate_oracle", {
            "bug_id": bug_id,
            "mode": "buggy",
        })
        data = response.json()
        if not data["success"]:
            self.assertIn("error", data)


if __name__ == "__main__":
    unittest.main(verbosity=2)

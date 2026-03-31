"""
test_shell_scripts.py — Tests for defects4c.sh and webapp_replay.sh.

These tests validate the shell-based CLI tools that wrap the HTTP API.
They require a running webapp at D4C_WEBAPP_URL.

Run:
    D4C_WEBAPP_URL=http://127.0.0.1:8092 python -m pytest tests/test_shell_scripts.py -v
"""
import os
import subprocess
import pytest

SCRIPT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)))
BASE_URL = os.environ.get("D4C_WEBAPP_URL", "http://127.0.0.1:8092")


def _run_script(script, args=None, env_override=None, timeout=30):
    """Run a shell script and return (returncode, stdout, stderr)."""
    env = {**os.environ, "DEFECTS4C_URL": BASE_URL, "D4C_WEBAPP_URL": BASE_URL}
    if env_override:
        env.update(env_override)
    cmd = ["bash", script] + (args or [])
    result = subprocess.run(
        cmd, capture_output=True, encoding="utf-8", errors="replace",
        cwd=SCRIPT_DIR, env=env, timeout=timeout,
    )
    return result.returncode, result.stdout, result.stderr


# ═══════════════════════════════════════════════════════════════
#  defects4c.sh
# ═══════════════════════════════════════════════════════════════

class TestDefects4cSh:
    """Tests for the defects4c.sh CLI wrapper."""

    SCRIPT = os.path.join(SCRIPT_DIR, "defects4c.sh")

    @pytest.fixture(autouse=True)
    def skip_if_no_script(self):
        if not os.path.isfile(self.SCRIPT):
            pytest.skip(f"Script not found: {self.SCRIPT}")

    def test_no_args_shows_usage(self):
        rc, out, err = _run_script(self.SCRIPT)
        assert rc == 1
        assert "usage:" in (out + err).lower()

    def test_pids(self):
        rc, out, err = _run_script(self.SCRIPT, ["pids"])
        # If service is unreachable, rc=127 and message says so
        if rc == 127:
            pytest.skip("Webapp not reachable")
        assert rc == 0
        assert "___" in out  # project names contain ___

    def test_info_project(self):
        rc, out, err = _run_script(self.SCRIPT,
                                   ["info", "-p", "danmar___cppcheck"], timeout=15)
        if rc == 127:
            pytest.skip("Webapp not reachable")
        assert rc == 0
        assert "danmar___cppcheck" in out

    def test_info_with_sha(self):
        rc, out, err = _run_script(self.SCRIPT,
                                   ["info", "-p", "danmar___cppcheck",
                                    "-v", "caa6ff7c2a6ef64df53e04701944aaa4712a1915"],
                                   timeout=15)
        if rc == 127:
            pytest.skip("Webapp not reachable")
        assert rc == 0
        assert "Source file" in out

    def test_unknown_server_returns_error(self):
        rc, out, err = _run_script(
            self.SCRIPT, ["pids"],
            env_override={"DEFECTS4C_URL": "http://127.0.0.1:19999"},
            timeout=10,
        )
        assert rc == 127
        assert "cannot reach" in err.lower()

    def test_checkout(self):
        rc, out, err = _run_script(self.SCRIPT,
                                   ["checkout", "-p", "danmar___cppcheck",
                                    "-v", "caa6ff7c2a6ef64df53e04701944aaa4712a1915"],
                                   timeout=30)
        if rc == 127:
            pytest.skip("Webapp not reachable")
        # May succeed or fail depending on state, but should not crash
        assert rc in (0, 1)


# ═══════════════════════════════════════════════════════════════
#  webapp_replay.sh
# ═══════════════════════════════════════════════════════════════

class TestWebappReplaySh:
    """Tests for webapp_replay.sh curl-based smoke test."""

    SCRIPT = os.path.join(SCRIPT_DIR, "webapp_replay.sh")

    @pytest.fixture(autouse=True)
    def skip_if_no_script(self):
        if not os.path.isfile(self.SCRIPT):
            pytest.skip(f"Script not found: {self.SCRIPT}")

    def test_replay_runs_without_crash(self):
        """The replay script should complete without crashing."""
        rc, out, err = _run_script(self.SCRIPT, [BASE_URL], timeout=120)
        # May have failures for long ops, but should output DONE
        assert "DONE" in out or "DONE" in err or rc == 0

    def test_replay_health_section(self):
        """At minimum, health check should succeed."""
        rc, out, err = _run_script(self.SCRIPT, [BASE_URL], timeout=120)
        assert "GET /health" in out
        assert '"status": "ok"' in out or "'status': 'ok'" in out

"""
d4c_client.py — HTTP client for Defects4C.

Drop-in replacement for d4j_client.py from the RepairAgent.
Same function signatures and return types.
"""

import base64
import logging
import requests

logger = logging.getLogger("d4c_client")

D4C_URL = "http://127.0.0.1:11111"
CONTAINER_WORKSPACE = "/workspace"


def set_url(url):
    global D4C_URL
    D4C_URL = url


def d4j_exec(args, cwd=None):
    """Run a defects4c subcommand.  Returns (returncode, stdout, stderr)."""
    cwd = cwd or CONTAINER_WORKSPACE
    try:
        resp = requests.post(f"{D4C_URL}/api/exec",
                             json={"args": args, "cwd": cwd}, timeout=1800)
        data = resp.json()
        return data.get("returncode", 1), data.get("stdout", ""), data.get("stderr", "")
    except Exception as e:
        return 1, "", str(e)


def d4j_shell(cmd, cwd=None):
    """Run a shell command.  Returns (returncode, stdout, stderr)."""
    cwd = cwd or CONTAINER_WORKSPACE
    try:
        resp = requests.post(f"{D4C_URL}/api/exec-shell",
                             json={"cmd": cmd, "cwd": cwd}, timeout=1800)
        data = resp.json()
        return data.get("returncode", 1), data.get("stdout", ""), data.get("stderr", "")
    except Exception as e:
        return 1, "", str(e)


def d4j_upload(local_path, container_path):
    """Upload a file via base64 (no multipart)."""
    try:
        with open(local_path, "rb") as f:
            content = base64.b64encode(f.read()).decode()
        resp = requests.post(f"{D4C_URL}/api/upload", json={
            "path": container_path,
            "content_base64": content,
        }, timeout=60)
        return resp.json()
    except Exception as e:
        return {"error": str(e)}

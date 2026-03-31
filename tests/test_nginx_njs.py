"""Tests for nginx___njs (11 bugs)."""
import pytest

PROJECT = "nginx___njs"
BUGS = [
    "d457c9545e7e71ebb5c0479eb16b9d33175855e2",
    "39e8fa1b7db1680654527f8fa0e9ee93b334ecba",
    "ad48705bf1f04b4221a5f5b07715ac48b3160d53",
    "f65981b0b8fcf02d69a40bc934803c25c9f607ab",
    "222d6fdcf0c6485ec8e175f3a7b70d650c234b4e",
    "ab1702c7af9959366a5ddc4a75b4357d4e9ebdc1",
    "8b39afdad9a0761e0a5d4af1a762bd9a6daef572",
    "2e00e95473861846aa8538be87db07699d9f676d",
    "eafe4c7a326b163612f10861392622b5da5b1792",
    "81af26364c21c196dd21fb5e14c7fa9ce7debd17",
    "5c6130a2a0b4c41ab415f6b8992aa323636338b9",
]

class Testnginx_njs:
    """Tests for nginx___njs."""

    def test_info_project(self, session, base_url, timeout):
        r = session.post(f"{base_url}/api/exec", json={"args":["info","-p",PROJECT]}, timeout=timeout)
        assert r.json()["returncode"] == 0
        assert PROJECT in r.json()["stdout"]

    def test_bids(self, session, base_url, timeout):
        r = session.post(f"{base_url}/api/exec", json={"args":["bids","-p",PROJECT]}, timeout=timeout)
        assert r.json()["returncode"] == 0

    @pytest.mark.parametrize("sha", BUGS)
    def test_info_bug(self, session, base_url, timeout, sha):
        r = session.post(f"{base_url}/api/exec", json={"args":["info","-p",PROJECT,"-v",sha]}, timeout=timeout)
        assert r.json()["returncode"] == 0
        assert "Source file" in r.json()["stdout"]

    @pytest.mark.parametrize("sha", BUGS)
    def test_get_defect(self, session, base_url, timeout, sha):
        r = session.get(f"{base_url}/get_defect/{PROJECT}@{sha}", timeout=timeout)
        assert r.status_code == 200
        assert r.json()["status"] == "success"

    @pytest.mark.parametrize("sha", BUGS[:1])
    def test_info_short_sha(self, session, base_url, timeout, sha):
        short = sha[:7]
        r = session.post(f"{base_url}/api/exec", json={"args":["info","-p",PROJECT,"-v",short]}, timeout=timeout)
        assert r.json()["returncode"] == 0

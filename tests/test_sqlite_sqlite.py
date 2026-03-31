"""Tests for sqlite___sqlite (6 bugs)."""
import pytest

PROJECT = "sqlite___sqlite"
BUGS = [
    "522ebfa7cee96fb325a22ea3a2464a63485886a8",
    "e59c562b3f6894f84c715772c4b116d7b5c01348",
    "ebd70eedd5d6e6a890a670b5ee874a5eae86b4dd",
    "926f796e8feec15f3836aa0a060ed906f8ae04d3",
    "54d501092d88c0cf89bec4279951f548fb0b8618",
    "a6c1a71cde082e09750465d5675699062922e387",
]

class Testsqlite_sqlite:
    """Tests for sqlite___sqlite."""

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

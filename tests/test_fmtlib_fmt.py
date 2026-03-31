"""Tests for fmtlib___fmt (14 bugs)."""
import pytest

PROJECT = "fmtlib___fmt"
BUGS = [
    "6a1346405949ca1cf5befc5e83c6c66c86e4f9d1",
    "611cf0b3c644d33b9d347b824bf06bd594bfa564",
    "287eaab3b2777daa5d0d0cf72d977196ba54efb7",
    "0cc73ebf79ec39ba74f3d31a76c0acb2df824908",
    "fc6e0fe992156935bfb66bb2121c62ee8446758e",
    "96c18b26c28bbdb8305b79be4eacfb28ee4aa872",
    "279d698e1b37f3f0a9b5c21b12198894d31c381d",
    "c1d430e61ab306e0e1d454fea6e07b4f66667a65",
    "cd7202e0399677942a5428a7db9dec1d3c8b5c45",
    "971fb584c3ea548c12dcfa813b0bafb94e1c0fde",
    "e6b37b4aff1a673cb735b9b287aa637142120f6d",
    "6b7bfed40c559c06d2a4e074427dbc24819bd447",
    "c04fb91b03cb6480c4a39b214efb6b05e452b20c",
    "2b7a146fa1f91ee3d7ebc6a782663185543bc373",
]

class Testfmtlib_fmt:
    """Tests for fmtlib___fmt."""

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

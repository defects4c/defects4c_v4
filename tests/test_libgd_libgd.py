"""Tests for libgd___libgd (17 bugs)."""
import pytest

PROJECT = "libgd___libgd"
BUGS = [
    "2bb97f407c1145c850416a3bfbcc8cf124e68a19",
    "77f619d48259383628c3ec4654b1ad578e9eb40e",
    "58b6dde319c301b0eae27d12e2a659e067d80558",
    "fb0e0cce0b9f25389ab56604c3547351617e1415",
    "fe9ed49dafa993e3af96b6a5a589efeea9bfb36f",
    "1846f48e5fcdde996e7c27a4bbac5d0aef183e4b",
    "69d2fd2c597ffc0c217de1238b9bf4d4bceba8e6",
    "a93eac0e843148dc2d631c3ba80af17e9c8c860f",
    "2bb97f407c1145c850416a3bfbcc8cf124e68a19",
    "77f619d48259383628c3ec4654b1ad578e9eb40e",
    "fd623025505e87bba7ec8555eeb72dae4fb0afdc",
    "58b6dde319c301b0eae27d12e2a659e067d80558",
    "fb0e0cce0b9f25389ab56604c3547351617e1415",
    "fe9ed49dafa993e3af96b6a5a589efeea9bfb36f",
    "1846f48e5fcdde996e7c27a4bbac5d0aef183e4b",
    "69d2fd2c597ffc0c217de1238b9bf4d4bceba8e6",
    "a93eac0e843148dc2d631c3ba80af17e9c8c860f",
]

class Testlibgd_libgd:
    """Tests for libgd___libgd."""

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

"""Tests for KhronosGroup___SPIRV-Tools (11 bugs)."""
import pytest

PROJECT = "KhronosGroup___SPIRV-Tools"
BUGS = [
    "6a9be627c760cf1efa43d155d4e6ee5e801deba3",
    "948577c5df3a6f0d337ab417b26edcc344eb65e2",
    "54385458ca2f7a7b06699e441822753dbc2d018d",
    "ab3cdcaef56e9311f299eebfd044f9646100c9dc",
    "286b3095dd187da747f85baf9ce3120580565df0",
    "4fa1a6f9b497193e54a814112f17ab3c2cf58053",
    "0a43a84e02245cca40ce187d1e427a5d0b4f3d13",
    "0391d0823ebfd7c37c07a54b8726cc417183a95f",
    "0a5d99d02cb8c0edd41c7a9d4f309c0974d076d0",
    "d5a3bfcf2ffd154a244e7ffae54dd1766d98efa4",
    "0ad83f9139daf70a791560f5c72f94b0be5b8390",
]

class TestKhronosGroup_SPIRV_Tools:
    """Tests for KhronosGroup___SPIRV-Tools."""

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

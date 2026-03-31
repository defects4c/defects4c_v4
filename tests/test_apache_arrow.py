"""Tests for apache___arrow (9 bugs)."""
import pytest

PROJECT = "apache___arrow"
BUGS = [
    "68e0fa7499876fc0cf86b8be784a890226648645",
    "eea4a54f66a36137f4204876a68e6d5ed913cbc5",
    "0b4fa2a2bf80bf3a91f9f8f42fe313f78b8a1282",
    "db004443e631fd72c0fd9a16a02294cd14b456e5",
    "171e8bfe5fe13467a1763227e495fae6bc5d011d",
    "4a7e19e118907d0b1c7e1505697a5b74a541c9f7",
    "3d0a9d58b6fe29dcb208c3fa244c789449517988",
    "c4f8436e2532524c2a2f9cc26f73c6b7dfababaa",
    "912e2bb3345536c271580e3a9e88baaffb7f2836",
]

class Testapache_arrow:
    """Tests for apache___arrow."""

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

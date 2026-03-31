"""Tests for CESNET___libyang (15 bugs)."""
import pytest

PROJECT = "CESNET___libyang"
BUGS = [
    "ea0f96cf45deed39fb98b28f30d0acdc304db243",
    "509d721c2b61088c1e491c330c48b0cc01dc191e",
    "7c7783df75b9a5dfc6cc22c70b9467d47aa913d2",
    "cdcace7230352a25eb21a488e7f42d004b3de3e1",
    "350a6bf69e03d19fe996cba992b49556ae2ce8ab",
    "b6ecaeaa0391745ec9054cc4351ac4049317576c",
    "a353cce850560bea761d67a2fbcaf9cb271f586f",
    "fff4dca0da454cdff4c8fab70b4a62c0674fb862",
    "812a5bff4857a485db96fe7269af944d0bdb91b3",
    "140ede9c075c604632a87ee3bf0e881fb485d0e7",
    "9cdb9e6f60d6bae42675bc3b46f5741e50b62e68",
    "92cc8517fcb85dcfbb93842758571f721c49c9cb",
    "f128972045a5d4a36854ddc0a75d99803ff81f6d",
    "24bd22f5e40f37fb6ae796bb8eebc9de58bae00e",
    "823fbe0f7a7b50b7c80cfd948eb3c189579126ab",
]

class TestCESNET_libyang:
    """Tests for CESNET___libyang."""

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

"""Tests for the-tcpdump-group___tcpdump (47 bugs)."""
import pytest

PROJECT = "the-tcpdump-group___tcpdump"
BUGS = [
    "f76e7feb41a4327d2b0978449bbdafe98d4a3771",
    "6f5ba2b651cd9d4b7fa8ee5c4f94460645877c45",
    "2b62d1dda41590db29368ec7ba5f4faf3464765a",
    "8934a7d6307267d301182f19ed162563717e29e3",
    "e942fb84fbe3a73a98a00d2a279425872b5fb9d2",
    "ffde45acf3348f8353fb4064a1b21683ee6b5ddf",
    "3a76fd7c95fced2c2f8c8148a9055c3a542eff29",
    "3b32029db354cbc875127869d9b12a9addc75b50",
    "979dcefd7b259e9e233f77fe1c5312793bfd948f",
    "6fca58f5f9c96749a575f52e20598ad43f5bdf30",
    "7a923447fd49a069a0fd3b6c3547438ab5ee2123",
    "a7e5f58f402e6919ec444a57946bade7dfd6b184",
    "8512734883227c11568bb35da1d48b9f8466f43f",
    "42073d54c53a496be40ae84152bbfe2c923ac7bc",
    "b45a9a167ca6a3ef2752ae9d48d56ac14b001bfd",
    "ca336198e8bebccc18502de27672fdbd6eb34856",
    "5edf405d7ed9fc92f4f43e8a3d44baa4c6387562",
    "8509ef02eceb2bbb479cea10fe4a7ec6395f1a8b",
    "985122081165753c7442bd7824c473eb9ff56308",
    "11b426ee05eb62ed103218526f1fa616851c43ce",
    "26a6799b9ca80508c05cac7a9a3bef922991520b",
    "4601c685e7fd19c3724d5e499c69b8d3ec49933e",
    "c5dd7bef5e54da5996dc4713284aa6266ae75b75",
    "67c7126062d59729cd421bb38f9594015c9907ba",
    "a77ff09c46560bc895dea11dc9fe643486b056ac",
    "2d669862df7cd17f539129049f6fb70d17174125",
    "1bc78d795cd5cad5525498658f414a11ea0a7e9c",
    "b8e559afaeb8fe0604a1f8e3ad4dc1445de07a00",
    "7d3aba9f06899d0128ef46e8a2fa143c6fad8f62",
    "29e5470e6ab84badbc31f4532bb7554a796d9d52",
    "da6f1a677bfa4476abaeaf9b1afe1c4390f51b41",
    "571a6f33f47e7a2394fa08f925e534135c29cf1e",
    "88b2dac837e81cf56dce05e6e7b5989332c0092d",
    "7335163a6ef82d46ff18f3e6099a157747241629",
    "f4b9e24c7384d882a7f434cc7413925bf871d63e",
    "39582c04cc5e34054b2936b423072fb9df2ff6ef",
    "c2f6833dddecf2d5fb89c9c898eee9981da342ed",
    "d10a0f980fe8f9407ab1ffbd612641433ebe175e",
    "aa0858100096a3490edf93034a80e66a4d61aad5",
    "3c8a2b0e91d8d8947e89384dacf6b54673083e71",
    "331530a4076c69bbd2e3214db6ccbe834fb75640",
    "289c672020280529fd382f3502efab7100d638ec",
    "e6511cc1a950fe1566b2236329d6b4bd0826cc7a",
    "bd4e697ebd6c8457efa8f28f6831fc929b88a014",
    "5d0d76e88ee2d3236d7e032589d6f1d4ec5f7b1e",
    "0cb1b8a434b599b8d636db029aadb757c24e39d6",
    "061e7371a944588f231cb1b66d6fb070b646e376",
]

class Testthe_tcpdump_group_tcpdump:
    """Tests for the-tcpdump-group___tcpdump."""

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

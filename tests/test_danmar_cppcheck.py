"""Tests for danmar___cppcheck (31 bugs)."""
import pytest

PROJECT = "danmar___cppcheck"
BUGS = [
    "099b4435c38dd52ddb38e6b1706d9c988699c082",
    "caa6ff7c2a6ef64df53e04701944aaa4712a1915",
    "4996ec190ecf27a4bf018eb0dcd12e2a51fd550e",
    "d0b6079a832d5c156af1e51274e09f28ee8677a7",
    "4ad90bf6f1cb0149c79324dff027cf8558c041c1",
    "2daf7f5430f11def5fd1d67598487369f97c42f4",
    "46ac0d79c1036afb0c565e2bc330d9eb5ffa9eb1",
    "290563b9640505d140684587e5c21e887d510495",
    "0c6aabe4445f097539a5d1f2a73815e69ce3d52f",
    "53734a3da1dd394aee9398127692b0e38e9ffa9f",
    "0c659a149953b9a67901a5b30259b7ef534bbaad",
    "68d77b73da0d86b8d18bf6fcbbf45e77a29a6b26",
    "797de4ef920dda5c569e5fd74e27731bc32053dd",
    "0ee3f678b52d0203d4b84abf65e5cac92f26a553",
    "26bd863d0a4c5499258c9672fbb945343c3901dd",
    "f5dbfce8ffb3132bb49a155527a620c98e61b4a1",
    "3f1e2b42700a1eb1466d4200eb87265defd962f6",
    "6a81b4c17c87241c6f205e9d47ebc9b5af7f0f38",
    "6f2879a59b2c71b1fdb472471412c4b73184fea0",
    "c4dcfef38564e97442fb6c122f5e67c908e0c665",
    "81a1d744c6e9d13f2fac894c380caafb270741df",
    "4779f0e1725c5807b329798e12dbeaddbf568b65",
    "f1169bf2b43fc1beab0da21e430adaa0a7a9eb49",
    "d909ac856569e39cb2f53b158c6246ac82d461b7",
    "a0c37ceba27179496fa2f44072f85e2a5448216e",
    "272760f9cac187bd16ab4a596a0cab870ceb42be",
    "398fa280213771a0fa7a649a54dd6e6625d48e20",
    "d2284ddbcd2a70b4a39047ae32b1c5662060407f",
    "fd3cb2497364d350632c288ce3771738499f718e",
    "192c30ab1d3ec97ae7c6955d8953fac133b8e4ff",
    "ea2916a3e49934d482cd9b30b520c7873db4de82",
]

class Testdanmar_cppcheck:
    """Tests for danmar___cppcheck."""

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

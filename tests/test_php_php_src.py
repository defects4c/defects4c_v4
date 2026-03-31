"""Tests for php___php-src (18 bugs)."""
import pytest

PROJECT = "php___php-src"
BUGS = [
    "28a6ed9f9a36b9c517e4a8a429baf4dd382fc5d5",
    "abd159cce48f3e34f08e4751c568e09677d5ec9c",
    "a44c89e8af7c2410f4bfc5e097be2a5d0639a60c",
    "7722455726bec8c53458a32851d2a87982cf0eac",
    "426aeb2808955ee3d3f52e0cfb102834cdb836a5",
    "698a691724c0a949295991e5df091ce16f899e02",
    "6dbb1ee46b5f4725cc6519abf91e512a2a10dfed",
    "1bd103df00f49cf4d4ade2cfe3f456ac058a4eae",
    "28022c9b1fd937436ab67bb3d61f652c108baf96",
    "b88393f08a558eec14964a55d3c680fe67407712",
    "b2af4e8868726a040234de113436c6e4f6372d17",
    "863d37ea66d5c960db08d6f4a2cbd2518f0f80d1",
    "ca46d0acbce55019b970fcd4c1e8a10edfdded93",
    "b28b8b2fee6dfa6fcd13305c581bb835689ac3be",
    "1cda0d7c2ffb62d8331c64e703131d9cabdc03ea",
    "8d2539fa0faf3f63e1d1e7635347c5b9e777d47b",
    "bab0b99f376dac9170ac81382a5ed526938d595a",
    "523f230c831d7b33353203fa34aee4e92ac12bba",
]

class Testphp_php_src:
    """Tests for php___php-src."""

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

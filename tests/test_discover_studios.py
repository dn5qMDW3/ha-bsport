"""Guard-rail tests for scripts/discover_studios.py.

The scraper feeds an automated PR. These tests pin two things: a failed or
empty scrape must never turn into a PR that wipes KNOWN_STUDIOS (which is
exactly what happened when APKPure started returning 403), and the Play
listing parser keeps working against captured real responses.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import discover_studios as ds


def _const_with(n: int) -> str:
    rows = "\n".join(f'    ({i}, "Studio {i}"),' for i in range(1, n + 1))
    return f"# header\n{ds._TUPLE_PREFIX}(\n{rows}\n)\n# footer\n"


@pytest.fixture
def const_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "const.py"
    path.write_text(_const_with(10))
    monkeypatch.setattr(ds, "CONST_PATH", path)
    return path


def test_scrape_raises_when_dev_page_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ds, "_fetch", lambda url, **kw: None)
    with pytest.raises(ds.DiscoveryError):
        ds._scrape_play("US")


def test_main_fails_and_leaves_const_untouched_when_nothing_discovered(
    const_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = const_file.read_text()
    monkeypatch.setattr(ds, "discover", dict)
    monkeypatch.setattr(sys, "argv", ["discover_studios.py", "--update-const"])
    assert ds.main() != 0
    assert const_file.read_text() == before


def test_main_fails_when_discovery_raises(
    const_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom() -> dict[int, str]:
        raise ds.DiscoveryError("page 1 unavailable")

    monkeypatch.setattr(ds, "discover", boom)
    monkeypatch.setattr(sys, "argv", ["discover_studios.py", "--update-const"])
    assert ds.main() != 0


# -- Google Play developer listing -------------------------------------------

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "play"


def test_parse_dev_page_yields_apps_bl_and_token() -> None:
    apps, token, bl = ds._parse_play_dev_page((FIXTURES / "dev_page.html").read_text())
    assert len(apps) >= 5
    assert all(pkg.startswith("com.bsport_") for pkg in apps)
    assert all(name.strip() for name in apps.values())
    assert token and token.startswith("IABiq")
    assert bl.startswith("boq_playuiserver_")


def test_parse_batch_response_yields_apps_and_next_token() -> None:
    apps, token = ds._parse_play_batch((FIXTURES / "batchexecute_page.txt").read_text())
    assert 8 <= len(apps) <= 12
    assert all(name.strip() for name in apps.values())
    assert token and token.startswith("IABiq")


def test_company_id_from_package_accepts_simple_and_bis_only() -> None:
    assert ds._company_id_from_package("com.bsport_538") == 538
    assert ds._company_id_from_package("com.bsport_2479_bis") == 2479
    # Two numeric segments are ambiguous (38 maps to two different studios
    # on Play), so they must be left for a human.
    assert ds._company_id_from_package("com.bsport_38_72") is None
    assert ds._company_id_from_package("com.bsport_38_549.v2") is None
    assert ds._company_id_from_package("com.example") is None


def test_update_const_merges_additively(const_file: Path) -> None:
    changed = ds._update_const({11: "New Studio", 3: "Renamed Studio 3"})
    assert changed is True
    text = const_file.read_text()
    assert '(11, "New Studio"),' in text
    assert '(3, "Studio 3"),' in text, "existing names must not be overwritten"
    assert '(10, "Studio 10"),' in text, "existing entries must not be dropped"


def test_update_const_reports_no_change_when_nothing_new(const_file: Path) -> None:
    before = const_file.read_text()
    assert ds._update_const({3: "Studio 3"}) is False
    assert const_file.read_text() == before

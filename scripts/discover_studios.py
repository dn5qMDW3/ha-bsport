#!/usr/bin/env python3
"""Discover bsport-powered studios by scraping APKPure's developer page.

bsport's Android apps follow the pattern ``com.bsport_<company_id>``.
APKPure's bsport developer directory lists those apps with their display
names, so paginating it gives us a ``(company_id, name)`` mapping without
any credentials or private API access.

Run with ``--update-const`` to rewrite the ``KNOWN_STUDIOS`` tuple in
``custom_components/bsport/const.py``. Without the flag, the script just
prints the list to stdout, safe for CI discovery runs before the
PR-creation step.
"""
from __future__ import annotations

import argparse
import html
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# -- Config ----------------------------------------------------------------

APKPURE_DEV_URL = "https://apkpure.com/developer/bsport?page={page}"
APKPURE_MAX_PAGES = 20  # APKPure returns an empty / repeating page past the end.

REPO_ROOT = Path(__file__).resolve().parent.parent
CONST_PATH = REPO_ROOT / "custom_components" / "bsport" / "const.py"

# User-agent / Accept headers a real browser sends, without these APKPure
# occasionally serves stripped-down markup or 403s.
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.9",
    "Accept-Language": "en-US,en;q=0.9",
}

# Anchors on APKPure repeat "Download APK" / "Read More" sibling links per app
# card. Drop those, they're text-only buttons, not app titles.
_APKPURE_NOISE = {"download apk", "read more", "see more", "all apps"}

# Zero-width / invisible characters that slip into app titles (BOMs, ZWSPs,
# RTL markers) and ruin tuple formatting when pasted into Python source.
_INVISIBLE = "\ufeff\u200b\u200c\u200d\u2060\u00a0"


def _clean_name(name: str) -> str:
    """Normalise an app title to a clean, paste-safe display name."""
    s = html.unescape(name)
    for ch in _INVISIBLE:
        s = s.replace(ch, "")
    # Collapse runs of inner whitespace.
    s = re.sub(r"\s+", " ", s)
    return s.strip()


# Polite throttle between network fetches (seconds).
_THROTTLE = 1.0


def _fetch(url: str, *, timeout: float = 15.0) -> str | None:
    """GET ``url`` with browser-like headers. Returns body on 200, else None.

    Swallows all HTTP/URL errors — the script's design is "do as much as you
    can; don't fail the whole run on one bad page".
    """
    req = urllib.request.Request(url, headers=BROWSER_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            body = resp.read()
    except (urllib.error.URLError, TimeoutError, ConnectionError) as err:
        print(f"    ! fetch failed: {url} — {err}", file=sys.stderr)
        return None
    return body.decode("utf-8", "replace")


def _scrape_apkpure() -> dict[int, str]:
    """Paginate APKPure's developer page until it runs out of new packages.

    Each page holds ~20 apps. APKPure does not 404 past the last page — it
    returns a page with zero ``com.bsport_*`` mentions — so we stop when a
    fetch yields no new ids.
    """
    print("[apkpure] scanning…")
    out: dict[int, str] = {}
    for page in range(1, APKPURE_MAX_PAGES + 1):
        body = _fetch(APKPURE_DEV_URL.format(page=page))
        if body is None:
            break
        new_this_page = 0
        # The page renders each app as:
        #   <a href="...com.bsport_<N>...">...Name...</a>
        # plus sibling "Download APK" / "Read More" anchors with the same
        # package id. The regex captures all three; we filter the noise.
        anchors = re.findall(
            r'<a[^>]*href="[^"]*com\.bsport_(\d+)[^"]*"[^>]*>([^<]{1,120})</a>',
            body,
        )
        for raw_id, raw_text in anchors:
            text = _clean_name(raw_text)
            if not text or text.lower() in _APKPURE_NOISE:
                continue
            if text.isdigit():
                continue
            cid = int(raw_id)
            if cid in out:
                continue
            out[cid] = text
            new_this_page += 1
        print(f"[apkpure] page {page}: {new_this_page} new studios (total {len(out)})")
        if new_this_page == 0:
            break
        time.sleep(_THROTTLE)
    return out


def discover() -> dict[int, str]:
    """Scrape APKPure's bsport developer page and return {company_id: name}."""
    return _scrape_apkpure()


_TUPLE_PREFIX = "KNOWN_STUDIOS: Final[tuple[tuple[int, str], ...]] = "


def _format_known_studios(mapping: dict[int, str]) -> str:
    """Render the tuple back into const.py syntax, sorted by company id."""
    lines = [f"{_TUPLE_PREFIX}("]
    for cid in sorted(mapping):
        name = mapping[cid].replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'    ({cid}, "{name}"),')
    lines.append(")")
    return "\n".join(lines)


def _find_tuple_span(content: str) -> tuple[int, int]:
    """Locate the KNOWN_STUDIOS tuple's [start, end) byte span in const.py.

    Walks paren-balanced with a tiny state machine so it tolerates any
    valid tuple content (nested parens inside strings, escape sequences,
    etc.). Using a regex here was fragile — a previous attempt stopped at
    the first `)` encountered inside an entry row.
    """
    start = content.find(_TUPLE_PREFIX)
    if start == -1:
        raise RuntimeError(
            f"could not find '{_TUPLE_PREFIX}' in const.py — "
            "has its formatting changed?"
        )
    open_paren_idx = start + len(_TUPLE_PREFIX)
    if content[open_paren_idx] != "(":
        raise RuntimeError(
            "expected '(' after KNOWN_STUDIOS prefix; const.py format changed?"
        )

    i = open_paren_idx
    depth = 0
    n = len(content)
    while i < n:
        ch = content[i]
        if ch == "(":
            depth += 1
            i += 1
            continue
        if ch == ")":
            depth -= 1
            i += 1
            if depth == 0:
                return start, i
            continue
        if ch == '"':
            # Skip over a double-quoted string, honouring backslash escapes.
            i += 1
            while i < n:
                if content[i] == "\\":
                    i += 2
                    continue
                if content[i] == '"':
                    i += 1
                    break
                i += 1
            continue
        i += 1
    raise RuntimeError("unterminated KNOWN_STUDIOS tuple")


def _update_const(mapping: dict[int, str]) -> bool:
    """Rewrite const.py's KNOWN_STUDIOS tuple in place. Returns True if the
    file changed."""
    before = CONST_PATH.read_text()
    start, end = _find_tuple_span(before)
    replacement = _format_known_studios(mapping)
    after = before[:start] + replacement + before[end:]
    if after == before:
        return False
    CONST_PATH.write_text(after)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--update-const",
        action="store_true",
        help="rewrite KNOWN_STUDIOS in custom_components/bsport/const.py",
    )
    args = parser.parse_args()

    mapping = discover()
    print(f"\n=== {len(mapping)} bsport studios discovered ===")
    for cid in sorted(mapping):
        print(f"  ({cid:>5}, {mapping[cid]!r}),")

    if args.update_const:
        changed = _update_const(mapping)
        print(
            f"\n[const] {'wrote' if changed else 'no changes to'} "
            f"custom_components/bsport/const.py"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())

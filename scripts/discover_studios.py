#!/usr/bin/env python3
"""Discover bsport-powered studios by scraping public app-store directories.

bsport's Android apps follow the pattern ``com.bsport_<company_id>``. Both
APKPure and Google Play index those apps with their display names, so
enumerating them gives us a ``(company_id, name)`` mapping without any
credentials or private API access.

Two sources, merged:
- APKPure's bsport developer page (primary — much broader coverage).
- Google Play (secondary — catches apps that studios publish under their
  own developer account rather than bsport's turnkey account).

Run with ``--update-const`` to rewrite the ``KNOWN_STUDIOS`` tuple in
``custom_components/bsport/const.py``. Without the flag, the script just
prints the merged list to stdout — safe for CI discovery runs before the
PR-creation step.
"""
from __future__ import annotations

import argparse
import html
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# -- Config ----------------------------------------------------------------

APKPURE_DEV_URL = "https://apkpure.com/developer/bsport?page={page}"
APKPURE_MAX_PAGES = 20  # APKPure returns an empty / repeating page past the end.

PLAY_SEARCH_URL = "https://play.google.com/store/search?q={q}&c=apps&hl=en_US"
PLAY_DETAIL_URL = "https://play.google.com/store/apps/details?id={pkg}&hl=en_US"
# Seed search queries — different queries surface different corners of Play's
# index. Any overlap is fine; dedupe by `company_id` at merge time.
PLAY_QUERIES = (
    "bsport",
    "bsport studio",
    "bsport yoga",
    "bsport pilates",
    "bsport fitness",
    "bsport gym",
    "bsport boxing",
)

REPO_ROOT = Path(__file__).resolve().parent.parent
CONST_PATH = REPO_ROOT / "custom_components" / "bsport" / "const.py"

# User-agent / Accept headers a real browser sends — without these, both sites
# occasionally serve stripped-down markup or 403s.
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.9",
    "Accept-Language": "en-US,en;q=0.9",
}

# Anchors on APKPure repeat "Download APK" / "Read More" sibling links per app
# card. Drop those — they're text-only buttons, not app titles.
_APKPURE_NOISE = {"download apk", "read more", "see more", "all apps"}

# Regex matching the ``com.bsport_<digits>`` package name embedded in URLs.
_PKG_RE = re.compile(r"com\.bsport_(\d+)")

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


def _scrape_play_search(query: str) -> set[str]:
    """Return a set of ``com.bsport_<N>`` package names from one Play SERP."""
    body = _fetch(PLAY_SEARCH_URL.format(q=urllib.parse.quote(query)))
    if body is None:
        return set()
    packages = {f"com.bsport_{m.group(1)}" for m in _PKG_RE.finditer(body)}
    return packages


def _scrape_play_dev_page() -> set[str]:
    """Pull bsport's own developer listing, if we can find it from any
    known app page. Less discovery-breadth than APKPure but authoritative for
    bsport-turnkey studios."""
    # Use the Chimosa app page as a stable anchor for finding the dev id.
    anchor = _fetch(PLAY_DETAIL_URL.format(pkg="com.bsport_538"))
    if anchor is None:
        return set()
    dev_ids = re.findall(r"/store/apps/(?:dev|developer)\?id=([^\"&]+)", anchor)
    if not dev_ids:
        return set()
    dev_url = (
        "https://play.google.com/store/apps/dev?"
        f"id={urllib.parse.quote(dev_ids[0])}&hl=en_US"
    )
    body = _fetch(dev_url)
    if body is None:
        return set()
    return {f"com.bsport_{m.group(1)}" for m in _PKG_RE.finditer(body)}


def _scrape_play() -> dict[int, str]:
    """Combine dev page + all search queries. For each discovered package,
    fetch its details page once to extract the display name."""
    print("[play] scanning…")
    packages: set[str] = set()
    packages |= _scrape_play_dev_page()
    for q in PLAY_QUERIES:
        packages |= _scrape_play_search(q)
        time.sleep(_THROTTLE)
    print(f"[play] {len(packages)} packages across dev page + {len(PLAY_QUERIES)} queries")

    out: dict[int, str] = {}
    for pkg in sorted(packages):
        m = _PKG_RE.search(pkg)
        if not m:
            continue
        cid = int(m.group(1))
        detail = _fetch(PLAY_DETAIL_URL.format(pkg=pkg))
        if detail is None:
            continue
        # Display name is in <meta property="og:title" content="X — Apps on
        # Google Play"> on Play's HTML.
        title_match = re.search(
            r'<meta[^>]*property="og:title"[^>]*content="([^"]+)"', detail,
        )
        if not title_match:
            continue
        name = _clean_name(title_match.group(1))
        name = re.sub(r"\s*[—-]\s*Apps on Google Play.*$", "", name).strip()
        if name and cid not in out:
            out[cid] = name
        time.sleep(_THROTTLE)
    return out


def discover() -> dict[int, str]:
    """Run both sources, merge, APKPure wins on name conflict (better coverage)."""
    apk = _scrape_apkpure()
    play = _scrape_play()
    merged = dict(play)  # Play first so APKPure overrides on conflict.
    merged.update(apk)
    print(
        f"\n[merge] apkpure={len(apk)}  play={len(play)}  "
        f"union={len(merged)}  overlap={len(set(apk) & set(play))}"
    )
    return merged


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

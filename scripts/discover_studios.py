#!/usr/bin/env python3
"""Discover bsport-powered studios from bsport's Google Play developer listing.

bsport's Android apps follow the pattern ``com.bsport_<company_id>``. Google
Play's developer page for bsport lists them with display names, so walking
it gives us ``(company_id, name)`` pairs without credentials or private
API access.

Two properties of that listing shape this script:

- Play caps the developer listing at roughly 150 apps per store country
  and the subset differs by country, so we query several countries and
  union the results. Even the union is far smaller than the historical
  list, which is why const.py is only ever *extended*, never regenerated:
  a studio missing from Play's listing is not evidence it closed.
- A few packages carry two numeric segments (``com.bsport_38_72``). The
  first number is not a unique company id there (38 appears with two
  different studios), so those are printed for a human and skipped.

The initial page carries the first batch of apps plus a continuation token;
further batches come from Play's ``batchexecute`` RPC (id ``qnKhOb``), the
same call the browser makes for "Show more". Captured 2026-09-13.

Run with ``--update-const`` to append any new studios to ``KNOWN_STUDIOS``
in ``custom_components/bsport/const.py``. Without the flag, the script just
prints what it found.
"""
from __future__ import annotations

import argparse
import ast
import html as html_mod
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# -- Config ----------------------------------------------------------------

PLAY_DEVELOPER_ID = "8685682078195488029"
PLAY_DEV_URL = "https://play.google.com/store/apps/dev?id={dev}&hl=en&gl={gl}"
PLAY_BATCH_URL = (
    "https://play.google.com/_/PlayStoreUi/data/batchexecute"
    "?rpcids=qnKhOb&source-path=%2Fstore%2Fapps%2Fdev&bl={bl}&hl=en&gl={gl}"
    "&authuser&soc-app=121&soc-platform=1&soc-device=1&rt=c"
)
# Store countries to union. Each returns a different ~150-app slice.
PLAY_COUNTRIES = ("US", "GB", "FR", "DE", "NL", "CH", "ES", "IT", "BE", "AU")
PLAY_MAX_BATCHES = 60  # ~10 apps per batch; a hard stop against token loops.

# Field mask the Play web client sends with the RPC; copied verbatim from a
# browser capture. Meaning of the numbers is not documented.
_PLAY_FIELDS = [
    96, 108, 72, 100, 27, 177, 183, 222, 8, 57, 169, 110, 11, 184, 16, 1, 139,
    152, 194, 165, 68, 163, 211, 9, 71, 31, 176, 195, 12, 64, 151, 320, 150,
    148, 113, 104, 55, 56, 145, 32, 34, 10, 122,
]

REPO_ROOT = Path(__file__).resolve().parent.parent
CONST_PATH = REPO_ROOT / "custom_components" / "bsport" / "const.py"

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# Zero-width / invisible characters that slip into app titles (BOMs, ZWSPs,
# RTL markers) and ruin tuple formatting when pasted into Python source.
_INVISIBLE = "\ufeff\u200b\u200c\u200d\u2060\u00a0"

# Polite throttle between network fetches (seconds).
_THROTTLE = 0.5

_PACKAGE_RE = re.compile(r"com\.bsport_(\d+)(?:_bis)?")
_TOKEN_RE = re.compile(r"IABiq[A-Za-z0-9_-]{40,}")


class DiscoveryError(RuntimeError):
    """Raised when a scrape cannot be trusted enough to act on."""


def _clean_name(name: str) -> str:
    """Normalise an app title to a clean, paste-safe display name."""
    s = html_mod.unescape(name)
    for ch in _INVISIBLE:
        s = s.replace(ch, "")
    return re.sub(r"\s+", " ", s).strip()


# -- HTTP ------------------------------------------------------------------


def _fetch(url: str, *, data: bytes | None = None, timeout: float = 30.0) -> str | None:
    """GET (or POST when ``data`` is given). Returns the body on 200, else None."""
    headers = dict(BROWSER_HEADERS)
    if data is not None:
        headers.update(
            {
                "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                "X-Same-Domain": "1",
                "Origin": "https://play.google.com",
                "Referer": "https://play.google.com/",
            }
        )
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            body = resp.read()
    except (urllib.error.URLError, TimeoutError, ConnectionError) as err:
        print(f"    ! fetch failed: {url} — {err}", file=sys.stderr)
        return None
    return body.decode("utf-8", "replace")


# -- Google Play parsing ---------------------------------------------------


def _collect_apps(node: object, out: dict[str, str]) -> None:
    """Walk Play's nested-list payload for app entries.

    An app entry looks like ``[["com.bsport_538", 7], <icon>, <shots>,
    "Display Name", ...]`` — package at [0][0], name at [3].
    """
    if not isinstance(node, list):
        return
    head = node[0] if node else None
    if (
        isinstance(head, list)
        and len(head) >= 2
        and isinstance(head[0], str)
        and head[0].startswith("com.bsport_")
        and len(node) > 3
        and isinstance(node[3], str)
    ):
        out[head[0]] = _clean_name(node[3])
    for child in node:
        _collect_apps(child, out)


def _parse_play_dev_page(html: str) -> tuple[dict[str, str], str | None, str]:
    """Return (apps, continuation token, build label) from the dev page HTML."""
    apps: dict[str, str] = {}
    for m in re.finditer(
        r"AF_initDataCallback\(\{key: 'ds:\d+'.*?data:(\[.*?\]), sideChannel", html, re.DOTALL
    ):
        try:
            _collect_apps(json.loads(m.group(1)), apps)
        except json.JSONDecodeError:
            continue
    bl_match = re.search(r'"cfb2h":"([^"]+)"', html)
    if not bl_match:
        raise DiscoveryError("Play dev page has no build label (cfb2h); markup changed?")
    token = re.search(r'"(IABiq[A-Za-z0-9_=-]{40,})"', html)
    return apps, (token.group(1) if token else None), bl_match.group(1)


def _parse_play_batch(raw: str) -> tuple[dict[str, str], str | None]:
    """Return (apps, next token) from a batchexecute response.

    The body is ``)]}'`` followed by length-prefixed JSON chunks; each chunk
    is ``[["wrb.fr", "<rpc id>", "<json payload string>", ...]]``.
    """
    apps: dict[str, str] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("[["):
            continue
        try:
            envelope = json.loads(line)
        except json.JSONDecodeError:
            continue
        for item in envelope:
            if isinstance(item, list) and len(item) > 2 and item[0] == "wrb.fr" and isinstance(item[2], str):
                _collect_apps(json.loads(item[2]), apps)
    token = _TOKEN_RE.search(raw)
    return apps, (token.group(0) if token else None)


def _company_id_from_package(package: str) -> int | None:
    """Map ``com.bsport_<N>`` or ``com.bsport_<N>_bis`` to N; anything else is None."""
    m = _PACKAGE_RE.fullmatch(package)
    return int(m.group(1)) if m else None


# -- Scraping --------------------------------------------------------------


def _scrape_play(gl: str) -> dict[str, str]:
    """Walk the developer listing for one store country: {package: name}."""
    print(f"[play:{gl}] scanning…")
    html = _fetch(PLAY_DEV_URL.format(dev=PLAY_DEVELOPER_ID, gl=gl))
    if html is None:
        raise DiscoveryError(
            f"could not fetch the Play developer page for gl={gl} — refusing "
            "to treat a blocked or unreachable source as an empty studio list"
        )
    apps, token, bl = _parse_play_dev_page(html)
    batches = 0
    while token and batches < PLAY_MAX_BATCHES:
        inner = json.dumps([[None, [[1, [10]], None, None, _PLAY_FIELDS], None, token], [1]])
        form = urllib.parse.urlencode({"f.req": json.dumps([[["qnKhOb", inner, None, "generic"]]])})
        raw = _fetch(PLAY_BATCH_URL.format(bl=bl, gl=gl), data=form.encode())
        if raw is None:
            break
        more, token = _parse_play_batch(raw)
        apps.update(more)
        batches += 1
        time.sleep(_THROTTLE)
    print(f"[play:{gl}] {len(apps)} apps after {batches} batches")
    return apps


def discover() -> dict[int, str]:
    """Union the Play listing across countries and map to {company_id: name}."""
    packages: dict[str, str] = {}
    for gl in PLAY_COUNTRIES:
        packages.update(_scrape_play(gl))
    mapping: dict[int, str] = {}
    skipped: list[tuple[str, str]] = []
    for package, name in sorted(packages.items()):
        cid = _company_id_from_package(package)
        if cid is None:
            skipped.append((package, name))
        elif name and cid not in mapping:
            mapping[cid] = name
    if skipped:
        print("\n[play] packages with ambiguous ids, left for manual review:")
        for package, name in skipped:
            print(f"  {package}: {name!r}")
    return mapping


# -- const.py rewrite ------------------------------------------------------

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
    """Locate the KNOWN_STUDIOS tuple's [start, end) span in const.py.

    Walks paren-balanced so it tolerates any valid tuple content (nested
    parens inside strings, escape sequences, etc.).
    """
    start = content.find(_TUPLE_PREFIX)
    if start == -1:
        raise RuntimeError(f"could not find '{_TUPLE_PREFIX}' in const.py — has its formatting changed?")
    open_paren_idx = start + len(_TUPLE_PREFIX)
    if content[open_paren_idx] != "(":
        raise RuntimeError("expected '(' after KNOWN_STUDIOS prefix; const.py format changed?")

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


def _update_const(discovered: dict[int, str]) -> bool:
    """Append studios missing from const.py's KNOWN_STUDIOS. Returns True if
    the file changed. Existing entries are never renamed or removed: Play's
    listing is partial, so absence there proves nothing."""
    before = CONST_PATH.read_text()
    start, end = _find_tuple_span(before)
    existing: dict[int, str] = dict(ast.literal_eval(before[start + len(_TUPLE_PREFIX) : end]))
    merged = {**discovered, **existing}
    if set(merged) == set(existing):
        return False
    after = before[:start] + _format_known_studios(merged) + before[end:]
    CONST_PATH.write_text(after)
    added = sorted(set(merged) - set(existing))
    print(f"[const] added {len(added)} studios: {added}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--update-const",
        action="store_true",
        help="append new studios to KNOWN_STUDIOS in custom_components/bsport/const.py",
    )
    args = parser.parse_args()

    try:
        mapping = discover()
        print(f"\n=== {len(mapping)} bsport studios discovered ===")
        for cid in sorted(mapping):
            print(f"  ({cid:>5}, {mapping[cid]!r}),")
        if not mapping:
            raise DiscoveryError("scrape returned zero studios — nothing to act on")

        if args.update_const:
            changed = _update_const(mapping)
            print(f"\n[const] {'wrote' if changed else 'no changes to'} custom_components/bsport/const.py")
    except DiscoveryError as err:
        print(f"\n[error] {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

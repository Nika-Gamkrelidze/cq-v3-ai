#!/usr/bin/env python3
"""i18n parity lint for frontend/public/brand.js.

WHY this exists: every user-facing string in the three UIs comes from the `DICT`
object in `brand.js`, which carries `en` / `ka` / `ru` side by side and is edited
BY HAND. `CQ.t()` silently falls back to English when a key is missing, so a
half-translated feature looks fine to the developer (who runs the UI in English)
and ships English text into a Georgian tenant's console. There is no build step
and no framework to catch it. This script is the lint.

It fails (exit 1) on:
  * a key present in one language and missing from another;
  * a key defined twice inside one language block (the second literal silently
    wins, so the first translation is dead code and the two drift apart).

Usage:  python3 scripts/check_i18n.py [path/to/brand.js]

Implementation note: brand.js is not JSON — values contain apostrophes, braces
and colons — so this walks the DICT text as a character stream tracking string
state and brace depth, rather than regexing keys out of it.
"""

from __future__ import annotations

import sys
from pathlib import Path

DEFAULT = Path(__file__).resolve().parent.parent / "frontend" / "public" / "brand.js"


def parse_dict(src: str) -> dict[str, list[str]]:
    """Return {lang: [keys in source order]} for the DICT literal in `src`."""
    start = src.find("const DICT = {")
    if start < 0:
        raise SystemExit("check_i18n: could not find `const DICT = {` in brand.js")
    i = src.index("{", start)

    langs: dict[str, list[str]] = {}
    depth = 0          # 0 = outside DICT, 1 = inside DICT, 2 = inside a language block
    lang: str | None = None
    pending = ""       # identifier being accumulated at depth 1
    last_ident = ""    # the label immediately before a language block opens ("en", "ka", "ru")
    n = len(src)

    while i < n:
        c = src[i]

        # --- string literal: consume it whole, then decide whether it was a key ---
        if c in "'\"`":
            quote, j, buf = c, i + 1, []
            while j < n:
                if src[j] == "\\":
                    buf.append(src[j + 1] if j + 1 < n else "")
                    j += 2
                    continue
                if src[j] == quote:
                    break
                buf.append(src[j])
                j += 1
            literal = "".join(buf)
            k = j + 1
            while k < n and src[k] in " \t\r\n":
                k += 1
            if k < n and src[k] == ":":
                if depth == 2 and lang is not None:
                    langs[lang].append(literal)
                elif depth == 1:
                    last_ident = literal   # a quoted language label, e.g. 'en': { … }
            i = j + 1
            continue

        if c == "{":
            depth += 1
            if depth == 2:
                lang = last_ident.strip() or f"<anonymous:{len(langs)}>"
                langs.setdefault(lang, [])
            pending = ""
            last_ident = ""
            i += 1
            continue

        if c == "}":
            depth -= 1
            if depth <= 1:
                lang = None
            if depth == 0:
                break
            pending = ""
            i += 1
            continue

        if depth == 1:
            # accumulate the bare identifier that precedes a language block ("en", "ka", "ru")
            if c == ":":
                last_ident = pending.strip()
                pending = ""
            elif c == ",":
                pending = ""
            else:
                pending += c
        i += 1

    return langs


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else DEFAULT
    if not path.exists():
        print(f"check_i18n: no such file: {path}", file=sys.stderr)
        return 2

    langs = parse_dict(path.read_text(encoding="utf-8"))
    if len(langs) < 2:
        print(f"check_i18n: expected at least two language blocks, found {list(langs)}", file=sys.stderr)
        return 2

    failures = 0

    # duplicates within a language
    for lang, keys in langs.items():
        seen: set[str] = set()
        dupes: set[str] = set()
        for k in keys:
            (dupes if k in seen else seen).add(k)
        for k in sorted(dupes):
            print(f"DUPLICATE  {lang}: '{k}' is defined more than once (the later one silently wins)")
            failures += 1

    # cross-language parity
    sets = {lang: set(keys) for lang, keys in langs.items()}
    union = set().union(*sets.values())
    for key in sorted(union):
        missing = sorted(lang for lang, ks in sets.items() if key not in ks)
        if missing:
            present = sorted(lang for lang, ks in sets.items() if key in ks)
            print(f"MISSING    '{key}' — present in {', '.join(present)}; missing from {', '.join(missing)}")
            failures += 1

    counts = ", ".join(f"{lang}={len(ks)}" for lang, ks in sets.items())
    if failures:
        print(f"\ncheck_i18n: FAIL — {failures} problem(s). Keys: {counts}", file=sys.stderr)
        return 1
    print(f"check_i18n: OK — {len(union)} keys × {len(sets)} languages in sync ({counts}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

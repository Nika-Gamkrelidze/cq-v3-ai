#!/usr/bin/env python3
"""i18n parity lint for the frontend's dictionaries.

WHY this exists: every user-facing string in the UIs comes from the `DICT` object in
`frontend/public/brand.js` — plus, since the call workbench, the module-local blocks each
feature file registers with `CQ.extendDict({...})` — and all of it carries `en` / `ka` / `ru`
side by side, edited BY HAND. `CQ.t()` silently falls back to English when a key is missing,
so a half-translated feature looks fine to the developer (who runs the UI in English) and
ships English text into a Georgian tenant's console. There is no build step and no framework
to catch it. This script is the lint.

It scans `brand.js`'s `const DICT = {` literal and EVERY `CQ.extendDict({` literal in every
`frontend/public/*.js` file, then fails (exit 1) on:
  * a key present in one language and missing from another (across all sources);
  * a key defined twice inside one language, in the same file or across files (the later
    registration silently wins, so the first translation is dead code and the two drift apart).

Usage:  python3 scripts/check_i18n.py [path/to/frontend/public]     (a brand.js path also works)

Implementation note: the sources are not JSON — values contain apostrophes, braces and colons —
so this walks each literal as a character stream tracking string state and brace depth, rather
than regexing keys out of it.
"""

from __future__ import annotations

import sys
from pathlib import Path

DEFAULT_DIR = Path(__file__).resolve().parent.parent / "frontend" / "public"
DICT_MARKER = "const DICT = {"
EXT_MARKER = "CQ.extendDict("


def parse_object(src: str, i: int) -> dict[str, list[str]]:
    """Parse the {lang: {key: value, ...}, ...} literal whose opening brace is at src[i].

    Returns {lang: [keys in source order]}."""
    if src[i] != "{":
        raise SystemExit(f"check_i18n: expected '{{' at offset {i}")

    langs: dict[str, list[str]] = {}
    depth = 0          # 0 = outside, 1 = inside the literal, 2 = inside a language block
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


def parse_dict(src: str) -> dict[str, list[str]]:
    """The main DICT literal of brand.js (kept for callers of the old single-file API)."""
    start = src.find(DICT_MARKER)
    if start < 0:
        raise SystemExit("check_i18n: could not find `const DICT = {` in brand.js")
    return parse_object(src, src.index("{", start))


def parse_extensions(src: str) -> list[dict[str, list[str]]]:
    """Every `CQ.extendDict({...})` literal in a file, in source order."""
    out, pos = [], 0
    while True:
        start = src.find(EXT_MARKER, pos)
        if start < 0:
            return out
        brace = src.find("{", start + len(EXT_MARKER))
        # Only a literal counts; `CQ.extendDict(someVariable)` cannot be linted and is not allowed.
        between = src[start + len(EXT_MARKER):brace].strip() if brace >= 0 else "x"
        if brace < 0 or between:
            raise SystemExit("check_i18n: CQ.extendDict must be called with an object literal, "
                             f"found `{src[start:start + 40]!r}`")
        out.append(parse_object(src, brace))
        pos = brace + 1


def collect(public_dir: Path) -> dict[str, list[tuple[str, str]]]:
    """{lang: [(key, source label), ...]} across brand.js's DICT and every extension block."""
    found: dict[str, list[tuple[str, str]]] = {}
    brand = public_dir / "brand.js"
    if not brand.exists():
        raise SystemExit(f"check_i18n: no brand.js in {public_dir}")
    for lang, keys in parse_dict(brand.read_text(encoding="utf-8")).items():
        found.setdefault(lang, []).extend((k, "brand.js DICT") for k in keys)
    for js in sorted(public_dir.glob("*.js")):
        for n, block in enumerate(parse_extensions(js.read_text(encoding="utf-8"))):
            label = f"{js.name} extendDict#{n + 1}"
            for lang, keys in block.items():
                found.setdefault(lang, []).extend((k, label) for k in keys)
    return found


def main(argv: list[str]) -> int:
    arg = Path(argv[1]) if len(argv) > 1 else DEFAULT_DIR
    public_dir = arg.parent if arg.is_file() else arg
    if not public_dir.is_dir():
        print(f"check_i18n: no such directory: {public_dir}", file=sys.stderr)
        return 2

    langs = collect(public_dir)
    if len(langs) < 2:
        print(f"check_i18n: expected at least two language blocks, found {list(langs)}", file=sys.stderr)
        return 2

    failures = 0

    # duplicates within a language (same file or across files)
    for lang, entries in langs.items():
        first: dict[str, str] = {}
        for k, where in entries:
            if k in first:
                print(f"DUPLICATE  {lang}: '{k}' defined in {first[k]} and again in {where} "
                      "(the later one silently wins)")
                failures += 1
            else:
                first[k] = where

    # cross-language parity
    sets = {lang: {k for k, _ in entries} for lang, entries in langs.items()}
    union = set().union(*sets.values())
    for key in sorted(union):
        missing = sorted(lang for lang, ks in sets.items() if key not in ks)
        if missing:
            present = sorted(lang for lang, ks in sets.items() if key in ks)
            print(f"MISSING    '{key}' — present in {', '.join(present)}; missing from {', '.join(missing)}")
            failures += 1

    counts = ", ".join(f"{lang}={len(ks)}" for lang, ks in sets.items())
    sources = 1 + sum(1 for js in public_dir.glob("*.js")
                      for _ in parse_extensions(js.read_text(encoding="utf-8")))
    if failures:
        print(f"check_i18n: FAILED — {failures} problem(s) ({counts}; {sources} source block(s)).")
        return 1
    print(f"check_i18n: OK — {len(union)} keys × {len(sets)} languages in sync ({counts}; "
          f"{sources} source block(s)).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

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

The migrated stack's dictionaries (`frontend/next/lib/i18n/**`) are read too, both for parity
across the two stacks and to enforce that exactly one migrated module owns each key — the page
ports run in parallel and that is the only automatic check that two of them have not written
the same string twice. Two further failures are specific to the split and are checked in
`audit_migrated_modules`: a module whose language blocks this script cannot parse, and a module
that is not wired into `lib/i18n/index.ts`. Both are ways for a module to be linted green while
contributing nothing at runtime — see that function for why each is fatal rather than a warning.

Values are compared too, but only for keys that exist in BOTH stacks, and only as a `DRIFT`
report — never a failure. Divergent wording between `brand.js` and a migrated module is
sometimes the point (the port is allowed to improve a string); silently divergent wording is
not, because the user sees a different label depending on which stack served the page.

Usage:  python3 scripts/check_i18n.py [path/to/frontend/public]     (a brand.js path also works)

Implementation note: the sources are not JSON — values contain apostrophes, braces and colons —
so this walks each literal as a character stream tracking string state and brace depth, rather
than regexing keys out of it.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

DEFAULT_DIR = Path(__file__).resolve().parent.parent / "frontend" / "public"
DICT_MARKER = "const DICT = {"
EXT_MARKER = "CQ.extendDict("


def parse_object(src: str, i: int) -> dict[str, list[tuple[str, str]]]:
    """Parse the {lang: {key: value, ...}, ...} literal whose opening brace is at src[i].

    Returns {lang: [(key, value) in source order]}. The value is carried because the drift
    report in main() needs it; a key seen in both stacks is only interesting once you can ask
    whether the two copies still say the same thing."""
    if src[i] != "{":
        raise SystemExit(f"check_i18n: expected '{{' at offset {i}")

    langs: dict[str, list[tuple[str, str]]] = {}
    depth = 0          # 0 = outside, 1 = inside the literal, 2 = inside a language block
    lang: str | None = None
    pending = ""       # identifier being accumulated at depth 1
    last_ident = ""    # the label immediately before a language block opens ("en", "ka", "ru")
    key: str | None = None   # key whose value has not been read yet
    n = len(src)

    while i < n:
        c = src[i]

        # --- comment: skip it whole ---
        # Not optional. A translator's note is the most natural thing to write next to a string
        # ("this differs from brand.js on purpose"), and an apostrophe in one — `don't` — would
        # otherwise open a string literal here and swallow the rest of the block as one value.
        # Safe at this point because string literals are consumed entire, below, so a `//` inside
        # a value (an URL) is never seen by this branch.
        if c == "/" and i + 1 < n and src[i + 1] in "/*":
            end = src.find("\n", i) if src[i + 1] == "/" else src.find("*/", i)
            if end < 0:
                break
            i = end + (1 if src[i + 1] == "/" else 2)
            continue

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
                    key = literal
                elif depth == 1:
                    last_ident = literal   # a quoted language label, e.g. 'en': { … }
            elif depth == 2 and lang is not None and key is not None:
                langs[lang].append((key, literal))
                key = None
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
                # A key still pending here had a value this walker could not read as a string
                # literal. Record it with an empty value rather than dropping it: the parity
                # and ownership checks are about key names and must not lose one silently.
                if key is not None and lang is not None:
                    langs[lang].append((key, ""))
                key = None
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


def parse_dict(src: str) -> dict[str, list[tuple[str, str]]]:
    """The main DICT literal of brand.js (kept for callers of the old single-file API)."""
    start = src.find(DICT_MARKER)
    if start < 0:
        raise SystemExit("check_i18n: could not find `const DICT = {` in brand.js")
    return parse_object(src, src.index("{", start))


def parse_extensions(src: str) -> list[dict[str, list[tuple[str, str]]]]:
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


def migrated_modules(nxt: Path) -> list[Path]:
    """Every TypeScript file the migrated stack keeps dictionaries in, index.ts included."""
    return sorted(p for p in [nxt / "lib" / "i18n.ts", *(nxt / "lib" / "i18n").rglob("*.ts")]
                  if p.is_file())


def collect(public_dir: Path) -> dict[str, list[tuple[str, str, str]]]:
    """{lang: [(key, value, source label), ...]} across brand.js, the extension blocks and the
    migrated modules."""
    found: dict[str, list[tuple[str, str, str]]] = {}

    def add(label: str, blocks: dict[str, list[tuple[str, str]]]) -> None:
        for lang, pairs in blocks.items():
            found.setdefault(lang, []).extend((k, v, label) for k, v in pairs)

    brand = public_dir / "brand.js"
    if not brand.exists():
        raise SystemExit(f"check_i18n: no brand.js in {public_dir}")
    add("brand.js DICT", parse_dict(brand.read_text(encoding="utf-8")))
    for js in sorted(public_dir.glob("*.js")):
        for n, block in enumerate(parse_extensions(js.read_text(encoding="utf-8"))):
            add(f"{js.name} extendDict#{n + 1}", block)

    # The migrated frontend keeps its strings in TypeScript as `const en: Dict = { ... }` per
    # language, split one module per OWNER — lib/i18n/chrome.ts, lib/i18n/features/*.ts,
    # lib/i18n/pages/*.ts — so that page ports running in parallel do not all edit one file.
    # Every one of them is read, because two things have to be checked across the whole set:
    # parity (a key that moved to the new stack in English but not in Georgian is exactly the
    # half-translated feature this lint exists to catch) and ownership (below).
    nxt = public_dir.parent / "next"
    for ts in migrated_modules(nxt):
        add("next:" + ts.relative_to(nxt).as_posix(),
            parse_ts_dicts(ts.read_text(encoding="utf-8")))
    return found


def audit_migrated_modules(nxt: Path) -> list[str]:
    """The two ways a migrated dictionary module can be green here and dead at runtime.

    Both are failure modes the SPLIT created — with one i18n.ts neither was expressible — and
    both are silent in every other tool: `tsc` is happy, the page renders, and the operator is
    the one who finds out, seeing `editor.trimstart` where a Georgian label belongs.

      * A module this script parsed NO language block out of. `parse_ts_dicts` matches the
        literal `const <lang>: Dict = {`, so an author who writes the equally valid
        `export const en = {…} satisfies Dict` drops their module out of the parity AND the
        ownership checks with no diagnostic at all. Nothing at the keyboard tells them the
        spelling is load-bearing, so this has to say so.
      * A module that is never merged into `DICT`. Wiring one in is a line in index.ts, a file
        no page agent owns and every page agent conflicts on, so it is the step that gets lost
        in a rebase. Being imported is not enough — `MODULES` is what `assemble()` reads.

    Fatal, not warnings: each means a shipped page has untranslated strings, which is the exact
    outcome this whole script exists to prevent.

    The rule this implies is that every file under `lib/i18n/` except the barrel and the shim IS
    a dictionary module. That is already the design — index.ts holds no strings and the modules
    hold nothing else — and stating it as a check is what lets "parsed nothing" mean "broken"
    rather than "probably a helper".
    """
    problems: list[str] = []
    index = nxt / "lib" / "i18n" / "index.ts"
    if not index.is_file():
        return [f"UNWIRED    no {index} — the migrated dictionary cannot be checked for wiring"]
    # Comments blanked first: a module parked as `aicfg, // usage,` during a rebase otherwise
    # still matches the identifier and passes this gate while its keys are absent from DICT.
    src = blank_comments(index.read_text(encoding="utf-8"))

    imports = {spec: alias for alias, spec in
               re.findall(r"import\s+\*\s+as\s+([A-Za-z_$][\w$]*)\s+from\s+'(\./[^']+)'", src)}
    listed = re.search(r"const\s+MODULES\b[^=]*=\s*\[(.*?)\]", src, re.S)
    if not listed:
        # Fail closed. A MODULES array this regex cannot see would make every module below look
        # unwired — or, if it were skipped instead, would silently switch the check off.
        return ["UNWIRED    could not find the `const MODULES = [ … ]` array in "
                "lib/i18n/index.ts — this check cannot run; fix the array or this parser"]
    in_modules = set(re.findall(r"[A-Za-z_$][\w$]*", listed.group(1)))

    for ts in migrated_modules(nxt):
        rel = ts.relative_to(nxt).as_posix()
        if ts == index or ts == nxt / "lib" / "i18n.ts":   # the barrel and the shim hold no strings
            continue
        blocks = parse_ts_dicts(ts.read_text(encoding="utf-8"))
        # Every language, each with keys. `if not blocks` is not enough: a module whose English
        # block failed to parse returns {"en": []}, which is TRUTHY — so the one case this guard
        # was added for walked straight through it, losing that language from the parity check
        # with no diagnostic. Name the languages that are missing or empty, because "some of
        # your blocks did not parse" is not actionable at 1am.
        empty = [lang for lang in ("en", "ka", "ru") if not blocks.get(lang)]
        if empty:
            problems.append(f"UNPARSED   next:{rel} declares no usable "
                            f"`const {'/'.join(empty)}: Dict = {{` block — a dictionary module "
                            "must use that exact form, or it is linted by nothing")
            continue
        spec = "./" + ts.relative_to(nxt / "lib" / "i18n").with_suffix("").as_posix()
        alias = imports.get(spec)
        if alias is None:
            problems.append(f"UNWIRED    next:{rel} holds a dictionary but is not imported by "
                            f"lib/i18n/index.ts (expected `import * as … from '{spec}'`)")
        elif alias not in in_modules:
            problems.append(f"UNWIRED    next:{rel} is imported as `{alias}` but is missing from "
                            "the MODULES array in lib/i18n/index.ts, so none of its keys reach "
                            "DICT")
    return problems


def blank_comments(src: str) -> str:
    """Overwrite every comment with spaces, PRESERVING LENGTH and line breaks.

    The character walkers below each skip comments as they go, but two places read the raw
    source directly — the `const <lang>: Dict = {` search here, and the import/MODULES regexes
    in `audit_migrated_modules` — and both were fooled by a comment that merely QUOTES the
    thing they look for. Two demonstrated escapes, both in the form most likely to be typed:

      * A module header quoting the required spelling (`// declare blocks as literally
        \x60const en: Dict = {…}\x60`) matched before the real block, so the parser walked the
        comment's braces and returned zero English keys for that module — silently dropping it
        from the parity, ownership AND drift checks.
      * `aicfg, // usage, — temporarily disabled` in the MODULES array still matched the
        identifier `usage`, so parking a module during a rebase passed the wiring gate while
        its keys were absent from `DICT` at runtime.

    Both are the exact failure this file exists to prevent, reintroduced by the guard against
    it. Blanking is done ONCE at the boundary rather than taught to each parser, because the
    lesson of those two escapes is that a second mechanism is what drifts.

    Length is preserved because `parse_ts_dicts` hands `parse_flat_object` an OFFSET into this
    string; newlines survive so anything reporting a line number still counts correctly.
    """
    out = list(src)
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        # A string literal is consumed whole, so `https://…` and an apostrophe inside a value
        # are never mistaken for a comment opener.
        if c in "'\"`":
            quote, i = c, i + 1
            while i < n:
                if src[i] == "\\":
                    i += 2
                    continue
                if src[i] == quote:
                    i += 1
                    break
                i += 1
            continue
        if c == "/" and i + 1 < n and src[i + 1] in "/*":
            line = src[i + 1] == "/"
            end = src.find("\n", i) if line else src.find("*/", i + 2)
            end = n if end < 0 else (end if line else end + 2)
            for k in range(i, end):
                if out[k] != "\n":
                    out[k] = " "
            i = end
            continue
        i += 1
    return "".join(out)


def parse_ts_dicts(src: str) -> dict[str, list[tuple[str, str]]]:
    """`const <lang>: Dict = { ... }` blocks, one per language, from the TypeScript source.

    Same character-walking approach as the JS parsers above and for the same reason: values
    contain apostrophes, braces and colons, so this is not JSON and a regex over it would be
    wrong in ways that only show up in Georgian.

    The needle is a LITERAL spelling, so a module that declares its blocks any other way parses
    as empty — `audit_migrated_modules` turns that silence into a failure.
    """
    src = blank_comments(src)
    out: dict[str, list[tuple[str, str]]] = {}
    for lang in ("en", "ka", "ru"):
        needle = f"const {lang}: Dict = {{"
        at = src.find(needle)
        if at < 0:
            continue
        out[lang] = parse_flat_object(src, at + len(needle) - 1)
    return out


def parse_flat_object(src: str, i: int) -> list[tuple[str, str]]:
    """(key, value) pairs of a FLAT `{ 'a': '...', "b": "..." }` literal opening at src[i].

    The JS dictionaries nest a language block inside an outer object; the TypeScript ones are
    one flat object per language, so they need their own walk rather than `parse_object`.
    """
    if src[i] != "{":
        raise SystemExit(f"check_i18n: expected '{{' at offset {i}")
    pairs: list[tuple[str, str]] = []
    depth = 0
    quote = ""          # the quote character of the string being read, or "" outside one
    buf = ""
    expecting_key = False
    key: str | None = None
    esc = False
    while i < len(src):
        c = src[i]
        if quote:
            if esc:
                # Keep the escaped character. `parse_object` above does the same, and the two
                # decodings have to agree exactly or the drift report calls `'You\'re…'` in a
                # module different from `"You're…"` in brand.js — same string, one backslash.
                buf += c
                esc = False
            elif c == "\\":
                esc = True
            elif c == quote:
                quote = ""
                if depth == 1:
                    if expecting_key:
                        key = buf
                        expecting_key = False
                    elif key is not None:
                        pairs.append((key, buf))
                        key = None
                buf = ""
            else:
                buf += c
        elif c == "/" and i + 1 < len(src) and src[i + 1] in "/*":
            # Same reason as in parse_object: comments carry apostrophes, and a module author
            # explaining a translation choice beside the string is behaviour to support, not to
            # forbid. Only reached outside a string, so `https://` in a value is untouched.
            end = src.find("\n", i) if src[i + 1] == "/" else src.find("*/", i)
            if end < 0:
                break
            i = end + (1 if src[i + 1] == "/" else 2)
            continue
        elif c in "'\"`":
            quote = c
            buf = ""
        elif c == "{":
            depth += 1
            if depth == 1:
                expecting_key = True
        elif c == "}":
            depth -= 1
            if depth == 0:
                if key is not None:      # value was not a plain string literal — keep the key
                    pairs.append((key, ""))
                return pairs
        elif c == "," and depth == 1:
            expecting_key = True
        i += 1
    return pairs


def main(argv: list[str]) -> int:
    # Every diagnostic here can quote a Georgian or Russian value, and the default Windows
    # console encoding (cp1252) raises on those — so the lint would die on the report instead of
    # printing it, which is the worst possible failure for a tool whose whole output is text in
    # three scripts.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass

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
    for problem in audit_migrated_modules(public_dir.parent / "next"):
        print(problem)
        failures += 1

    # Duplicates within a language. The rule differs by WHERE the two definitions live, and
    # the three cases are genuinely different problems — hence a three-way test rather than a
    # comparison against one filename, which would turn every sanctioned overlap into a
    # failure the moment a module was added or renamed:
    #
    #   * both in the legacy stack — a real fault. brand.js's DICT and every extendDict block
    #     are merged into one object at runtime, so the later registration silently wins and
    #     the first translation is dead code that drifts.
    #   * one legacy, one migrated — expected while the migration is in flight. The two
    #     stacks never load together (a page is served by one or the other), so neither can
    #     shadow the other; the shared chrome, the header above all, has to exist in both
    #     until the last .html page is gone. Reported, not failed — but it IS drift bait, so
    #     it stays visible and the list should shrink, never grow.
    #   * both migrated — a real fault, and the reason the dictionary is split by owner. Two
    #     page modules claiming one key means two people wrote that string, only one wording
    #     survives the merge in lib/i18n/index.ts, and which one depends on import order.
    #     Both paths are printed because the fix is a conversation between their owners.
    # Each stack is tracked separately, NOT as one "where did I last see this key" map. With a
    # single map the second migrated module would be compared against the legacy definition
    # that came first and pass as merely shared — the ownership clash this is here to catch
    # would be invisible precisely when a key exists in all three places.
    #
    # Because both copies are in hand at that moment, a shared key is also compared by VALUE and
    # a difference printed as DRIFT. Counting shared keys says how far the migration has to go;
    # it says nothing about whether the two copies still read the same, and "the Russian nav
    # label depends on which stack served the page" is a bug a reader of the count would never
    # suspect. DRIFT does not fail: the port is explicitly allowed to improve a string, so this
    # is a list to look at, not a gate — an intentional rewording stays listed until the legacy
    # copy is deleted with its page.
    MIGRATED = "next:"
    shared = 0
    drifted = 0
    for lang, entries in langs.items():
        seen: dict[bool, dict[str, tuple[str, str]]] = {False: {}, True: {}}
        for k, val, where in entries:
            new = where.startswith(MIGRATED)
            prev = seen[new].get(k)
            if prev is not None:
                if new:
                    print(f"DUPLICATE  {lang}: '{k}' is claimed by two migrated modules — {prev[1]} "
                          f"and {where} (one owner per key)")
                else:
                    print(f"DUPLICATE  {lang}: '{k}' defined in {prev[1]} and again in {where} "
                          "(the later one silently wins)")
                failures += 1
                continue
            other = seen[not new].get(k)
            if other is not None:
                shared += 1
                if other[0] != val:
                    drifted += 1
                    old, mig = (other[0], val) if new else (val, other[0])
                    print(f"DRIFT      {lang}: '{k}' differs between the stacks — "
                          f"legacy {old!r} vs migrated {mig!r}")
            seen[new][k] = (val, where)

    # cross-language parity
    sets = {lang: {k for k, _, _ in entries} for lang, entries in langs.items()}
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
    if shared:
        drift_note = f", {drifted} of them worded differently (DRIFT above)" if drifted else ""
        print(f"check_i18n: {shared} key definition(s) shared between the legacy and migrated "
              f"stacks{drift_note} (expected during the migration — keep them in step).")
    print(f"check_i18n: OK — {len(union)} keys × {len(sets)} languages in sync ({counts}; "
          f"{sources} source block(s)).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

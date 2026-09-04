"""Timestamped transcript segments — the coordinate system every analyser cites.

ElevenLabs Scribe returns one entry per word (`{text, start, end, type, speaker_id}`, `type`
∈ word | spacing | audio_event). Raw words are far too fine to highlight and far too many to
prompt with, so they are grouped into short speaker turns HERE, once, and everything
downstream — fact-check, scoring, semantic, summarise — is prompted with `render_timeline()`
and asked to cite the `#` index of a segment. Code turns indices back into seconds
(`spans_from_indices`). The model is never asked for seconds: it invents them.

Pure functions, no I/O. Inputs come from a third-party API, from a jsonb column some other
code wrote, or from a textarea, so every helper tolerates None entries, missing keys and
numbers that arrive as strings instead of raising halfway through an analysis.
"""
import math
import re

MAX_GAP_S = 1.2        # silence between two words that ends a segment
MAX_WORDS = 40         # a longer segment is no longer a meaningful highlight
MAX_SPAN_S = 25.0      # ... and neither is a longer one in seconds
MAX_TEXT_WORDS = 60    # a pasted line longer than this is split at sentence punctuation
DEFAULT_SPEAKER = "speaker_0"

# Scribe's non-speech entries. `spacing` is skipped too: its text is a single space that
# whitespace-normalisation recreates anyway, and at a speaker boundary it may carry EITHER
# neighbour's speaker_id, which would otherwise open a spurious one-space segment.
_SKIP_TYPES = frozenset({"audio_event", "spacing"})

# Sentence ends: Latin/Cyrillic `.!?…`, plus U+10FB (Georgian paragraph separator) and U+0589
# (the full stop Georgian texts borrow from Armenian). Split only when whitespace follows, so
# "3.5 mm" and "e.g.x" stay intact.
_SENTENCE_RE = re.compile(r"(?<=[.!?…჻։])\s+")

# A speaker label at the start of a pasted line: `Agent:`, `Customer 2:`, `ოპერატორი:`,
# `speaker_1:`. First character must be a letter (so `12:30 we met` is not speaker "12"),
# at most three short words, and the colon must be followed by whitespace or the line end
# (so `https://…` is not speaker "https").
_LABEL_RE = re.compile(
    r"^(?P<label>[^\W\d_][\w\-]{0,29}(?:[ \t][\w\-]{1,20}){0,2})[ \t]*:"
    r"(?:[ \t]+(?P<rest>.*)|[ \t]*)$"
)


# ---------------------------------------------------------------------------
# Coercion helpers — the "tolerant of garbage" half of the contract
# ---------------------------------------------------------------------------
def _num(value) -> float | None:
    """float, or None for anything that is not a finite number (bools included)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _round2(value: float | None) -> float | None:
    return None if value is None else round(value, 2)


def _clean(text) -> str:
    """Stripped, single-spaced text ('' for None). A newline inside a segment's text would
    break the one-line-per-segment timeline format the model is prompted with."""
    return " ".join(str(text).split()) if text is not None else ""


def _speaker(value) -> str:
    return _clean(value) or DEFAULT_SPEAKER


def _index(value) -> int | None:
    """int for 3, 3.0, "3"; None for bools, 3.5, "x", None."""
    if value is None or isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return int(f) if math.isfinite(f) and f == int(f) else None


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, (str, bytes, int, float)):
        return [value]
    try:
        return list(value)
    except TypeError:
        return []


# ---------------------------------------------------------------------------
# Audio: Scribe words → segments
# ---------------------------------------------------------------------------
def _first_start(group: list[dict]) -> float | None:
    return next((w["start"] for w in group if w["start"] is not None), None)


def _starts_new_segment(group: list[dict], word: dict) -> bool:
    """Whether `word` may not join `group` — a speaker change, a silence, or a group that is
    already as long as a highlight can usefully be. Time rules only apply when both sides
    actually carry a time."""
    last = group[-1]
    if word["speaker"] != last["speaker"] or len(group) >= MAX_WORDS:
        return True
    if last["end"] is not None and word["start"] is not None \
            and word["start"] - last["end"] > MAX_GAP_S:
        return True
    first = _first_start(group)
    return first is not None and word["end"] is not None and word["end"] - first > MAX_SPAN_S


def _segment(i: int, group: list[dict]) -> dict:
    starts = [w["start"] for w in group if w["start"] is not None]
    ends = [w["end"] for w in group if w["end"] is not None]
    # `ends + starts`: a word with a start but no end still ends no earlier than it started,
    # and the max over both can never sit before min(starts) — so end >= start holds even
    # when the input's times are inconsistent.
    return {
        "i": i,
        "speaker": group[0]["speaker"],
        "start": _round2(min(starts) if starts else None),
        "end": _round2(max(ends + starts) if (ends or starts) else None),
        "text": " ".join(w["text"] for w in group),
    }


def build_segments(words) -> list[dict]:
    """Group Scribe words into short speaker turns.

    Consecutive words of one speaker form an utterance; it is cut where the silence between
    two words exceeds 1.2 s, or where it would exceed 40 words or 25 s. `audio_event` (and
    `spacing`) entries are dropped. Missing/empty `words` → `[]`, so callers can fall back to
    `segments_from_text`.
    """
    groups: list[list[dict]] = []
    current: list[dict] = []
    for raw in _as_list(words):
        if not isinstance(raw, dict) or raw.get("type") in _SKIP_TYPES:
            continue
        text = _clean(raw.get("text"))
        if not text:
            continue
        word = {"text": text, "speaker": _speaker(raw.get("speaker_id")),
                "start": _num(raw.get("start")), "end": _num(raw.get("end"))}
        if current and _starts_new_segment(current, word):
            groups.append(current)
            current = []
        current.append(word)
    if current:
        groups.append(current)
    return [_segment(i, group) for i, group in enumerate(groups)]


# ---------------------------------------------------------------------------
# Text: pasted transcript → segments (no times)
# ---------------------------------------------------------------------------
def _split_label(line: str) -> tuple[str | None, str]:
    """(speaker, text) — speaker is None when the line carries no label."""
    m = _LABEL_RE.match(line)
    if not m:
        return None, line
    return m.group("label").strip().lower(), _clean(m.group("rest"))


def _sentence_words(text: str):
    """Sentences as word lists. A single sentence longer than the cap (an unpunctuated STT
    dump pasted as one line) is chopped at the cap: there is no punctuation to split at, and
    one 3 000-word segment would make every highlight cover the whole transcript."""
    for sentence in _SENTENCE_RE.split(text):
        words = sentence.split()
        for k in range(0, len(words), MAX_TEXT_WORDS):
            yield words[k:k + MAX_TEXT_WORDS]


def _split_long(text: str) -> list[str]:
    """One piece for a normal line; for a line over 60 words, sentences packed greedily into
    pieces of at most 60 words — so a piece is as close to a normal line as punctuation
    allows rather than one fragment per sentence."""
    text = _clean(text)
    if not text:
        return []
    if len(text.split()) <= MAX_TEXT_WORDS:
        return [text]
    pieces: list[str] = []
    current: list[str] = []
    for words in _sentence_words(text):
        if current and len(current) + len(words) > MAX_TEXT_WORDS:
            pieces.append(" ".join(current))
            current = []
        current.extend(words)
    if current:
        pieces.append(" ".join(current))
    return pieces


def segments_from_text(text) -> list[dict]:
    """One segment per non-empty line of a pasted transcript, `start`/`end` = None.

    A line that starts with a speaker label (`Agent:`, `ოპერატორი:`, `speaker_1:`) keeps it
    as `speaker`, lower-cased without the colon; any other line is `speaker_0`. A label on a
    line of its own (`Agent:` ↵ `Hello…`) names the line that follows — dropping it would
    lose the speaker, keeping it would make an empty segment.
    """
    out: list[dict] = []
    pending: str | None = None
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        speaker, body = _split_label(line)
        if speaker is not None and not body:
            pending = speaker
            continue
        if speaker is None:
            speaker = pending or DEFAULT_SPEAKER
        pending = None
        for piece in _split_long(body):
            out.append({"i": len(out), "speaker": speaker, "start": None, "end": None,
                        "text": piece})
    return out


# ---------------------------------------------------------------------------
# Rendering + mapping back
# ---------------------------------------------------------------------------
def fmt_time(seconds) -> str:
    """`MM:SS.s`. Tenths are enough for a human reading a transcript; minutes run past 59
    rather than rolling into hours, because nobody says "1:02:03" about a call. Rounds in
    tenths first so 59.97 s prints as 01:00.0, not 00:60.0."""
    tenths = max(0, round((_num(seconds) or 0.0) * 10))
    minutes, rest = divmod(tenths, 600)
    return f"{minutes:02d}:{rest / 10:04.1f}"


def render_timeline(segments) -> str:
    """The transcript the analysers are prompted with, one line per segment:
    `[#12 00:34.2-00:41.8 speaker_0] text`, or `[#12 speaker_0] text` without times.
    The `#` number is the segment's position in the list — the same coordinate
    `spans_from_indices` maps back — regardless of what the row's own `i` says."""
    lines = []
    for pos, seg in enumerate(_as_list(segments)):
        if not isinstance(seg, dict):
            continue
        start, end = _num(seg.get("start")), _num(seg.get("end"))
        speaker = _speaker(seg.get("speaker"))
        if start is not None and end is not None:
            head = f"#{pos} {fmt_time(start)}-{fmt_time(end)} {speaker}"
        else:
            head = f"#{pos} {speaker}"
        lines.append(f"[{head}] {_clean(seg.get('text'))}")
    return "\n".join(lines)


def _span(segs: list[dict], run: list[int], extra: dict) -> dict:
    """The first known start and the last known end of the run — equal to the first
    segment's start and the last segment's end whenever the times are complete."""
    starts = (_num(segs[i].get("start")) for i in run)
    ends = (_num(segs[i].get("end")) for i in reversed(run))
    return {
        "segments": list(run),
        "start": next((t for t in starts if t is not None), None),
        "end": next((t for t in ends if t is not None), None),
        **extra,
    }


def spans_from_indices(segments, indices, **extra) -> list[dict]:
    """Model-cited `#` indices → timeline spans.

    Sorts and dedupes, drops anything that is not a valid position, and merges runs of
    ADJACENT indices into one span (`{"segments": [3, 4], "start": seg3.start,
    "end": seg4.end, **extra}`). In text mode the span exists with `start`/`end` = None —
    the UI then highlights the transcript instead of the timeline.
    """
    segs = [s if isinstance(s, dict) else {} for s in _as_list(segments)]
    valid = sorted({i for i in map(_index, _as_list(indices))
                    if i is not None and 0 <= i < len(segs)})
    spans: list[dict] = []
    run: list[int] = []
    for i in valid:
        if run and i != run[-1] + 1:
            spans.append(_span(segs, run, extra))
            run = []
        run.append(i)
    if run:
        spans.append(_span(segs, run, extra))
    return spans


def duration_of(segments) -> float | None:
    """The latest time any segment reaches, or None when nothing carries a time."""
    times = [t for s in _as_list(segments) if isinstance(s, dict)
             for t in (_num(s.get("end")), _num(s.get("start"))) if t is not None]
    return _round2(max(times)) if times else None

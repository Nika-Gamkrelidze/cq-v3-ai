"""How a file is cut into chunks WITHOUT the AI restructure pass (kb_ingest.chunk_text).

The sample files offered under Import knowledge are built to this behaviour, so these tests pin
it: topics separated by a blank line stay whole, Windows line endings do not erase the blank
lines, and text with no blank lines (Word, PDF, Excel) is cut between lines, not mid-word.
"""
from app.services.kb_ingest import CHUNK_SIZE, chunk_text

TOPICS = [f"Topic {i}\n" + (("Sentence about the rule number %d. " % i) * 6).strip() for i in range(8)]


def test_blank_line_separated_topics_pack_whole_up_to_one_chunk():
    chunks = chunk_text("\n\n".join(TOPICS))
    assert len(chunks) > 1
    assert all(len(c) <= CHUNK_SIZE for c in chunks)
    # Every chunk starts at a topic heading: nothing is cut in the middle of one.
    assert all(c.startswith("Topic ") for c in chunks)
    assert "\n\n".join(chunks).replace("\n\n", "\n") == "\n".join(TOPICS)


def test_windows_line_endings_keep_their_paragraph_breaks():
    unix = chunk_text("\n\n".join(TOPICS))
    windows = chunk_text("\r\n\r\n".join(t.replace("\n", "\r\n") for t in TOPICS))
    assert windows == unix
    assert all("\r" not in c for c in windows)
    assert chunk_text("a\r\rb") == ["a\n\nb"]            # old-Mac breaks too


def test_text_without_blank_lines_is_cut_between_lines():
    lines = [f"Line {i}: " + ("word " * 30).strip() for i in range(40)]      # ~6,000 chars, no blank line
    chunks = chunk_text("\n".join(lines))
    assert len(chunks) > 3
    assert all(len(c) <= CHUNK_SIZE for c in chunks)
    assert all(c.startswith("Line ") for c in chunks)              # never starts mid-line
    assert all(c.rstrip().endswith("word") for c in chunks)        # never ends mid-line
    assert "\n".join(chunks) == "\n".join(lines).strip()


def test_a_single_line_longer_than_a_chunk_is_still_cut_with_overlap():
    chunks = chunk_text("x" * (CHUNK_SIZE * 2 + 500))
    assert len(chunks) == 3 and all(len(c) <= CHUNK_SIZE for c in chunks)


def test_empty_and_blank_text():
    assert chunk_text("") == [] and chunk_text("  \r\n\r\n ") == [] and chunk_text(None) == []

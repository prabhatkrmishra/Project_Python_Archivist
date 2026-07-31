"""Tests for snippet extraction (utils/text.py)."""

from __future__ import annotations

from archivist.utils.text import extract_snippet


class TestExtractSnippetPhrase:
    def test_marks_matching_line(self):
        content = "line one\nquarterly budget\nline three"
        snippet = extract_snippet(content, "quarterly budget", plain=True)
        lines = snippet.split("\n")
        assert any(line.startswith("> L2:") for line in lines)

    def test_prefers_exact_phrase_over_any_term(self):
        content = "apple pie\nbanana split\nfoo bar"
        snippet = extract_snippet(content, "foo bar", plain=True)
        assert "> L3: foo bar" in snippet

    def test_falls_back_to_any_term(self):
        content = "apple pie\nbanana split"
        snippet = extract_snippet(content, "banana pie", plain=True)
        # "pie" matches line 1 first; no exact "banana pie" phrase exists.
        assert "> L1: apple pie" in snippet

    def test_matching_is_case_insensitive(self):
        content = "Quarterly Budget Report"
        snippet = extract_snippet(content, "quarterly budget", plain=True)
        assert "> L1:" in snippet


class TestExtractSnippetContext:
    def test_shows_context_lines_around_match(self):
        content = "\n".join(f"line {i}" for i in range(10))
        snippet = extract_snippet(content, "line 5", context_lines=2, plain=True)
        assert "> L6: line 5" in snippet
        assert "L4:" in snippet  # 2 lines before
        assert "L8:" in snippet  # 2 lines after

    def test_respects_chunk_line_offset(self):
        content = "first\nsecond\nthird"
        snippet = extract_snippet(content, "second", line_offset=100, plain=True)
        assert "L102: second" in snippet
        assert "L101: first" in snippet

    def test_ellipsis_when_context_omitted_at_start(self):
        content = "\n".join(f"line {i}" for i in range(20))
        snippet = extract_snippet(content, "line 15", context_lines=1, plain=True)
        assert snippet.startswith("  ...")

    def test_ellipsis_when_context_omitted_at_end(self):
        content = "\n".join(f"line {i}" for i in range(20))
        snippet = extract_snippet(content, "line 1", context_lines=1, plain=True)
        assert snippet.endswith("  ...")


class TestExtractSnippetEdgeCases:
    def test_no_match_shows_first_lines(self):
        content = "hello world\nsecond line"
        snippet = extract_snippet(content, "nope", plain=True)
        lines = snippet.split("\n")
        assert len(lines) == 2
        assert not any(line.startswith(">") for line in lines)

    def test_empty_query_shows_first_lines(self):
        content = "one\ntwo\nthree"
        snippet = extract_snippet(content, "", plain=True)
        assert "L1: one" in snippet

    def test_empty_content_returns_empty(self):
        assert extract_snippet("", "anything", plain=True) == ""

    def test_plain_mode_has_no_rich_markup(self):
        content = "find me here"
        snippet = extract_snippet(content, "find", plain=True)
        assert "[" not in snippet

    def test_rich_mode_includes_markup(self):
        content = "find me here"
        snippet = extract_snippet(content, "find", plain=False)
        assert "[" in snippet

    def test_single_line_content(self):
        snippet = extract_snippet("just one line", "one", plain=True)
        assert "> L1: just one line" in snippet


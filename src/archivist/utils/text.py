"""Text utilities for search result display.

Provides snippet extraction with line-numbered context around matches.
"""

from __future__ import annotations

import re


def extract_snippet(content: str, query: str, context_lines: int = 3,
                     line_offset: int = 0) -> str:
    """Return a line-numbered snippet centered on the best query-term match.

    Displays the matching line with a ► marker and surrounding context lines.
    Uses "..." to indicate omitted content at boundaries.
    Prefers exact phrase matches over single-term matches.

    Args:
        content: Full document text (or chunk text).
        query: User's search query.
        context_lines: Lines of context before and after the match.
        line_offset: Starting line number of this chunk in the original file.

    Returns:
        Multi-line snippet with absolute line numbers, e.g.:
            ...
            L1542: sigaltstack(&ss, nullptr);
            L1543: struct sigaction sa{};
            > L1544: sa.sa_sigaction = segvHandler;
            L1545: sigemptyset(&sa.sa_mask);
            L1546: sa.sa_flags = SA_SIGINFO | SA_ONSTACK;
            ...
    """
    terms = [re.escape(t) for t in query.split() if t]
    if not terms:
        lines = content.splitlines()
        shown = lines[: context_lines * 2 + 1]
        return "\n".join(
            f"  [green]L{i + line_offset + 1}[/green][orange]:[/orange] {line}"
            for i, line in enumerate(shown)
        )

    # Build patterns: prefer exact phrase, fall back to any-term match
    phrase_pattern = re.compile(" ".join(terms), re.IGNORECASE)
    any_pattern = re.compile("|".join(terms), re.IGNORECASE)

    lines = content.splitlines()
    match_line_idx = None

    # First pass: try exact phrase match
    for i, line in enumerate(lines):
        if phrase_pattern.search(line):
            match_line_idx = i
            break

    # Second pass: fall back to any-term match
    if match_line_idx is None:
        for i, line in enumerate(lines):
            if any_pattern.search(line):
                match_line_idx = i
                break

    if match_line_idx is None:
        shown = lines[: context_lines * 2 + 1]
        return "\n".join(
            f"  [green]L{i + line_offset + 1}[/green][orange]:[/orange] {line}"
            for i, line in enumerate(shown)
        )

    start = max(0, match_line_idx - context_lines)
    end = min(len(lines), match_line_idx + context_lines + 1)

    parts = []
    for i in range(start, end):
        if i == match_line_idx:
            prefix = "[bright_cyan]> [/bright_cyan]"
        else:
            prefix = "  "
        parts.append(f"{prefix}[green]L{i + line_offset + 1}[/green][orange]:[/orange] {lines[i]}")

    if start > 0:
        parts.insert(0, "  ...")
    if end < len(lines):
        parts.append("  ...")

    return "\n".join(parts)

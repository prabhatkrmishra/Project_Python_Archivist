"""Text utilities for search result display.

Provides snippet extraction with line-numbered context around matches.
"""

from __future__ import annotations

import re


def extract_snippet(content: str, query: str, context_lines: int = 3) -> str:
    """Return a line-numbered snippet centered on the first query-term match.

    Displays the matching line with a ► marker and surrounding context lines.
    Uses "..." to indicate omitted content at boundaries.

    Args:
        content: Full document text.
        query: User's search query.
        context_lines: Lines of context before and after the match.

    Returns:
        Multi-line snippet with line numbers, e.g.:
            ...
            L142: sigaltstack(&ss, nullptr);
            L143: struct sigaction sa{};
            > L144: sa.sa_sigaction = segvHandler;
            L145: sigemptyset(&sa.sa_mask);
            L146: sa.sa_flags = SA_SIGINFO | SA_ONSTACK;
            ...
    """
    terms = [re.escape(t) for t in query.split() if t]
    if not terms:
        lines = content.splitlines()
        shown = lines[: context_lines * 2 + 1]
        return "\n".join(f"  L{i+1}: {l}" for i, l in enumerate(shown))

    pattern = re.compile("|".join(terms), re.IGNORECASE)

    lines = content.splitlines()
    match_line_idx = None
    for i, line in enumerate(lines):
        if pattern.search(line):
            match_line_idx = i
            break

    if match_line_idx is None:
        shown = lines[: context_lines * 2 + 1]
        return "\n".join(f"  L{i+1}: {l}" for i, l in enumerate(shown))

    start = max(0, match_line_idx - context_lines)
    end = min(len(lines), match_line_idx + context_lines + 1)

    parts = []
    for i in range(start, end):
        prefix = "> " if i == match_line_idx else "  "
        parts.append(f"{prefix}L{i+1}: {lines[i]}")

    if start > 0:
        parts.insert(0, "  ...")
    if end < len(lines):
        parts.append("  ...")

    return "\n".join(parts)

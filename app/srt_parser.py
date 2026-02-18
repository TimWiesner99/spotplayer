"""
SRT subtitle file parser.

Parses standard SRT format into a list of cue dicts suitable for
inserting into the `cues` database table.

SRT format:
    <index>
    HH:MM:SS,mmm --> HH:MM:SS,mmm
    Text line 1
    Text line 2

    <index>
    ...

Handles:
  - Windows (CRLF) and Unix (LF) line endings.
  - Both comma and period as the millisecond separator (non-standard but common).
  - Inline HTML tags stripped from text (e.g. <i>, <b>, <font>).
  - BOM at start of file (common in Windows-generated SRTs).
"""

import re
from typing import List, Dict, Any


# Matches one complete SRT block, allowing flexible whitespace between blocks.
_SRT_BLOCK_RE = re.compile(
    r"(\d+)\s*\n"                              # cue index
    r"(\d{2}:\d{2}:\d{2}[,\.]\d{3})"          # start timestamp
    r"\s*-->\s*"
    r"(\d{2}:\d{2}:\d{2}[,\.]\d{3})"          # end timestamp
    r"[^\n]*\n"                                # optional positioning info after timestamps
    r"([\s\S]*?)"                              # cue text (non-greedy)
    r"(?=\n\s*\n|\Z)",                         # end: blank line or end of string
    re.MULTILINE,
)

# Matches any HTML tag so we can strip it.
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _parse_timestamp(ts: str) -> float:
    """
    Convert an SRT timestamp string to seconds (float).

    Accepts both HH:MM:SS,mmm and HH:MM:SS.mmm.
    """
    ts = ts.replace(",", ".")
    h, m, rest = ts.split(":")
    s, ms = rest.split(".")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def _strip_html(text: str) -> str:
    """Remove HTML/XML tags from *text* and collapse extra whitespace."""
    clean = _HTML_TAG_RE.sub("", text)
    # Normalise internal whitespace while preserving single newlines between
    # multi-line cues (so callers can decide how to join them).
    clean = re.sub(r"[ \t]+", " ", clean).strip()
    return clean


def parse_srt(content: str) -> List[Dict[str, Any]]:
    """
    Parse SRT *content* (as a string) and return a list of cue dicts.

    Each dict has the keys:
        index_num  (int)   — original cue number from the SRT file
        start_time (float) — start in seconds
        end_time   (float) — end in seconds
        text       (str)   — cleaned cue text (HTML stripped, trimmed)

    Raises ValueError if no cues could be parsed (likely not a valid SRT).
    """
    # Strip UTF-8 BOM if present.
    content = content.lstrip("\ufeff")
    # Normalise line endings.
    content = content.replace("\r\n", "\n").replace("\r", "\n")
    # Ensure the string ends with a blank line so the regex terminates the last block.
    if not content.endswith("\n\n"):
        content += "\n\n"

    cues: List[Dict[str, Any]] = []

    for match in _SRT_BLOCK_RE.finditer(content):
        index_str, start_str, end_str, raw_text = match.groups()

        text = _strip_html(raw_text)
        # Replace internal newlines within a cue with a single space so the
        # transcript renders as flowing prose.
        text = " ".join(line.strip() for line in text.splitlines() if line.strip())

        if not text:
            # Skip cues with no displayable text (e.g. positioning-only blocks).
            continue

        cues.append(
            {
                "index_num": int(index_str),
                "start_time": _parse_timestamp(start_str),
                "end_time": _parse_timestamp(end_str),
                "text": text,
            }
        )

    if not cues:
        raise ValueError("No valid SRT cues found in the uploaded file.")

    # Sort by start time — some SRT files have cues out of order.
    cues.sort(key=lambda c: c["start_time"])
    return cues

"""Structure-aware chunking.

Goal from the brief: never separate a rule from its conditions. So we chunk on
Markdown structure rather than a fixed token window:
* each paragraph is a unit (the corpus writes one rule + its conditions per paragraph);
* a lead-in paragraph ending in ':' is merged with the list that follows it
  (e.g. "check these steps in order:" + steps 1-6);
* lists and tables are never split;
* each FAQ question/answer pair is its own chunk;
* very short neighbouring paragraphs in the same section are merged.
The section heading and document title are carried with every chunk.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_LIST_LINE = re.compile(r"^\s*(\d+\.|[-*+])\s+")
MIN_CHARS = 120
MAX_CHARS = 1400


@dataclass
class Chunk:
    index: int
    section: str
    text: str


def _block_kind(block: str) -> str:
    lines = block.splitlines()
    if all(l.strip().startswith("|") for l in lines if l.strip()):
        return "table"
    if all(_LIST_LINE.match(l) or l.startswith("   ") for l in lines if l.strip()):
        return "list"
    if block.startswith("**") and "?**" in block:
        return "faq"
    return "para"


def chunk_markdown(body: str) -> list[Chunk]:
    section = ""
    units: list[tuple[str, str, str]] = []  # (section, kind, text)
    for raw in re.split(r"\n\s*\n", body.strip()):
        block = raw.strip()
        if not block:
            continue
        if block.startswith("#"):
            heading, _, rest = block.partition("\n")
            section = heading.lstrip("#").strip()
            block = rest.strip()
            if not block:
                continue
        kind = _block_kind(block)
        if kind == "list" and units and units[-1][0] == section and units[-1][2].rstrip().endswith(":"):
            prev = units.pop()
            units.append((section, "rule_list", prev[2] + "\n" + block))
            continue
        units.append((section, kind, block))

    merged: list[tuple[str, str, str]] = []
    for sec, kind, text in units:
        # A paragraph right after a table usually qualifies its rows ("Standard includes X but not Y"):
        # keep it with the table so a plan's numbers and its conditions are retrieved together.
        if (merged and kind == "para" and merged[-1][1] == "table" and merged[-1][0] == sec
                and len(merged[-1][2]) + len(text) < MAX_CHARS):
            prev = merged.pop()
            merged.append((sec, "table_with_notes", prev[2] + "\n\n" + text))
            continue
        if (
            merged
            and kind == "para"
            and merged[-1][1] == "para"
            and merged[-1][0] == sec
            and (len(merged[-1][2]) < MIN_CHARS or len(text) < MIN_CHARS)
            and len(merged[-1][2]) + len(text) < MAX_CHARS
        ):
            prev = merged.pop()
            merged.append((sec, "para", prev[2] + "\n\n" + text))
        else:
            merged.append((sec, kind, text))
    return [Chunk(index=i, section=sec, text=text) for i, (sec, _, text) in enumerate(merged)]


def embedding_text(title: str, section: str, text: str) -> str:
    """Text used to EMBED a chunk (the stored chunk stays unchanged).

    Markdown tables embed poorly (little sentence structure), so each table row is rewritten as a
    sentence, e.g. "| Standard | 25 | 500 GB |" -> "Standard plan: users 25, storage 500 GB."
    """
    lines = [l.strip() for l in text.splitlines()]
    rows = [l for l in lines if l.startswith("|")]
    if len(rows) >= 3:
        header = [h.strip() for h in rows[0].strip("|").split("|")]
        sentences = [l for l in lines if l and not l.startswith("|")]
        for r in rows[2:]:
            cells = [c.strip() for c in r.strip("|").split("|")]
            pairs = ", ".join(f"{h.lower()} {v}" for h, v in zip(header[1:], cells[1:]))
            sentences.append(f"{cells[0]} plan: {pairs}.")
        text = "\n".join(sentences)
    return f"{title}\nSection: {section}\n{text}"

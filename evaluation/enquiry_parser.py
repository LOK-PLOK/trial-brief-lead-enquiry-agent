"""Parse assessor-supplied enquiry datasets from JSON, CSV, Markdown, or plain text.

Input-layer only — converts free-form uploads into the harness shape
`[{id, text}, ...]`. Does not call the Planner, Executor, or Verifier.
"""

from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path
from typing import Any

# Heading / ID lines that start a new enquiry block.
_ID_LINE_RE = re.compile(
    r"(?i)^(?:#{1,6}\s*)?"
    r"(?P<id>"
    r"[EA]\d{1,3}"
    r"|enq[-_]?\d{1,3}"
    r"|enquiry\s*\d{1,3}"
    r"|case\s*\d{1,3}"
    r"|sample\s*\d{1,3}"
    r"|#\s*\d{1,3}"
    r")"
    r"(?:\s*[:.\-–—)]\s*.*)?\s*$"
)

_HORIZONTAL_RULE_RE = re.compile(r"(?m)^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
_BLANK_BLOCK_RE = re.compile(r"\n\s*\n+")


def parse_enquiries_payload(
    *,
    text: str | None = None,
    structured: dict[str, Any] | list[Any] | None = None,
    filename: str | None = None,
    mode: str = "auto",
) -> tuple[list[dict[str, str]], str]:
    """Return `(enquiries, format_detected)`.

    `mode`:
      - ``auto`` — detect JSON / CSV / multi-enquiry text separators
      - ``single`` — treat the entire text as one enquiry
    """
    if structured is not None and (text is None or not str(text).strip()):
        from evaluation.datasets import normalize_custom_enquiries

        return normalize_custom_enquiries(structured), "json"

    if text is None:
        raise ValueError("Provide either raw text or a structured enquiries payload.")

    raw = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        raise ValueError("Enquiry text is empty.")

    if mode == "single":
        enquiry_id = _id_from_filename(filename) or "single"
        return [{"id": enquiry_id, "text": raw}], "single"

    fmt = _detect_format(raw, filename)
    if fmt == "json":
        return _parse_json(raw), "json"
    if fmt == "csv":
        return _parse_csv(raw), "csv"
    return _parse_plain_text(raw, filename=filename), fmt


def _detect_format(text: str, filename: str | None) -> str:
    name = (filename or "").lower()
    if name.endswith(".json"):
        return "json"
    if name.endswith(".csv"):
        return "csv"
    if name.endswith(".md") or name.endswith(".markdown"):
        return "markdown"
    if name.endswith(".txt"):
        return "text"

    stripped = text.lstrip()
    if stripped[:1] in "[{":
        try:
            json.loads(text)
            return "json"
        except json.JSONDecodeError:
            pass

    first_line = text.split("\n", 1)[0].lower()
    if "," in first_line and any(
        token in first_line for token in ("text", "enquiry", "id", "body")
    ):
        return "csv"

    return "markdown" if name.endswith(".md") else "text"


def _parse_json(text: str) -> list[dict[str, str]]:
    from evaluation.datasets import normalize_custom_enquiries

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON: {exc}") from exc
    return normalize_custom_enquiries(payload)


def _parse_csv(text: str) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise ValueError("CSV has no header row.")

    fields = {name.strip().lower(): name for name in reader.fieldnames if name}
    id_key = next(
        (fields[k] for k in ("id", "enquiry_id", "case_id", "name") if k in fields),
        None,
    )
    text_key = next(
        (
            fields[k]
            for k in ("text", "enquiry_text", "enquiry", "body", "content", "message")
            if k in fields
        ),
        None,
    )

    if text_key is None:
        if len(fields) == 1:
            text_key = next(iter(fields.values()))
        else:
            raise ValueError(
                "CSV must include a text/enquiry_text/body column (or a single text column)."
            )

    out: list[dict[str, str]] = []
    for i, row in enumerate(reader, start=1):
        body = (row.get(text_key) or "").strip()
        if not body:
            continue
        enquiry_id = (row.get(id_key) or "").strip() if id_key else ""
        if not enquiry_id:
            enquiry_id = f"row-{i:02d}"
        out.append({"id": enquiry_id, "text": body})

    if not out:
        raise ValueError("CSV contained no non-empty enquiry rows.")
    return out


def _parse_plain_text(text: str, *, filename: str | None) -> list[dict[str, str]]:
    if _HORIZONTAL_RULE_RE.search(text):
        chunks = [c.strip() for c in _HORIZONTAL_RULE_RE.split(text) if c.strip()]
        if len(chunks) > 1:
            return [_chunk_to_enquiry(chunk, index) for index, chunk in enumerate(chunks, start=1)]

    id_blocks = _split_on_id_headings(text)
    if id_blocks is not None:
        return id_blocks

    blank_chunks = [c.strip() for c in _BLANK_BLOCK_RE.split(text) if c.strip()]
    if len(blank_chunks) > 1:
        return [_chunk_to_enquiry(chunk, index) for index, chunk in enumerate(blank_chunks, start=1)]

    enquiry_id = _id_from_filename(filename) or "custom-01"
    return [{"id": enquiry_id, "text": text.strip()}]


def _split_on_id_headings(text: str) -> list[dict[str, str]] | None:
    lines = text.split("\n")
    heading_indexes: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        match = _ID_LINE_RE.match(line.strip())
        if match:
            heading_indexes.append((i, _normalize_id(match.group("id"))))

    if not heading_indexes:
        return None

    # One heading only counts when it opens the document (labelled single block).
    if len(heading_indexes) == 1 and heading_indexes[0][0] != 0:
        return None

    out: list[dict[str, str]] = []
    for idx, (line_no, enquiry_id) in enumerate(heading_indexes):
        start = line_no + 1
        end = heading_indexes[idx + 1][0] if idx + 1 < len(heading_indexes) else len(lines)
        body = "\n".join(lines[start:end]).strip()
        if not body:
            continue
        out.append({"id": enquiry_id, "text": body})

    return out or None


def _chunk_to_enquiry(chunk: str, index: int) -> dict[str, str]:
    lines = chunk.split("\n")
    first = lines[0].strip()
    match = _ID_LINE_RE.match(first)
    if match:
        enquiry_id = _normalize_id(match.group("id"))
        body = "\n".join(lines[1:]).strip() or chunk
        return {"id": enquiry_id, "text": body}
    return {"id": f"custom-{index:02d}", "text": chunk}


def _normalize_id(raw: str) -> str:
    cleaned = re.sub(r"\s+", "-", raw.strip())
    cleaned = cleaned.replace("_", "-")
    return cleaned


def _id_from_filename(filename: str | None) -> str | None:
    if not filename:
        return None
    stem = Path(filename).stem
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-")
    return cleaned[:64] or None

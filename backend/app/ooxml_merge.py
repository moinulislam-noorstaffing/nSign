"""Merge values into a .docx by splicing bytes — never by re-serialising XML.

This replaces an earlier ElementTree implementation that looked correct and was
measurably not. Two defects, both found by diffing the client's real 4-page
letter rather than by reading the code:

  1. RUN COLLAPSE. Joining a paragraph's `w:t` nodes and writing the whole
     result into the first one destroys every formatting boundary inside that
     paragraph. On the real letter: 99 text runs became 68, and bold runs fell
     from 35 to 18. The sentence "On behalf of **COMPANY NAME**… as a **POSTION
     TITLE**" came back with nothing bold, because the first run of the
     paragraph happened to be plain. The words the lawyers chose to emphasise
     silently stopped being emphasised.

  2. NAMESPACE LOSS. `ET.tostring` re-emits the root with only the prefixes it
     saw used — 35 declarations became 4. `mc:Ignorable` then names prefixes
     that no longer exist, which is malformed OOXML. LibreOffice tolerated it;
     Word is entitled not to.

So nothing is parsed and re-emitted here. The XML bytes are scanned for `w:t`
elements, the replacement text is spliced in place, and every other byte of the
part — root attributes, namespace declarations, run properties, rsids, section
properties — is carried through untouched.

Placeholders that straddle runs (`<w:t>NA</w:t><w:t>ME</w:t>`, which is how Word
routinely stores them) are handled by matching across the joined paragraph text
and then writing the replacement into the run where the match STARTED, so it
inherits that run's formatting — the bold on `COMPANY NAME` stays bold.
"""

from __future__ import annotations

import io
import re
from bisect import bisect_right
import zipfile
from typing import Any
from xml.sax.saxutils import escape, unescape

#: Parts that can hold visible text. Headers and footers are not optional: on a
#: real letter that is where the letterhead, logo and page furniture live.
TEXT_PARTS = re.compile(r"^word/(document|header\d*|footer\d*|footnotes|endnotes)\.xml$")

_P_OPEN = re.compile(rb"<w:p(?=[ >/])")
_P_CLOSE = re.compile(rb"</w:p>")
_P_SELF = re.compile(rb"<w:p[^>]*/>")
_WT = re.compile(rb"(<w:t(?:\s[^>]*)?>)(.*?)(</w:t>)", re.DOTALL)
_WT_OPEN_HAS_SPACE = re.compile(rb"xml:space\s*=")

MAX_PART_BYTES = 8 * 1024 * 1024
#: Across every text part, so many small parts cannot add up to the same attack.
MAX_TOTAL_BYTES = 24 * 1024 * 1024

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


class MergeError(RuntimeError):
    pass


class Span:
    __slots__ = ("open_tag", "text", "close_tag", "start", "end", "t_start", "t_end")

    def __init__(self, open_tag: bytes, text: str, close_tag: bytes,
                 start: int, end: int, t_start: int, t_end: int) -> None:
        self.open_tag = open_tag
        self.text = text
        self.close_tag = close_tag
        self.start = start          # byte offset of the whole <w:t>…</w:t>
        self.end = end
        self.t_start = t_start      # char offset within the joined paragraph text
        self.t_end = t_end


def paragraph_spans(xml: bytes) -> list[tuple[int, int]]:
    """Byte ranges of TOP-LEVEL w:p elements, counting depth.

    A non-greedy `<w:p .*?</w:p>` stops at the FIRST close tag, which is wrong
    the moment a paragraph contains another one — and it does whenever the
    letter has a text box (`w:txbxContent`), which is exactly how letterheads
    are built. The outer paragraph is then truncated mid-element and the splice
    corrupts the document.
    """
    events: list[tuple[int, int, int]] = []      # (pos, kind, end) kind: +1 open, -1 close
    for m in _P_OPEN.finditer(xml):
        events.append((m.start(), 1, m.end()))
    for m in _P_CLOSE.finditer(xml):
        events.append((m.start(), -1, m.end()))
    for m in _P_SELF.finditer(xml):
        # Self-closing <w:p/> opens and closes at once; drop the phantom open.
        events.append((m.start(), 0, m.end()))
    events.sort()

    spans: list[tuple[int, int]] = []
    depth = 0
    start = 0
    for pos, kind, end in events:
        if kind == 0:
            continue
        if kind == 1:
            if depth == 0:
                start = pos
            depth += 1
        else:
            if depth > 0:
                depth -= 1
                if depth == 0:
                    spans.append((start, end))
    return spans


def _spans(paragraph: bytes) -> list[Span]:
    out: list[Span] = []
    cursor = 0
    for m in _WT.finditer(paragraph):
        text = unescape(m.group(2).decode("utf-8"))
        out.append(Span(m.group(1), text, m.group(3), m.start(), m.end(),
                        cursor, cursor + len(text)))
        cursor += len(text)
    return out


def _pattern(values: dict[str, str], present: list[str] | None = None) -> re.Pattern[str] | None:
    """Alternation over every token, whether or not it has a value.

    Sorting the SUPPLIED keys longest-first is not enough. If the document says
    `COMPANY NAME` and only `NAME` was supplied, `COMPANY NAME` is absent from
    the alternation entirely, so `NAME` matches inside it and the letter reads
    "On behalf of Jane Doe" where it should say the company. Including the
    document's own tokens means the longer one always wins at that position and
    is then left alone because it has no value.

    Word boundaries stop a token matching inside an unrelated word.
    """
    keys = sorted(set(values) | set(present or []), key=len, reverse=True)
    if not keys:
        return None
    parts = []
    for k in keys:
        esc = re.escape(k)
        parts.append(r"\{\{\s*" + esc + r"\s*\}\}")
        # A token may start/end with a non-word char (CITY/STATE/ZIP CODE), so
        # the guards are lookarounds on word characters rather than \b.
        parts.append(r"(?<![A-Za-z0-9])" + esc + r"(?![A-Za-z0-9])")
    return re.compile("|".join(parts))


def _merge_paragraph(paragraph: bytes, values: dict[str, str],
                     pattern: re.Pattern[str]) -> tuple[bytes, list[str]]:
    spans = _spans(paragraph)
    if not spans:
        return paragraph, []
    joined = "".join(s.text for s in spans)
    if not joined.strip():
        return paragraph, []

    matches = list(pattern.finditer(joined))
    if not matches:
        return paragraph, []

    used: list[str] = []
    # Each output character is attributed to the run that owns its position, so
    # every formatting boundary outside the placeholders survives untouched.
    pieces: list[list[str]] = [[] for _ in spans]

    # Binary search, not a scan. The previous per-character linear walk made
    # merging quadratic in paragraph length, so one long paragraph in an 8 MB
    # part could hold the event loop for minutes.
    starts = [s.t_start for s in spans]

    def owner(pos: int) -> int:
        i = bisect_right(starts, pos) - 1
        return max(0, min(i, len(spans) - 1))

    cursor = 0
    for m in matches:
        # Text before the match keeps its original run attribution.
        for pos in range(cursor, m.start()):
            pieces[owner(pos)].append(joined[pos])
        name = m.group(0).strip("{} ").strip()
        value = values.get(name)
        if value is None:
            for pos in range(m.start(), m.end()):
                pieces[owner(pos)].append(joined[pos])
        else:
            used.append(name)
            # The replacement goes to the run the placeholder STARTED in — that
            # is the run carrying its bold/underline.
            pieces[owner(m.start())].append(value)
        cursor = m.end()
    for pos in range(cursor, len(joined)):
        pieces[owner(pos)].append(joined[pos])

    # Splice back, right to left so earlier offsets stay valid.
    out = bytearray(paragraph)
    for i in range(len(spans) - 1, -1, -1):
        span = spans[i]
        new_text = "".join(pieces[i])
        if new_text == span.text:
            continue
        open_tag = span.open_tag
        if new_text != new_text.strip() and not _WT_OPEN_HAS_SPACE.search(open_tag):
            open_tag = open_tag[:-1] + b' xml:space="preserve">'
        # XML 1.0 forbids C0 controls other than tab/newline/carriage return.
        # A value pasted from a spreadsheet routinely carries one, and it would
        # make document.xml unparseable — the failure surfaces as "the file is
        # corrupt" in Word, long after the merge reported success.
        clean = _CONTROL.sub("", new_text)
        replacement = open_tag + escape(clean).encode("utf-8") + span.close_tag
        out[span.start:span.end] = replacement
    return bytes(out), used


def _merge_part(xml_bytes: bytes, values: dict[str, str],
                present: list[str] | None = None) -> tuple[bytes, list[str]]:
    if b"<!DOCTYPE" in xml_bytes[:2048] or b"<!ENTITY" in xml_bytes[:8192]:
        raise MergeError("This .docx declares an XML DTD. Word never does. Refused.")
    pattern = _pattern(values, present)
    if pattern is None:
        return xml_bytes, []

    used: list[str] = []
    out = bytearray()
    cursor = 0
    for start, end in paragraph_spans(xml_bytes):
        out += xml_bytes[cursor:start]
        merged, part_used = _merge_paragraph(xml_bytes[start:end], values, pattern)
        out += merged
        used.extend(part_used)
        cursor = end
    out += xml_bytes[cursor:]
    return bytes(out), used


def merge(docx_bytes: bytes, values: dict[str, str]) -> dict[str, Any]:
    """Return the same .docx with merge fields filled in and nothing else changed."""
    try:
        source = zipfile.ZipFile(io.BytesIO(docx_bytes))
    except zipfile.BadZipFile as exc:
        raise MergeError("Not a readable .docx (bad ZIP).") from exc

    expected = scan_tokens(docx_bytes)
    used: list[str] = []
    touched: list[str] = []
    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as out:
        for item in source.infolist():
            data = source.read(item.filename)
            if TEXT_PARTS.match(item.filename):
                if item.file_size > MAX_PART_BYTES:
                    raise MergeError(f"{item.filename} is {item.file_size} bytes, over the cap.")
                data, part_used = _merge_part(data, values, expected)
                if part_used:
                    used.extend(part_used)
                    touched.append(item.filename)
            info = zipfile.ZipInfo(item.filename, date_time=item.date_time)
            info.compress_type = item.compress_type
            info.external_attr = item.external_attr
            out.writestr(info, data)

    merged = buffer.getvalue()
    return {
        "docx": merged,
        "used": sorted(set(used)),
        "parts_touched": touched,
        "unfilled": _unfilled(merged, expected),
    }


# --------------------------------------------------------------------------- #
#  Token discovery
# --------------------------------------------------------------------------- #

#: The client's agreed reference terms plus the names their eight letters use.
#: POSTION TITLE is their typo, kept verbatim — silently correcting a legal
#: document is not this tool's job.
KNOWN_TOKENS = {
    "NAME", "COMPANY NAME", "POSITION TITLE", "POSTION TITLE", "WORK ARRANGEMENT",
    "MANAGER", "MANAGER NAME", "HIRING MANAGER", "OFFICE LOCATION", "PAY SCHEDULE",
    "ADDRESS", "CITY/STATE/ZIP CODE", "PHONE NUMBER", "EMAIL", "TITLE",
    "START DATE", "HOURLY RATE", "ANNUAL SALARY", "CONTRACT RATE",
    "COMMISSION RATE", "DRAW", "FLAT SERVICE FEE", "BONUS",
}

#: Shouty text that is not a merge field. A real letterhead is full of it, and
#: treating "NY 10036" as a placeholder blocked a document that was complete.
NOISE = {
    "PRIVATE AND CONFIDENTIAL", "CONFIDENTIAL", "RE", "PS", "LLC", "INC", "LTD",
    "USA", "US", "EIN", "AND", "THE", "FOR", "NOT", "ALL", "ANY", "IRS", "PTO",
    "NEW YORK", "NY", "NJ", "PA", "TX", "CA", "FL", "AT-WILL", "EEO", "FLSA",
    "HR", "IT", "PDF", "N/A", "TBD", "AM", "PM", "EST", "EDT",
}

_CAPS = re.compile(r"\b[A-Z][A-Z/&' ]{2,40}[A-Z]\b")
_BRACED = re.compile(r"\{\{\s*([^}]+?)\s*\}\}")


def _paragraph_texts(docx_bytes: bytes) -> list[str]:
    """Every paragraph's joined text, with hard decompression caps.

    This is the choke point both scan_tokens and _unfilled reach, and it used to
    read parts with no size guard at all — a 400 KB archive declaring a 419 MB
    document.xml drove the 512 MB api container into MemoryError. The guards
    below are checked against the DECLARED size before any read.
    """
    archive = zipfile.ZipFile(io.BytesIO(docx_bytes))
    out: list[str] = []
    total = 0
    for name in archive.namelist():
        if not TEXT_PARTS.match(name):
            continue
        info = archive.getinfo(name)
        if info.file_size > MAX_PART_BYTES:
            raise MergeError(
                f"{name} expands to {info.file_size} bytes, over the "
                f"{MAX_PART_BYTES} cap. Refusing to read it."
            )
        total += info.file_size
        if total > MAX_TOTAL_BYTES:
            raise MergeError("The document's text parts exceed the total size cap.")
        data = archive.read(name)
        for start, end in paragraph_spans(data):
            m_group = data[start:end]
            joined = "".join(
                unescape(t.group(2).decode("utf-8")) for t in _WT.finditer(m_group)
            )
            if joined.strip():
                out.append(joined)
    return out


def scan_tokens(docx_bytes: bytes) -> list[str]:
    """Placeholders in the file, across body and headers/footers.

    Confident by construction: `{{...}}` and the known vocabulary are certain;
    anything else must be all-caps words with NO digits and at most four words,
    which is what separates a field name from a street address.
    """
    found: list[str] = []
    for para in _paragraph_texts(docx_bytes):
        for token in _BRACED.findall(para):
            if token.strip() not in found:
                found.append(token.strip())
        for raw in _CAPS.findall(para):
            token = " ".join(raw.split())
            if token in found or token in NOISE:
                continue
            if token in KNOWN_TOKENS:
                found.append(token)
            elif not any(c.isdigit() for c in token) and len(token.split()) <= 4:
                found.append(token)
    return found


def _unfilled(docx_bytes: bytes, expected: list[str]) -> list[str]:
    """Tokens the document started with that are still present after merging."""
    blob = "\n".join(_paragraph_texts(docx_bytes))
    remaining = [
        token for token in expected
        if re.search(r"\{\{\s*" + re.escape(token) + r"\s*\}\}", blob)
        or re.search(r"(?<![A-Za-z0-9])" + re.escape(token) + r"(?![A-Za-z0-9])", blob)
    ]
    return sorted(set(remaining))

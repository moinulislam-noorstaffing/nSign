"""Compose a NEW .docx from an approved one, without losing what makes it real.

Generating a document from nothing produces a document that looks generated: no
letterhead, no numbering definitions, no section setup, no embedded fonts, and
a page geometry that has nothing to do with the letters the client's lawyers
approved.

So this never starts from nothing. It takes a DONOR — one of the approved
letters — and keeps everything that is not prose:

    styles.xml          the paragraph and character styles
    numbering.xml       list definitions
    theme, fontTable    typography, including embedded font blobs
    header*/footer*     the letterhead, and the logo living inside it
    sectPr              page size, margins, header references

and replaces only the body paragraphs. The result is the same stationery with
different words on it, which is exactly what "make a similar template" means
when the stationery is the part that took a lawyer to approve.

Two operations are supported on top of that:

  * BODY REPLACEMENT from structured blocks — never from raw HTML, because HTML
    would have to be parsed and mapped anyway and a closed block vocabulary is
    something a model can be constrained to emit.
  * LOGO SUBSTITUTION — delegated to `letter_logo`, the one implementation shared
    with the offer generator. It fits the new image into the old one's space,
    renames the part when the format changes and keeps every relationship valid.
"""

from __future__ import annotations

import io
import re
import zipfile
from typing import Any
from xml.sax.saxutils import escape

from app import letter_logo

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

_BODY = re.compile(rb"(<w:body[^>]*>)(.*)(</w:body>)", re.DOTALL)
_SECTPR = re.compile(rb"<w:sectPr[ >].*?</w:sectPr>", re.DOTALL)
_HEADER_RELS = re.compile(r"^word/_rels/(header\d*|footer\d*)\.xml\.rels$")


class ComposeError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
#  Block vocabulary
#
#  Deliberately small. Every construct here maps to OOXML that Word, LibreOffice
#  and ONLYOFFICE all render identically; anything richer would render three
#  different ways and the fidelity claim would quietly stop being true.
# --------------------------------------------------------------------------- #

BLOCK_TYPES = ["heading", "paragraph", "bullet", "numbered", "terms_table", "spacer"]


def _runs(text: str, *, bare_placeholders: bool = True) -> str:
    """Split on **bold** markers and placeholders into OOXML runs.

    `bare_placeholders` strips the braces. The client's approved letters write
    merge points as plain capitals — `COMPANY NAME`, `POSITION TITLE` — with no
    delimiters at all. A generated template full of `{{NAME}}` reads as obviously
    machine-made sitting next to them, and the merge engine finds bare tokens
    either way (that is what KNOWN_TOKENS exists for). So the braces are an
    authoring convenience that gets removed on the way into the document.

    Placeholders stay bold regardless: the originals emphasise them, and a
    template that does not would still look like different stationery.
    """
    out: list[str] = []
    for part in re.split(r"(\*\*[^*]+\*\*|\{\{[^}]+\}\})", text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**"):
            body, bold = part[2:-2], True
        elif part.startswith("{{") and part.endswith("}}"):
            body = part[2:-2].strip() if bare_placeholders else part
            bold = True
        else:
            body, bold = part, False
        props = "<w:rPr><w:b/></w:rPr>" if bold else ""
        out.append(
            f'<w:r>{props}<w:t xml:space="preserve">{escape(body)}</w:t></w:r>'
        )
    return "".join(out) or '<w:r><w:t xml:space="preserve"></w:t></w:r>'


def _paragraph(text: str, *, style: str = "", align: str = "",
               numbered: bool = False, bullet: bool = False,
               bare: bool = True) -> str:
    props: list[str] = []
    if style:
        props.append(f'<w:pStyle w:val="{escape(style)}"/>')
    if bullet or numbered:
        # numId 1 is what Word writes for the default list in nearly every
        # document; if the donor lacks it the paragraph still renders, just
        # without the marker, which is a survivable degradation.
        props.append(f'<w:numPr><w:ilvl w:val="0"/><w:numId w:val="{1 if bullet else 2}"/></w:numPr>')
    if align:
        props.append(f'<w:jc w:val="{escape(align)}"/>')
    prefix = f"<w:pPr>{''.join(props)}</w:pPr>" if props else ""
    return f"<w:p>{prefix}{_runs(text, bare_placeholders=bare)}</w:p>"


def _terms_table(rows: list[dict[str, str]], *, bare: bool = True) -> str:
    """A two-column label/value table — how every one of these letters states terms."""
    cells: list[str] = []
    for row in rows:
        label = _runs(f"**{row.get('label', '')}**", bare_placeholders=bare)
        value = _runs(row.get("value", ""), bare_placeholders=bare)
        cells.append(
            "<w:tr>"
            f'<w:tc><w:tcPr><w:tcW w:w="3600" w:type="dxa"/></w:tcPr><w:p>{label}</w:p></w:tc>'
            f'<w:tc><w:tcPr><w:tcW w:w="5400" w:type="dxa"/></w:tcPr><w:p>{value}</w:p></w:tc>'
            "</w:tr>"
        )
    borders = (
        "<w:tblBorders>"
        + "".join(f'<w:{e} w:val="single" w:sz="4" w:color="BFBFBF"/>'
                  for e in ("top", "left", "bottom", "right", "insideH", "insideV"))
        + "</w:tblBorders>"
    )
    return (f'<w:tbl><w:tblPr><w:tblW w:w="0" w:type="auto"/>{borders}</w:tblPr>'
            + "".join(cells) + "</w:tbl>")


def blocks_to_xml(blocks: list[dict[str, Any]], *, bare: bool = True) -> str:
    out: list[str] = []
    for block in blocks:
        kind = block.get("type")
        text = block.get("text", "") or ""
        if kind == "heading":
            out.append(_paragraph(text, style="Heading1", bare=bare))
        elif kind == "paragraph":
            out.append(_paragraph(text, align=block.get("align", "") or "", bare=bare))
        elif kind == "bullet":
            out.append(_paragraph(text, bullet=True, bare=bare))
        elif kind == "numbered":
            out.append(_paragraph(text, numbered=True, bare=bare))
        elif kind == "terms_table":
            out.append(_terms_table(block.get("rows") or [], bare=bare))
        elif kind == "spacer":
            out.append("<w:p/>")
    return "".join(out)


# --------------------------------------------------------------------------- #
#  Composition
# --------------------------------------------------------------------------- #

def donor_logo_parts(docx_bytes: bytes) -> list[str]:
    """The letterhead image part, as a list (empty when the donor has none)."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(docx_bytes))
        slot = letter_logo.find_logo(archive.namelist(), archive.read)
    except (zipfile.BadZipFile, KeyError):
        return []
    return [slot.media] if slot else []


def uses_braced_placeholders(text: str) -> bool:
    """Does this document delimit its merge points, or write them bare?

    Detected from the donor rather than assumed, so a generated template always
    matches the convention of the letters it will sit beside.
    """
    return bool(re.search(r"\{\{\s*[A-Za-z0-9_ /&'-]+\s*\}\}", text))


def compose(donor_bytes: bytes, blocks: list[dict[str, Any]], *,
            logo: bytes | None = None, logo_name: str = "",
            bare_placeholders: bool = True) -> dict[str, Any]:
    """Build a new .docx: the donor's stationery, new body, optionally a new logo."""
    try:
        source = zipfile.ZipFile(io.BytesIO(donor_bytes))
    except zipfile.BadZipFile as exc:
        raise ComposeError("The donor is not a readable .docx.") from exc

    if "word/document.xml" not in source.namelist():
        raise ComposeError("The donor has no word/document.xml.")

    document = source.read("word/document.xml")
    match = _BODY.search(document)
    if not match:
        raise ComposeError("Could not locate <w:body> in the donor.")

    # sectPr is the LAST child of the body and carries page size, margins and
    # the header/footer references. Dropping it loses the letterhead entirely,
    # which is the one thing this whole approach exists to keep.
    old_body = match.group(2)
    sect = _SECTPR.search(old_body)
    tail = sect.group(0) if sect else b""

    new_body = blocks_to_xml(blocks, bare=bare_placeholders).encode("utf-8") + tail
    new_document = document[:match.start(2)] + new_body + document[match.end(2):]

    change: letter_logo.Change | None = None
    if logo:
        try:
            change = letter_logo.replace_logo(source.namelist(), source.read, logo, logo_name)
        except letter_logo.LogoProblem as exc:
            # A damaged or unsupported image is the caller's problem to hear about.
            # A donor with no letterhead logo is not: there is simply nothing to
            # replace, and the donor's own stationery stays, as it always did.
            if not exc.template:
                raise ComposeError(str(exc)) from exc
    renames = change.renames if change else {}

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as out:
        for item in source.infolist():
            if item.filename == "word/document.xml":
                data = new_document
            elif change and item.filename in change.media:
                data = change.media[item.filename]
            elif change and item.filename in change.edited:
                data = change.edited[item.filename]
            else:
                data = source.read(item.filename)
            info = zipfile.ZipInfo(renames.get(item.filename, item.filename), date_time=item.date_time)
            info.compress_type = item.compress_type
            info.external_attr = item.external_attr
            out.writestr(info, data)

    if renames:
        packaged = zipfile.ZipFile(io.BytesIO(buffer.getvalue()))
        broken = (letter_logo.dangling_relationships(packaged.namelist(), packaged.read)
                  - letter_logo.dangling_relationships(source.namelist(), source.read))
        if broken:
            raise ComposeError("Replacing the logo left a broken reference: " + "; ".join(sorted(broken)[:3]))

    logo_parts = donor_logo_parts(donor_bytes)
    return {
        "docx": buffer.getvalue(),
        "kept_parts": [n for n in source.namelist() if n != "word/document.xml"],
        "logo_replaced": change is not None,
        "logo_part": change.info["part"] if change else (logo_parts[0] if logo_parts else None),
        "logo": change.info if change else None,
        "logo_notes": change.notes if change else [],
        "blocks": len(blocks),
        "section_preserved": bool(tail),
        "placeholder_style": "braced" if not bare_placeholders else "bare",
    }

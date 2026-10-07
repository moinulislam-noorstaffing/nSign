"""Convert a .docx to HTML while KEEPING its formatting.

The first version of this importer flattened every paragraph to a bare `<p>` and
dropped everything else. On a lawyer-approved offer letter that is not a lossy
import, it is damage: bold terms stop being bold, the letterhead disappears, a
compensation table becomes a run of loose sentences, and the signature block
collapses. The client's eight files are the deliverable — the whole point of
importing them is that they already say and look like what the lawyers approved.

So this carries across, from the OOXML:

  paragraphs   alignment (w:jc), heading styles (w:pStyle), list membership (w:numPr)
  runs         bold, italic, underline, strike, superscript/subscript
  breaks       w:br and w:tab
  tables       w:tbl / w:tr / w:tc, including column spans
  images       w:drawing -> a:blip -> the media part, inlined as a data: URI
  links        w:hyperlink resolved through the relationship part

What it deliberately does NOT carry: fonts, colours, point sizes and spacing.
Those belong to the rendering shell so every letter looks like the same company
produced it — and letting a Word document dictate them is how you get eight
subtly different documents.

Standard library only: zipfile + ElementTree. The two guards from docx_util stay
— DTD rejection (billion laughs) and hidden-run quarantine.
"""

from __future__ import annotations

import base64
import html
import re
import zipfile
from io import BytesIO
from typing import Any
from xml.etree import ElementTree as ET

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

MAX_XML_BYTES = 8 * 1024 * 1024
MAX_IMAGE_BYTES = 2 * 1024 * 1024

_ALIGN = {"center": "center", "right": "right", "both": "justify", "left": "left"}
_IMG_MIME = {
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "gif": "image/gif", "bmp": "image/bmp", "svg": "image/svg+xml",
    "emf": "", "wmf": "",   # vector metafiles browsers cannot show
}


class DocxError(ValueError):
    pass


def _parse(xml_bytes: bytes) -> ET.Element:
    if b"<!DOCTYPE" in xml_bytes[:2048] or b"<!ENTITY" in xml_bytes[:8192]:
        raise DocxError(
            "This .docx declares an XML DTD or entities. Word never does that; the "
            "file is malformed or crafted to exhaust memory. Refused."
        )
    return ET.fromstring(xml_bytes)


def _rels(archive: zipfile.ZipFile) -> dict[str, str]:
    """rId -> target part, from word/_rels/document.xml.rels."""
    try:
        root = _parse(archive.read("word/_rels/document.xml.rels"))
    except (KeyError, DocxError):
        return {}
    out: dict[str, str] = {}
    for rel in root:
        rid, target = rel.get("Id"), rel.get("Target")
        if rid and target:
            out[rid] = target
    return out


def _hidden(run: ET.Element) -> bool:
    props = run.find(f"{W}rPr")
    if props is None:
        return False
    if props.find(f"{W}vanish") is not None:
        return True
    colour = props.find(f"{W}color")
    return colour is not None and (colour.get(f"{W}val") or "").lower() in {"ffffff", "white"}


def _wrap_run(text: str, props: ET.Element | None) -> str:
    out = html.escape(text)
    if props is None or not out:
        return out
    def on(tag: str) -> bool:
        el = props.find(f"{W}{tag}")
        return el is not None and (el.get(f"{W}val") or "true") not in ("false", "0")
    if on("b"):
        out = f"<strong>{out}</strong>"
    if on("i"):
        out = f"<em>{out}</em>"
    if props.find(f"{W}u") is not None:
        out = f"<u>{out}</u>"
    if on("strike"):
        out = f"<s>{out}</s>"
    va = props.find(f"{W}vertAlign")
    if va is not None:
        val = va.get(f"{W}val")
        if val == "superscript":
            out = f"<sup>{out}</sup>"
        elif val == "subscript":
            out = f"<sub>{out}</sub>"
    return out


def _image(node: ET.Element, rels: dict[str, str], archive: zipfile.ZipFile,
           warnings: list[str]) -> str:
    blip = node.find(f".//{A}blip")
    if blip is None:
        return ""
    rid = blip.get(f"{R}embed")
    target = rels.get(rid or "")
    if not target:
        return ""
    part = target if target.startswith("word/") else f"word/{target.lstrip('/')}"
    try:
        info = archive.getinfo(part)
    except KeyError:
        return ""
    ext = part.rsplit(".", 1)[-1].lower()
    mime = _IMG_MIME.get(ext, "")
    if not mime:
        warnings.append(f"An embedded {ext.upper()} image cannot be shown in a browser "
                        "and was skipped — re-save it as PNG in Word.")
        return ""
    if info.file_size > MAX_IMAGE_BYTES:
        warnings.append(f"Skipped a {info.file_size // 1024} KB image — over the 2 MB inline cap.")
        return ""
    data = base64.b64encode(archive.read(part)).decode()
    return f'<img src="data:{mime};base64,{data}" alt="">'


def _runs(container: ET.Element, rels: dict[str, str], archive: zipfile.ZipFile,
          hidden_out: list[str], warnings: list[str]) -> str:
    parts: list[str] = []
    for node in container:
        tag = node.tag
        if tag == f"{W}hyperlink":
            inner = _runs(node, rels, archive, hidden_out, warnings)
            href = rels.get(node.get(f"{R}id") or "", "")
            parts.append(f'<a href="{html.escape(href)}">{inner}</a>' if href.startswith(("http", "mailto")) else inner)
            continue
        if tag != f"{W}r":
            continue

        props = node.find(f"{W}rPr")
        text = "".join(t.text or "" for t in node.iter(f"{W}t"))
        if _hidden(node):
            if text.strip():
                hidden_out.append(text)
            continue

        chunk = _wrap_run(text, props) if text else ""
        for child in node:
            if child.tag == f"{W}br":
                chunk += "<br>"
            elif child.tag == f"{W}tab":
                chunk += "&nbsp;&nbsp;&nbsp;&nbsp;"
            elif child.tag == f"{W}drawing":
                chunk += _image(child, rels, archive, warnings)
        parts.append(chunk)
    return "".join(parts)


def _para(p: ET.Element, rels: dict[str, str], archive: zipfile.ZipFile,
          hidden_out: list[str], warnings: list[str]) -> tuple[str, bool, str]:
    """Returns (html, is_list_item, style) for one w:p."""
    props = p.find(f"{W}pPr")
    align = ""
    style = ""
    is_list = False
    if props is not None:
        jc = props.find(f"{W}jc")
        if jc is not None:
            align = _ALIGN.get((jc.get(f"{W}val") or "").lower(), "")
        ps = props.find(f"{W}pStyle")
        if ps is not None:
            style = (ps.get(f"{W}val") or "").lower()
        is_list = props.find(f"{W}numPr") is not None

    inner = _runs(p, rels, archive, hidden_out, warnings)
    if not inner.strip():
        return "", False, style

    if is_list:
        return f"<li>{inner}</li>", True, style

    if style.startswith("heading") or style == "title":
        level = "2" if style in ("heading1", "title") else "3"
        return f"<h{level}>{inner}</h{level}>", False, style

    attr = f' style="text-align:{align}"' if align and align != "left" else ""
    return f"<p{attr}>{inner}</p>", False, style


def _table(tbl: ET.Element, rels: dict[str, str], archive: zipfile.ZipFile,
           hidden_out: list[str], warnings: list[str]) -> str:
    rows: list[str] = []
    for tr in tbl.findall(f"{W}tr"):
        cells: list[str] = []
        for tc in tr.findall(f"{W}tc"):
            span = ""
            props = tc.find(f"{W}tcPr")
            if props is not None:
                merge = props.find(f"{W}gridSpan")
                if merge is not None and merge.get(f"{W}val"):
                    span = f' colspan="{html.escape(merge.get(f"{W}val"))}"'
            body = "".join(
                _para(p, rels, archive, hidden_out, warnings)[0] for p in tc.findall(f"{W}p")
            )
            cells.append(f"<td{span}>{body or '&nbsp;'}</td>")
        if cells:
            rows.append("<tr>" + "".join(cells) + "</tr>")
    return "<table>" + "".join(rows) + "</table>" if rows else ""


def to_html(file_bytes: bytes) -> dict[str, Any]:
    """Convert a .docx into HTML that preserves its structure."""
    try:
        archive = zipfile.ZipFile(BytesIO(file_bytes))
    except zipfile.BadZipFile as exc:
        raise DocxError("Not a readable .docx (bad ZIP).") from exc

    if "word/document.xml" not in archive.namelist():
        raise DocxError("No word/document.xml inside — is this really a Word file?")

    info = archive.getinfo("word/document.xml")
    if info.file_size > MAX_XML_BYTES:
        raise DocxError(f"word/document.xml expands to {info.file_size} bytes, over the cap.")

    root = _parse(archive.read("word/document.xml"))
    rels = _rels(archive)
    hidden: list[str] = []
    warnings: list[str] = []

    body = root.find(f"{W}body")
    if body is None:
        raise DocxError("The document has no body.")

    out: list[str] = []
    open_list = False

    for node in body:
        if node.tag == f"{W}p":
            frag, is_list, _ = _para(node, rels, archive, hidden, warnings)
            if not frag:
                continue
            if is_list and not open_list:
                out.append("<ul>")
                open_list = True
            elif not is_list and open_list:
                out.append("</ul>")
                open_list = False
            out.append(frag)
        elif node.tag == f"{W}tbl":
            if open_list:
                out.append("</ul>")
                open_list = False
            out.append(_table(node, rels, archive, hidden, warnings))
    if open_list:
        out.append("</ul>")

    html_out = "\n".join(x for x in out if x)

    if hidden:
        warnings.insert(0, f"{len(hidden)} hidden or white-on-white run(s) were removed. "
                           "Invisible in Word — read them before trusting this file.")

    # Report what was carried, so "did the import damage it?" is answerable.
    kept = {
        "paragraphs": html_out.count("<p"),
        "headings": len(re.findall(r"<h[23]", html_out)),
        "bold_runs": html_out.count("<strong>"),
        "italic_runs": html_out.count("<em>"),
        "underlined_runs": html_out.count("<u>"),
        "list_items": html_out.count("<li>"),
        "tables": html_out.count("<table>"),
        "images": html_out.count("<img"),
        "links": html_out.count("<a href"),
    }

    return {"html": html_out, "hidden_text": hidden, "warnings": warnings, "kept": kept}

"""Swap the letterhead logo in a .docx and change nothing else.

A logo in a .docx is four things that have to agree, which is why replacing the
image bytes alone is not enough:

  * the media part            word/media/image1.png
  * a relationship to it      word/_rels/header1.xml.rels  (Id -> Target)
  * a content type for it     [Content_Types].xml          (by file extension)
  * a drawing that sizes it   <wp:extent cx= cy=> in the header, in EMU

So a JPEG dropped over image1.png would be a JPEG under a .png name, and a logo
with different proportions would be stretched into the old one's box. Here the
part is renamed when the format changes, every relationship that points at it is
updated, the content type is added if it is missing, and the new image is FITTED
inside the old box, keeping its own proportions.

This is the ONLY place a letterhead logo is swapped. Both the AI studio
(`docx_compose.compose`) and the offer generator (`offer_letter.generate`) call
`replace_logo`, so a fix or a rule added here applies to both.

Pure standard library; nothing here parses and re-emits XML.
"""

from __future__ import annotations

import posixpath
import re
import struct
from dataclasses import dataclass, field
from typing import Callable
from xml.sax.saxutils import escape

SUPPORTED = {"image/png": "png", "image/jpeg": "jpeg", "image/gif": "gif"}
MAX_SIDE_PX = 10_000
MAX_PIXELS = 25_000_000
MAX_BYTES = 4 * 1024 * 1024
LOW_RES_PX = 300

_HEADER = re.compile(r"^word/(?:header|footer)\d*\.xml$")
_RELS = re.compile(r"^(?:.*/)?_rels/[^/]*\.rels$")
_BLIP = re.compile(rb'<a:blip\b[^>]*?\br:embed="([^"]+)"')
_TAG_EXTENT = re.compile(rb"<wp:extent\b[^>]*>")
_TAG_AEXT = re.compile(rb"<a:ext\b[^>]*\bcx=[^>]*>")
_DESCR = re.compile(rb'\bdescr="[^"]*"')
_REL_TAG = re.compile(rb"<Relationship\b[^>]*>")
_DEFAULT_TAG = re.compile(rb"<Default\b[^>]*>", re.I)
_OVERRIDE_TAG = re.compile(rb"<Override\b[^>]*>", re.I)


class LogoProblem(Exception):
    """`template` is True when the fault is the template's, not the image's."""

    def __init__(self, message: str, template: bool = False) -> None:
        super().__init__(message)
        self.template = template


# --------------------------------------------------------------------------- #
#  The image
# --------------------------------------------------------------------------- #

def _jpeg_size(d: bytes) -> tuple[int, int]:
    i = 2
    sof = {*range(0xC0, 0xC4), *range(0xC5, 0xC8), *range(0xC9, 0xCC), *range(0xCD, 0xD0)}
    while i + 4 <= len(d):
        if d[i] != 0xFF:
            i += 1
            continue
        while i < len(d) and d[i] == 0xFF:
            i += 1
        if i >= len(d):
            break
        marker = d[i]
        i += 1
        if marker in (0x01, 0xD8) or 0xD0 <= marker <= 0xD7:
            continue
        if marker == 0xD9:
            break
        if i + 2 > len(d):
            break
        length = struct.unpack(">H", d[i:i + 2])[0]
        if marker in sof:
            if i + 7 > len(d):
                break
            height, width = struct.unpack(">HH", d[i + 3:i + 7])
            return width, height
        i += max(length, 2)
    raise LogoProblem("The JPEG is damaged: no image header was found.")


def image_info(data: bytes) -> tuple[str, int, int]:
    """(mime, width_px, height_px) for a PNG, JPEG or GIF; LogoProblem otherwise.

    Sniffed from the bytes, never from a name or a declared type, and a file whose
    ending is missing is refused as truncated.
    """
    if not data:
        raise LogoProblem("The logo file is empty.")
    if len(data) > MAX_BYTES:
        raise LogoProblem("The logo is over 4 MB. It goes into every letter; shrink it.")
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        raise LogoProblem("WebP logos are not supported inside a Word document. Use PNG, JPEG or GIF.")
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        if len(data) < 33 or data[12:16] != b"IHDR":
            raise LogoProblem("The PNG is damaged: no image header.")
        if data[-8:-4] != b"IEND":
            raise LogoProblem("The PNG is truncated.")
        mime = "image/png"
        width, height = struct.unpack(">II", data[16:24])
    elif data[:6] in (b"GIF87a", b"GIF89a"):
        if len(data) < 14 or data[-1:] != b"\x3b":
            raise LogoProblem("The GIF is truncated.")
        mime = "image/gif"
        width, height = struct.unpack("<HH", data[6:10])
    elif data[:2] == b"\xff\xd8":
        if data.rstrip(b"\x00")[-2:] != b"\xff\xd9":
            raise LogoProblem("The JPEG is truncated.")
        mime = "image/jpeg"
        width, height = _jpeg_size(data)
    else:
        raise LogoProblem("That is not a PNG, JPEG or GIF image.")
    if not (1 <= width <= MAX_SIDE_PX and 1 <= height <= MAX_SIDE_PX) or width * height > MAX_PIXELS:
        raise LogoProblem(f"The image is {width}x{height}px; the limit is {MAX_SIDE_PX}px a side.")
    return mime, width, height


def fit(width_px: int, height_px: int, box_cx: int, box_cy: int) -> tuple[int, int]:
    """Largest size with the image's own proportions that fits the box, in EMU."""
    scale = min(box_cx / width_px, box_cy / height_px)
    return max(1, round(width_px * scale)), max(1, round(height_px * scale))


# --------------------------------------------------------------------------- #
#  The slot in the template
# --------------------------------------------------------------------------- #

@dataclass
class Slot:
    header: str            # word/header1.xml
    rid: str               # rId1
    media: str             # word/media/image1.png
    box: tuple[int, int]   # current drawing size, EMU


def _attr(tag: bytes, name: str) -> str | None:
    m = re.search(rb'\b' + name.encode() + rb'="([^"]*)"', tag)
    return m.group(1).decode("utf-8", "replace") if m else None


def _resolve(rels_name: str, target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    base = posixpath.dirname(posixpath.dirname(rels_name))
    return posixpath.normpath(posixpath.join(base, target))


def _drawing_range(xml: bytes, at: int) -> tuple[int, int] | None:
    start = xml.rfind(b"<w:drawing", 0, at)
    end = xml.find(b"</w:drawing>", at)
    return (start, end + len(b"</w:drawing>")) if start >= 0 and end >= 0 else None


def find_logo(names: list[str], read: Callable[[str], bytes]) -> Slot | None:
    """The first picture in a header (then a footer): that is the letterhead."""
    for header in sorted((n for n in names if _HEADER.match(n)),
                         key=lambda n: ("/header" not in n, n)):
        xml = read(header)
        m = _BLIP.search(xml)
        rels = f"word/_rels/{posixpath.basename(header)}.rels"
        if not m or rels not in names:
            continue
        rid = m.group(1).decode()
        drawing = _drawing_range(xml, m.start())
        extent = _TAG_EXTENT.search(xml[drawing[0]:drawing[1]]) if drawing else None
        if not extent:
            continue
        for tag in _REL_TAG.findall(read(rels)):
            if _attr(tag, "Id") == rid and _attr(tag, "TargetMode") != "External":
                media = _resolve(rels, _attr(tag, "Target") or "")
                cx, cy = _attr(extent.group(0), "cx"), _attr(extent.group(0), "cy")
                if media in names and cx and cy and cx.isdigit() and cy.isdigit():
                    return Slot(header, rid, media, (int(cx), int(cy)))
    return None


# --------------------------------------------------------------------------- #
#  The swap
# --------------------------------------------------------------------------- #

@dataclass
class Change:
    edited: dict[str, bytes]                 # XML parts to write (header, rels, content types)
    renames: dict[str, str]                  # old part name -> new part name
    media: dict[str, bytes]                  # old part name -> the new image bytes
    notes: list[str] = field(default_factory=list)
    info: dict = field(default_factory=dict)


def _same_format(a: str, b: str) -> bool:
    jpeg = {"jpg", "jpeg"}
    return a == b or (a in jpeg and b in jpeg)


def _set_cx_cy(tag: bytes, cx: int, cy: int) -> bytes:
    tag = re.sub(rb'\bcx="\d+"', b'cx="%d"' % cx, tag, count=1)
    return re.sub(rb'\bcy="\d+"', b'cy="%d"' % cy, tag, count=1)


def replace_logo(names: list[str], read: Callable[[str], bytes], logo: bytes,
                 alt_text: str = "") -> Change:
    mime, width, height = image_info(logo)
    slot = find_logo(names, read)
    if slot is None:
        raise LogoProblem("This template has no logo in its header to replace.", template=True)

    cx, cy = fit(width, height, *slot.box)
    notes: list[str] = []
    if width < LOW_RES_PX:
        notes.append(f"The logo is only {width}px wide and may print blurry. Use a larger file if you have one.")

    new_ext = SUPPORTED[mime]
    old_ext = posixpath.splitext(slot.media)[1].lstrip(".").lower()
    new_media = slot.media
    if not _same_format(old_ext, new_ext):
        new_media = f"{posixpath.splitext(slot.media)[0]}.{new_ext}"
        while new_media in names:
            new_media = f"{posixpath.splitext(new_media)[0]}-logo.{new_ext}"

    edited: dict[str, bytes] = {}

    # The header: resize the picture to fit, and describe it correctly.
    xml = read(slot.header)
    m = _BLIP.search(xml)
    drawing = _drawing_range(xml, m.start()) if m else None
    if not drawing:
        raise LogoProblem("The template's logo drawing could not be located.", template=True)
    body = xml[drawing[0]:drawing[1]]
    body = _TAG_EXTENT.sub(lambda t: _set_cx_cy(t.group(0), cx, cy), body)
    body = _TAG_AEXT.sub(lambda t: _set_cx_cy(t.group(0), cx, cy), body)
    alt = re.sub(r"[\x00-\x1f]", " ", alt_text).strip()[:200]
    if alt:
        safe = escape(alt, {'"': "&quot;"}).encode("utf-8")
        body = _DESCR.sub(lambda _t: b'descr="' + safe + b'"', body)
    edited[slot.header] = xml[:drawing[0]] + body + xml[drawing[1]:]

    renames: dict[str, str] = {}
    if new_media != slot.media:
        renames[slot.media] = new_media
        old_base, new_base = posixpath.basename(slot.media), posixpath.basename(new_media)
        # Every relationship that points at the old part, in any rels file.
        for name in names:
            if not _RELS.match(name):
                continue
            rels = read(name)

            def retarget(t: re.Match[bytes], _name: str = name) -> bytes:
                tag = t.group(0)
                target = _attr(tag, "Target")
                if target and _attr(tag, "TargetMode") != "External" and _resolve(_name, target) == slot.media:
                    fixed = target[:-len(old_base)] + new_base
                    return tag.replace(f'Target="{target}"'.encode(), f'Target="{fixed}"'.encode(), 1)
                return tag
            updated = _REL_TAG.sub(retarget, rels)
            if updated != rels:
                edited[name] = updated

        # The content type, by extension, unless the package already says it.
        types = read("[Content_Types].xml")
        has_default = any((_attr(t, "Extension") or "").lower() == new_ext
                          for t in _DEFAULT_TAG.findall(types))
        has_override = any(_attr(t, "PartName") == "/" + new_media for t in _OVERRIDE_TAG.findall(types))
        if not (has_default or has_override):
            opening = re.search(rb"<Types\b[^>]*>", types)
            if not opening:
                raise LogoProblem("[Content_Types].xml has no <Types> element.", template=True)
            add = f'<Default ContentType="{mime}" Extension="{new_ext}"/>'.encode()
            edited["[Content_Types].xml"] = types[:opening.end()] + add + types[opening.end():]

    return Change(
        edited=edited, renames=renames, media={slot.media: logo}, notes=notes,
        info={"part": new_media, "replaced": slot.media, "format": mime,
              "size_px": [width, height], "box_emu": list(slot.box), "fitted_emu": [cx, cy]})


def dangling_relationships(names: list[str], read: Callable[[str], bytes]) -> set[str]:
    """Relationships whose target is not in the package. A renamed part that one
    relationship still points at the old name of makes Word call the file corrupt."""
    broken: set[str] = set()
    for name in names:
        if not _RELS.match(name):
            continue
        for tag in _REL_TAG.findall(read(name)):
            target = _attr(tag, "Target")
            if target and _attr(tag, "TargetMode") != "External" and _resolve(name, target) not in names:
                broken.add(f"{name} -> {target}")
    return broken


def content_type_covers(types: bytes, part: str) -> bool:
    ext = posixpath.splitext(part)[1].lstrip(".").lower()
    return (any((_attr(t, "Extension") or "").lower() == ext for t in _DEFAULT_TAG.findall(types))
            or any(_attr(t, "PartName") == "/" + part for t in _OVERRIDE_TAG.findall(types)))

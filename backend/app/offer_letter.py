"""Produce an offer letter from the approved master .docx without reformatting it.

The contract is the same as `ooxml_merge`: the master is the artifact, and every
byte this module does not have to change is copied through untouched. Nothing
here parses and re-emits XML. Text is spliced into the `w:t` of the run where a
placeholder starts, so a bold "Company Name" stays bold and the sentence around
it keeps its own formatting.

A letter is made of three kinds of section:

  * FIELD slots      plain-text labels ("Employee Name", "Company Name", the two
                     literal dates). Replaced by value, never by a model.
  * COMPENSATION     the wage rate, the commission table and the overtime rate.
                     The only part that varies in structure: the commission
                     table gains or loses rows, and either component can be
                     absent, in which case its paragraph (and table) is removed.
  * STATIC           everything else. Never edited, and checked afterwards.

This module never calls a model. A model may SUGGEST a compensation structure
(see `ai.parse_compensation`), a person confirms it, and what arrives here is
already structured data that is validated again before anything is written.
"""

from __future__ import annotations

import io
import math
import re
import xml.etree.ElementTree as ET
import zipfile
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date
from typing import Any
from xml.sax.saxutils import escape

from app import letter_logo
from app import ooxml_merge as om


# --------------------------------------------------------------------------- #
#  Errors
# --------------------------------------------------------------------------- #

class OfferError(RuntimeError):
    """Base class. `status` is the HTTP status the API should answer with and
    `issues` is the structured list the UI can show next to the right input."""

    status = 422

    def __init__(self, message: str, issues: list[dict[str, str]] | None = None) -> None:
        super().__init__(message)
        self.issues = issues or []


class ValidationFailed(OfferError):
    """The supplied values are not acceptable. Nothing was generated."""


class TemplateShapeError(OfferError):
    """The template does not contain what this generator needs to edit."""


class IntegrityFailure(OfferError):
    """A generated file failed its own safety checks. It is never returned."""

    status = 500


def _issue(severity: str, field: str, message: str) -> dict[str, str]:
    return {"severity": severity, "field": field, "message": message}


def _raise_if_errors(issues: list[dict[str, str]], headline: str,
                     exc: type[OfferError] = ValidationFailed) -> None:
    errors = [i for i in issues if i["severity"] == "error"]
    if errors:
        detail = "; ".join(i["message"] for i in errors[:4])
        more = f" (+{len(errors) - 4} more)" if len(errors) > 4 else ""
        raise exc(f"{headline}: {detail}{more}", issues)


# --------------------------------------------------------------------------- #
#  Form values
# --------------------------------------------------------------------------- #

WORK_ARRANGEMENTS = ("in_office", "hybrid", "remote")
EMPLOYMENT_TYPES = ("W2", "1099")
ONSHORE_OFFSHORE = ("onshore", "offshore")

#: Field -> (label shown to the user, max length).
_TEXT_FIELDS: dict[str, tuple[str, int]] = {
    "employee_name": ("Employee name", 120),
    "address1": ("Address 1", 120),
    "address2": ("Address 2", 120),
    "city": ("City", 80),
    "job_title": ("Job title", 120),
    "office_location": ("Office location", 120),
    "report_to": ("Report to / Manager", 120),
}

_REQUIRED = ("employee_name", "address1", "city", "state", "zip", "email",
             "phone", "job_title", "report_to", "start_date", "document_date")

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")
_ZIP = re.compile(r"^\d{5}(-\d{4})?$")
_STATE = re.compile(r"^[A-Za-z]{2}$")

_MONTHS = ("January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December")


def _clean_text(value: Any) -> str | None:
    """One line of printable text, or None when the value is not text at all."""
    if value is None:
        return ""
    if not isinstance(value, str):
        return None
    value = om._CONTROL.sub("", value)
    return re.sub(r"\s+", " ", value).strip()


def format_date(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{_MONTHS[d.month - 1]} {d.day:02d}, {d.year}"


def clean_fields(raw: Any, company_name: str) -> tuple[dict[str, str], list[dict[str, str]]]:
    """Validate and normalise the form. Collects EVERY problem, not just the first,
    so the person fixes the form once instead of once per submit.

    Returns (clean values, warnings). Raises ValidationFailed on any error.
    """
    issues: list[dict[str, str]] = []
    if not isinstance(raw, dict):
        raise ValidationFailed("The form values are missing.",
                               [_issue("error", "fields", "The form values are missing.")])

    out: dict[str, str] = {}
    for key, (label, limit) in _TEXT_FIELDS.items():
        text = _clean_text(raw.get(key))
        if text is None:
            issues.append(_issue("error", key, f"{label} must be text."))
            text = ""
        if len(text) > limit:
            issues.append(_issue("error", key, f"{label} is longer than {limit} characters."))
        out[key] = text

    state = _clean_text(raw.get("state")) or ""
    out["state"] = state.upper()
    if state and not _STATE.match(state):
        issues.append(_issue("error", "state", "State must be a two-letter code, e.g. RI."))

    zip_code = _clean_text(raw.get("zip")) or ""
    out["zip"] = zip_code
    if zip_code and not _ZIP.match(zip_code):
        issues.append(_issue("error", "zip", "Zip code must be 5 digits (or 5+4)."))

    email = _clean_text(raw.get("email")) or ""
    out["email"] = email
    if email and (len(email) > 254 or not _EMAIL.match(email)):
        issues.append(_issue("error", "email", "Email address is not valid."))

    phone = _clean_text(raw.get("phone")) or ""
    digits = re.sub(r"\D", "", phone)
    if phone and not (7 <= len(digits) <= 15):
        issues.append(_issue("error", "phone", "Phone number needs 7 to 15 digits."))
    elif len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    out["phone"] = f"{digits[:3]}-{digits[3:6]}-{digits[6:]}" if len(digits) == 10 else phone

    for key, allowed, label in (
        ("work_arrangement", WORK_ARRANGEMENTS, "Work arrangement"),
        ("employment_type", EMPLOYMENT_TYPES, "Employment type"),
        ("onshore_offshore", ONSHORE_OFFSHORE, "Onshore / offshore"),
    ):
        value = _clean_text(raw.get(key)) or ""
        out[key] = value
        if value not in allowed:
            issues.append(_issue("error", key, f"{label} must be one of: {', '.join(allowed)}."))

    dates: dict[str, date] = {}
    for key, label in (("document_date", "Document date"), ("start_date", "Start date")):
        text = _clean_text(raw.get(key)) or ""
        out[key] = text
        try:
            parsed = date.fromisoformat(text)
        except ValueError:
            if text:
                issues.append(_issue("error", key, f"{label} is not a valid date."))
            continue
        if not 2000 <= parsed.year <= 2100:
            issues.append(_issue("error", key, f"{label} is outside 2000-2100."))
        else:
            dates[key] = parsed

    for key in _REQUIRED:
        if not out.get(key):
            label = _TEXT_FIELDS.get(key, (key.replace("_", " ").capitalize(), 0))[0]
            issues.append(_issue("error", key, f"{label} is required."))
    if out["work_arrangement"] in ("in_office", "hybrid") and not out["office_location"]:
        issues.append(_issue("error", "office_location",
                             "Office location is required unless the role is remote."))
    if not company_name:
        issues.append(_issue("error", "company_id", "Company name is required."))

    out["company_name"] = company_name
    _raise_if_errors(issues, "The form has problems")

    if "start_date" in dates and "document_date" in dates and dates["start_date"] < dates["document_date"]:
        issues.append(_issue("warning", "start_date", "The start date is before the document date."))
    return out, [i for i in issues if i["severity"] != "error"]


# --------------------------------------------------------------------------- #
#  Compensation
# --------------------------------------------------------------------------- #

FREQUENCIES = ("weekly", "biweekly", "semimonthly", "monthly")
COMMISSION_BASIS = "Gross Profit"
MAX_TIERS = 10
_CENT = 0.011

_FREQ_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("biweekly", re.compile(r"bi-?\s?weekly|every\s+(?:other|two)\s+weeks?|fortnight", re.I)),
    ("semimonthly", re.compile(r"semi-?\s?monthly|twice\s+a\s+month", re.I)),
    ("weekly", re.compile(r"(?<![A-Za-z])weekly\b|per\s+week\b|every\s+week\b", re.I)),
    ("monthly", re.compile(r"(?<![A-Za-z])monthly\b|per\s+month\b", re.I)),
)


def frequencies_in_text(text: str) -> list[str]:
    """Pay frequencies the text mentions, deterministically. 'bi-weekly' must not
    also count as 'weekly', so the more specific patterns win the span."""
    found: list[str] = []
    remaining = text
    for name, pattern in _FREQ_PATTERNS:
        if pattern.search(remaining):
            found.append(name)
            remaining = pattern.sub(" ", remaining)
    return found


def _number(value: Any, field: str, issues: list[dict[str, str]]) -> float | None:
    if isinstance(value, bool) or value is None:
        issues.append(_issue("error", field, "A number is required."))
        return None
    if isinstance(value, str):
        value = value.replace("$", "").replace(",", "").strip()
    try:
        number = float(value)
    except (TypeError, ValueError):
        issues.append(_issue("error", field, f"'{value}' is not a number."))
        return None
    if not math.isfinite(number):
        issues.append(_issue("error", field, "The number is not finite."))
        return None
    return round(number, 2)


def check_compensation(raw: Any) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    """Validate a compensation structure. Never raises: the caller decides whether
    an error is fatal (generate) or just something to show (parse)."""
    issues: list[dict[str, str]] = []
    if not isinstance(raw, dict):
        return None, [_issue("error", "compensation", "Compensation is missing.")]

    clean: dict[str, Any] = {"base": None, "commission": None, "overtime": None,
                             "pay_frequency_mentioned": None}

    base = raw.get("base")
    if not isinstance(base, dict):
        issues.append(_issue("error", "base", "A base rate (hourly or salary) is required."))
    else:
        kind = base.get("type")
        amount = _number(base.get("amount"), "base.amount", issues)
        if kind not in ("hourly", "salary"):
            issues.append(_issue("error", "base.type", "Base pay must be hourly or salary."))
        elif amount is not None:
            ceiling = 1_000 if kind == "hourly" else 10_000_000
            if amount <= 0 or amount > ceiling:
                issues.append(_issue("error", "base.amount",
                                     f"The {kind} amount must be above 0 and at most {ceiling:,}."))
            else:
                clean["base"] = {"type": kind, "amount": amount}
                if kind == "hourly" and amount > 300:
                    issues.append(_issue("warning", "base.amount", "An hourly rate above $300 looks unusual."))
                if kind == "salary" and amount < 1_000:
                    issues.append(_issue("warning", "base.amount", "A salary below $1,000/year looks like an hourly rate."))
                if kind == "salary":
                    issues.append(_issue("warning", "base.type",
                                         "The master letter words the rate as $X/hour; a salary prints as $X/year. Confirm the wording."))

    commission = raw.get("commission")
    if commission is not None:
        if not isinstance(commission, dict):
            issues.append(_issue("error", "commission", "Commission must be an object."))
        else:
            basis = _clean_text(commission.get("basis")) or ""
            if basis.lower() != COMMISSION_BASIS.lower():
                issues.append(_issue(
                    "error", "commission.basis",
                    f"Commission basis must be '{COMMISSION_BASIS}' (the letter's footnote defines only that)"
                    + (f"; got '{basis}'." if basis else ".")))
            tiers_raw = commission.get("tiers")
            tiers: list[dict[str, Any]] = []
            if not isinstance(tiers_raw, list) or not tiers_raw:
                issues.append(_issue("error", "commission.tiers", "Commission needs at least one tier."))
            elif len(tiers_raw) > MAX_TIERS:
                issues.append(_issue("error", "commission.tiers", f"At most {MAX_TIERS} tiers fit the letter."))
            else:
                for n, tier in enumerate(tiers_raw, 1):
                    where = f"commission.tiers[{n}]"
                    if not isinstance(tier, dict):
                        issues.append(_issue("error", where, f"Tier {n} is not an object."))
                        continue
                    lo = _number(tier.get("min"), f"{where}.min", issues)
                    hi_raw = tier.get("max")
                    hi = None if hi_raw in (None, "") else _number(hi_raw, f"{where}.max", issues)
                    rate = _number(tier.get("rate_pct"), f"{where}.rate_pct", issues)
                    if lo is None or rate is None or (hi_raw not in (None, "") and hi is None):
                        continue
                    if lo < 0:
                        issues.append(_issue("error", f"{where}.min", f"Tier {n}: the lower bound cannot be negative."))
                    if hi is not None and hi <= lo:
                        issues.append(_issue("error", f"{where}.max", f"Tier {n}: the upper bound must exceed the lower bound."))
                    if not 0 < rate <= 100:
                        issues.append(_issue("error", f"{where}.rate_pct", f"Tier {n}: the rate must be above 0% and at most 100%."))
                    if max(lo, hi or 0) > 1_000_000_000:
                        issues.append(_issue("error", where, f"Tier {n}: an amount is implausibly large."))
                    tiers.append({"min": lo, "max": hi, "rate_pct": rate})
                tiers.sort(key=lambda t: t["min"])
                if tiers and tiers[0]["min"] != 0:
                    issues.append(_issue("warning", "commission.tiers", "The first tier does not start at 0."))
                for a, b in zip(tiers, tiers[1:]):
                    if a["max"] is None:
                        issues.append(_issue("error", "commission.tiers", "Only the last tier may have no upper bound."))
                    elif b["min"] <= a["max"]:
                        issues.append(_issue("error", "commission.tiers",
                                             f"Tiers overlap: {a['max']:,.2f} runs into {b['min']:,.2f}."))
                    elif b["min"] - a["max"] > _CENT:
                        issues.append(_issue("error", "commission.tiers",
                                             f"Gap between tiers: nothing covers {a['max']:,.2f} to {b['min']:,.2f}."))
                if tiers and tiers[-1]["max"] is None:
                    issues.append(_issue("warning", "commission.tiers",
                                         "The last tier is open-ended; the letter has no approved wording for that and prints 'and above'."))
            if tiers and not any(i["severity"] == "error" and i["field"].startswith("commission") for i in issues):
                clean["commission"] = {"basis": COMMISSION_BASIS, "tiers": tiers}

    overtime = raw.get("overtime")
    if overtime is not None:
        if not isinstance(overtime, dict):
            issues.append(_issue("error", "overtime", "Overtime must be an object."))
        else:
            rate = _number(overtime.get("amount"), "overtime.amount", issues)
            if rate is not None:
                if rate <= 0 or rate > 1_500:
                    issues.append(_issue("error", "overtime.amount", "The overtime rate must be above 0 and at most 1,500."))
                else:
                    clean["overtime"] = {"amount": rate}
                    base_clean = clean["base"]
                    if base_clean and base_clean["type"] == "hourly" and abs(rate - base_clean["amount"] * 1.5) > 0.011:
                        issues.append(_issue("warning", "overtime.amount",
                                             f"The letter says 'time and one-half', but ${rate:g} is not 1.5 x ${base_clean['amount']:g}."))
                    if base_clean and base_clean["type"] == "salary":
                        issues.append(_issue("warning", "overtime.amount", "Overtime is listed for a salaried role."))

    freq = raw.get("pay_frequency_mentioned")
    if freq is not None:
        if freq not in FREQUENCIES:
            issues.append(_issue("error", "pay_frequency_mentioned", f"Pay frequency must be one of: {', '.join(FREQUENCIES)}."))
        else:
            clean["pay_frequency_mentioned"] = freq

    if any(i["severity"] == "error" for i in issues):
        return None, issues
    return clean, issues


_NUM = re.compile(r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)\s*([kKmM])?(?![\w])")


def numbers_in_text(text: str) -> set[float]:
    found: set[float] = set()
    for match in _NUM.finditer(text):
        try:
            value = float(match.group(1).replace(",", ""))
        except ValueError:
            continue
        suffix = (match.group(2) or "").lower()
        value *= 1_000 if suffix == "k" else 1_000_000 if suffix == "m" else 1
        found.add(round(value, 2))
    return found


def unverified_numbers(comp: dict[str, Any], source_text: str) -> list[tuple[str, float]]:
    """Numbers in a model's answer that the person never wrote. A model that
    'helpfully' rounds or invents a figure is the failure that matters here, so
    every number must trace back to the source text (a tier ending one cent below
    the next one's start is the letter's own convention and is allowed)."""
    seen = numbers_in_text(source_text)
    allowed = seen | {round(n - 0.01, 2) for n in seen} | {0.0}
    pairs: list[tuple[str, float]] = []
    if comp.get("base"):
        pairs.append(("base.amount", comp["base"]["amount"]))
    for n, tier in enumerate((comp.get("commission") or {}).get("tiers", []), 1):
        pairs.append((f"commission.tiers[{n}].min", tier["min"]))
        if tier["max"] is not None:
            pairs.append((f"commission.tiers[{n}].max", tier["max"]))
        pairs.append((f"commission.tiers[{n}].rate_pct", tier["rate_pct"]))
    if comp.get("overtime"):
        pairs.append(("overtime.amount", comp["overtime"]["amount"]))
    return [(field, value) for field, value in pairs
            if not any(abs(value - a) < 0.006 for a in allowed)]


# --------------------------------------------------------------------------- #
#  Formatting of compensation text — mirrors the master's own conventions
# --------------------------------------------------------------------------- #

def _money(amount: float) -> str:
    text = f"{amount:,.2f}"
    return "$" + (text[:-3] if text.endswith(".00") else text)


def format_base(base: dict[str, Any]) -> str:
    return f"{_money(base['amount'])}/{'hour' if base['type'] == 'hourly' else 'year'}"


def format_overtime(overtime: dict[str, Any]) -> str:
    return f"{_money(overtime['amount'])} per hour"


def format_range(tier: dict[str, Any]) -> str:
    lo = "0" if tier["min"] == 0 else f"{tier['min']:,.2f}"
    if tier["max"] is None:
        return f"{lo} and above"
    return f"{lo} to {tier['max']:,.2f}"


def format_rate(tier: dict[str, Any]) -> str:
    return f"{tier['rate_pct']:g}%"


# --------------------------------------------------------------------------- #
#  Reading the master
# --------------------------------------------------------------------------- #

#: Field -> the plain-text label the master uses for it.
LABELS: dict[str, str] = {
    "employee_name": "Employee Name",
    "address_line": "Address",
    "state_zip": "State Zip Code",
    "phone": "Contact Number",
    "email": "Email Address",
    "company_name": "Company Name",
    "job_title": "Job Title",
    "office_location": "Office Location",
    "manager_name": "Manager Name",
}

#: Without these the letter would print a placeholder or the wrong figure.
_CORE_SLOTS = ("employee_name", "company_name", "job_title", "manager_name",
               "document_date", "start_date", "wage_rate")

_LABEL_RE = re.compile(
    "|".join(r"(?<![A-Za-z0-9])" + re.escape(label) + r"(?![A-Za-z0-9])"
             for label in sorted(LABELS.values(), key=len, reverse=True)))
_FIELD_BY_LABEL = {label: field for field, label in LABELS.items()}

_DATE_RE = re.compile(r"(?:%s) \d{1,2}, \d{4}" % "|".join(_MONTHS))
_START_DATE_CONTEXT = re.compile(r"start date is\s*$", re.I)
_REMOTE_PHRASE = " at our Office Location office"
_RATE = r"\$\s?\d[\d,]*(?:\.\d+)?\s*(?:/|per\s+)\s*"
_WAGE_RATE_RE = re.compile(_RATE + r"(?:hour|hr|year|yr|week|month)\b")
_OVERTIME_RATE_RE = re.compile(_RATE + r"hour\b")
_PAYROLL_RE = re.compile(r"You will be paid\s+([A-Za-z-]+)")

_TBL_OPEN = re.compile(rb"<w:tbl(?=[ >])")
_TBL_CLOSE = re.compile(rb"</w:tbl>")
_TR = re.compile(rb"<w:tr[ >].*?</w:tr>", re.DOTALL)
_TC = re.compile(rb"<w:tc[ >]")
_IDS = re.compile(rb'(w14:(?:paraId|textId))="([0-9A-Fa-f]{8})"')
_UNSAFE_TO_DROP = (b"<w:sectPr", b"<w:drawing", b"<w:pict", b"<w:br", b"<w:fldChar",
                   b"<w:footnoteReference", b"<w:bookmarkStart")


@dataclass
class _Para:
    index: int
    start: int
    end: int
    text: str


@dataclass
class _Edit:
    start: int
    end: int
    text: str
    field: str


@dataclass
class _Replacement:
    start: int
    end: int
    data: bytes


#: A line break or tab between two `w:t` elements is invisible to a plain join,
#: so "State Zip Code<br/>Contact Number<br/>Email Address" read as one word and
#: no label matched. Each one counts as a single separator character instead.
_BREAK = re.compile(rb"<w:(br|cr)(?=[ />])|<w:(tab)(?=[ />])")


def _spans_and_text(paragraph: bytes) -> tuple[list[om.Span], str]:
    spans: list[om.Span] = []
    parts: list[str] = []
    cursor = 0
    previous_end: int | None = None
    for raw in om._spans(paragraph):
        if previous_end is not None:
            for m in _BREAK.finditer(paragraph[previous_end:raw.start]):
                parts.append("\t" if m.group(2) else "\n")
                cursor += 1
        spans.append(om.Span(raw.open_tag, raw.text, raw.close_tag, raw.start, raw.end,
                             cursor, cursor + len(raw.text)))
        parts.append(raw.text)
        cursor += len(raw.text)
        previous_end = raw.end
    return spans, "".join(parts)


def _paragraphs(xml: bytes) -> list[_Para]:
    out: list[_Para] = []
    for index, (start, end) in enumerate(om.paragraph_spans(xml)):
        text = _spans_and_text(xml[start:end])[1]
        out.append(_Para(index, start, end, text))
    return out


def _table_ranges(xml: bytes) -> list[tuple[int, int]]:
    events = sorted([(m.start(), 1, m.end()) for m in _TBL_OPEN.finditer(xml)] +
                    [(m.start(), -1, m.end()) for m in _TBL_CLOSE.finditer(xml)])
    ranges: list[tuple[int, int]] = []
    depth = 0
    start = 0
    for pos, kind, end in events:
        if kind == 1:
            if depth == 0:
                start = pos
            depth += 1
        elif depth:
            depth -= 1
            if depth == 0:
                ranges.append((start, end))
    return ranges


def _inside(pos: int, ranges: list[tuple[int, int]]) -> bool:
    return any(a <= pos < b for a, b in ranges)


def _read_docx(docx: bytes) -> tuple[zipfile.ZipFile, dict[str, bytes]]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(docx))
    except zipfile.BadZipFile as exc:
        raise TemplateShapeError("The template is not a readable .docx.") from exc
    parts: dict[str, bytes] = {}
    total = 0
    for info in archive.infolist():
        if not om.TEXT_PARTS.match(info.filename):
            continue
        if info.file_size > om.MAX_PART_BYTES:
            raise TemplateShapeError(f"{info.filename} is over the size limit.")
        total += info.file_size
        if total > om.MAX_TOTAL_BYTES:
            raise TemplateShapeError("The template's text parts are over the size limit.")
        data = archive.read(info.filename)
        if b"<!DOCTYPE" in data[:2048] or b"<!ENTITY" in data[:8192]:
            raise TemplateShapeError("The template declares an XML DTD. Refused.")
        parts[info.filename] = data
    if "word/document.xml" not in parts:
        raise TemplateShapeError("The template has no word/document.xml.")
    return archive, parts


@dataclass
class _Anchors:
    wage: _Para | None = None
    commission: _Para | None = None
    overtime: _Para | None = None
    payroll: _Para | None = None


def _find_anchors(paras: list[_Para], tables: list[tuple[int, int]]) -> _Anchors:
    anchors = _Anchors()
    for p in paras:
        if _inside(p.start, tables):
            continue
        text = p.text.lstrip()
        if anchors.wage is None and text.startswith("Wages/Rate of Pay"):
            anchors.wage = p
        elif anchors.commission is None and text.startswith("Commission:"):
            anchors.commission = p
        elif anchors.overtime is None and text.startswith("Overtime:"):
            anchors.overtime = p
        elif anchors.payroll is None and text.startswith("You will be paid"):
            anchors.payroll = p
    return anchors


def inspect_template(docx: bytes) -> dict[str, Any]:
    """What this generator can edit in a template. Cheap, read-only, and the
    reason a wrong template is rejected when it is picked, not when it is used."""
    archive, parts = _read_docx(docx)
    xml = parts["word/document.xml"]
    paras = _paragraphs(xml)
    tables = _table_ranges(xml)
    anchors = _find_anchors(paras, tables)
    logo_slot = letter_logo.find_logo(
        archive.namelist(), lambda n: parts[n] if n in parts else archive.read(n))

    labels: dict[str, int] = {}
    dates = {"document_date": 0, "start_date": 0}
    for name, data in parts.items():
        for p in _paragraphs(data):
            for m in _LABEL_RE.finditer(p.text):
                field = _FIELD_BY_LABEL[m.group(0)]
                labels[field] = labels.get(field, 0) + 1
            for m in _DATE_RE.finditer(p.text):
                key = "start_date" if _START_DATE_CONTEXT.search(p.text[:m.start()]) else "document_date"
                dates[key] += 1

    rate_found = bool(anchors.wage and _WAGE_RATE_RE.search(anchors.wage.text))
    table_rows = 0
    if anchors.commission:
        found = _commission_table(xml, anchors.commission, tables)
        table_rows = len(_TR.findall(xml[found[0]:found[1]])) if found else 0

    payroll = _PAYROLL_RE.search(anchors.payroll.text) if anchors.payroll else None
    slots = {**{f: labels.get(f, 0) for f in LABELS}, **dates, "wage_rate": int(rate_found)}
    missing = [f for f in _CORE_SLOTS if not slots.get(f)]
    problems: list[str] = []
    if missing:
        problems.append("Missing: " + ", ".join(missing))
    if anchors.commission and not table_rows:
        problems.append("A Commission paragraph exists but no table follows it.")
    return {
        "slots": slots,
        "compensation": {
            "wage": bool(anchors.wage), "commission": bool(anchors.commission),
            "commission_table_rows": table_rows, "overtime": bool(anchors.overtime),
        },
        "pay_frequency": payroll.group(1).lower() if payroll else None,
        "logo": {"found": logo_slot is not None,
                 "part": logo_slot.media if logo_slot else None,
                 "box_emu": list(logo_slot.box) if logo_slot else None},
        "missing_core": missing,
        "expected_labels": list(LABELS.values()),
        "usable": not missing and not (anchors.commission and not table_rows),
        "problems": problems,
    }


def _commission_table(xml: bytes, commission: _Para,
                      tables: list[tuple[int, int]]) -> tuple[int, int] | None:
    for start, end in tables:
        if start >= commission.end and not xml[commission.end:start].strip():
            return start, end
    return None


# --------------------------------------------------------------------------- #
#  Editing
# --------------------------------------------------------------------------- #

def _apply_edits(paragraph: bytes, edits: list[_Edit]) -> bytes:
    """Splice edits into one paragraph. A replacement is written into the run in
    which the replaced text STARTED, so that run's bold/size/font carry over;
    text outside every edit stays in the run that owned it."""
    spans, joined = _spans_and_text(paragraph)
    ordered = sorted(edits, key=lambda e: (e.start, e.end))
    previous = 0
    for e in ordered:
        if e.start < previous or e.end < e.start or e.end > len(joined):
            raise OfferError("Internal error: overlapping edits in one paragraph.")
        previous = e.end

    starts = [s.t_start for s in spans]
    pieces: list[list[str]] = [[] for _ in spans]

    def keep(a: int, b: int) -> None:
        for i, s in enumerate(spans):
            lo, hi = max(a, s.t_start), min(b, s.t_end)
            if lo < hi:
                pieces[i].append(joined[lo:hi])

    cursor = 0
    for e in ordered:
        keep(cursor, e.start)
        if e.text:
            owner = max(0, min(bisect_right(starts, e.start) - 1, len(spans) - 1))
            pieces[owner].append(e.text)
        cursor = e.end
    keep(cursor, len(joined))

    out = bytearray(paragraph)
    for i in range(len(spans) - 1, -1, -1):
        span = spans[i]
        new_text = "".join(pieces[i])
        if new_text == span.text:
            continue
        open_tag = span.open_tag
        if new_text != new_text.strip() and not om._WT_OPEN_HAS_SPACE.search(open_tag):
            open_tag = open_tag[:-1] + b' xml:space="preserve">'
        clean = om._CONTROL.sub("", new_text)
        out[span.start:span.end] = open_tag + escape(clean).encode("utf-8") + span.close_tag
    return bytes(out)


@dataclass
class _Context:
    values: dict[str, str]          # field -> text to print
    remote: bool
    compensation: dict[str, Any] | None


def _label_values(fields: dict[str, str]) -> dict[str, str]:
    street = ", ".join(p for p in (fields["address1"], fields["address2"], fields["city"]) if p)
    return {
        "employee_name": fields["employee_name"],
        "address_line": street,
        "state_zip": f"{fields['state']} {fields['zip']}".strip(),
        "phone": fields["phone"],
        "email": fields["email"],
        "company_name": fields["company_name"],
        "job_title": fields["job_title"],
        "office_location": fields["office_location"],
        "manager_name": fields["report_to"],
    }


def _takes_an(word: str) -> bool:
    """'an' before a vowel SOUND. Good enough for job titles; the exceptions are
    the ones that actually occur (University, Uber, Hour)."""
    first = word.strip().lower()
    if first.startswith(("uni", "use", "usu", "eu", "one", "uber")):
        return False
    return first.startswith(("a", "e", "i", "o", "u", "hour", "honest", "heir"))


def _field_edits(text: str, ctx: _Context, counts: dict[str, int],
                 notes: set[str]) -> list[_Edit]:
    edits: list[_Edit] = []
    removed: list[tuple[int, int]] = []

    if ctx.remote:
        at = text.find(_REMOTE_PHRASE)
        if at >= 0:
            edits.append(_Edit(at, at + len(_REMOTE_PHRASE), "", "office_phrase_removed"))
            removed.append((at, at + len(_REMOTE_PHRASE)))
            notes.add("Remote role: the 'at our <office> office' phrase was removed. That wording is "
                      "not in an approved template; confirm it.")

    for m in _LABEL_RE.finditer(text):
        field = _FIELD_BY_LABEL[m.group(0)]
        if any(a <= m.start() < b for a, b in removed):
            continue
        if field == "office_location" and ctx.remote:
            raise TemplateShapeError(
                "This is a remote letter, but the template mentions Office Location outside the "
                "'at our Office Location office' phrase, so it cannot be removed safely.")
        value = ctx.values[field]
        start = m.start()
        if field == "manager_name" and text[max(0, start - 4):start].lower() == "the ":
            edits.append(_Edit(start - 4, start, "", "manager_article_removed"))
        if field == "company_name" and text[m.end():m.end() + 1] == "(":
            value += " "
        if field == "job_title":
            article = text[max(0, start - 3):start]
            if article in (" a ", " an "):
                fixed = " an " if _takes_an(value) else " a "
                if fixed != article:
                    edits.append(_Edit(start - len(article), start, fixed, "article_fixed"))
                    notes.add("Changed 'a'/'an' before the job title to agree with it.")
        edits.append(_Edit(m.start(), m.end(), value, field))

    document_date = ctx.values["document_date"]
    start_date = ctx.values["start_date"]
    for m in _DATE_RE.finditer(text):
        is_start = bool(_START_DATE_CONTEXT.search(text[:m.start()]))
        edits.append(_Edit(m.start(), m.end(), start_date if is_start else document_date,
                           "start_date" if is_start else "document_date"))

    for e in edits:
        counts[e.field] = counts.get(e.field, 0) + 1
    return edits


def _next_ids(xml: bytes):
    highest = max((int(m.group(2), 16) for m in _IDS.finditer(xml)), default=0)
    counter = highest

    def allocate() -> str:
        nonlocal counter
        counter += 1
        if counter >= 0x80000000:
            raise TemplateShapeError("The document ran out of paragraph ids.")
        return f"{counter:08X}"
    return allocate


def _commission_rows(xml: bytes, table: tuple[int, int], tiers: list[dict[str, Any]]) -> _Replacement:
    """New data rows for the commission table. The header row is untouched. Each
    new row is a copy of the template row at the same position (so shading that
    alternates survives) with only its cell text changed."""
    body = xml[table[0]:table[1]]
    if len(_TBL_OPEN.findall(body)) > 1:
        raise TemplateShapeError("The commission table contains a nested table.")
    rows = [(m.start(), m.end()) for m in _TR.finditer(body)]
    if len(rows) < 2:
        raise TemplateShapeError("The commission table needs a header row and at least one data row.")
    templates = rows[1:]
    allocate = _next_ids(xml)
    built: list[bytes] = []
    for n, tier in enumerate(tiers):
        a, b = templates[min(n, len(templates) - 1)]
        row = body[a:b]
        if len(_TC.findall(row)) != 2:
            raise TemplateShapeError("Commission table rows must have exactly two cells (range, rate).")
        cells = [format_range(tier), format_rate(tier)]
        cell_paragraphs = om.paragraph_spans(row)
        if len(cell_paragraphs) != 2:
            raise TemplateShapeError("Each commission cell must hold exactly one paragraph.")
        out = bytearray()
        cursor = 0
        for index, (ps, pe) in enumerate(cell_paragraphs):
            joined = _spans_and_text(row[ps:pe])[1]
            out += row[cursor:ps]
            if joined:
                out += _apply_edits(row[ps:pe], [_Edit(0, len(joined), cells[index], "commission_cell")])
            else:
                out += row[ps:pe]
            cursor = pe
        out += row[cursor:]
        row = bytes(out)
        if n >= len(templates):
            row = _IDS.sub(lambda m: m.group(1) + b'="' + allocate().encode() + b'"', row)
        built.append(row)
    return _Replacement(table[0] + templates[0][0], table[0] + templates[-1][1], b"".join(built))


def _droppable_blank(xml: bytes, para: _Para) -> bool:
    raw = xml[para.start:para.end]
    return not para.text.strip() and not any(tag in raw for tag in _UNSAFE_TO_DROP)


def _plan_document(xml: bytes, ctx: _Context, counts: dict[str, int],
                   notes: set[str], removed: list[str]) -> list[_Replacement]:
    paras = _paragraphs(xml)
    tables = _table_ranges(xml)
    anchors = _find_anchors(paras, tables)
    comp = ctx.compensation
    plan: list[_Replacement] = []
    skip: set[int] = set()
    extra: dict[int, list[_Edit]] = {}

    if comp is not None:
        if anchors.wage is None:
            raise TemplateShapeError("The template has no 'Wages/Rate of Pay' paragraph to fill.")
        rate = _WAGE_RATE_RE.search(anchors.wage.text)
        if not rate:
            raise TemplateShapeError("The wages paragraph has no rate (like $22/hour) to replace.")
        extra[anchors.wage.index] = [_Edit(rate.start(), rate.end(), format_base(comp["base"]), "wage_rate")]

        # Overtime.
        if comp["overtime"]:
            if anchors.overtime is None:
                raise TemplateShapeError("Overtime was given, but the template has no Overtime paragraph.")
            ot = _OVERTIME_RATE_RE.search(anchors.overtime.text)
            if not ot:
                raise TemplateShapeError("The overtime paragraph has no rate (like $33 per hour) to replace.")
            extra[anchors.overtime.index] = [_Edit(ot.start(), ot.end(), format_overtime(comp["overtime"]), "overtime_rate")]
        elif anchors.overtime is not None:
            plan.append(_Replacement(anchors.overtime.start, anchors.overtime.end, b""))
            skip.add(anchors.overtime.index)
            removed.append("overtime")

        # Commission.
        if anchors.commission is not None:
            table = _commission_table(xml, anchors.commission, tables)
            if table is None:
                raise TemplateShapeError("The Commission paragraph has no table directly after it.")
            in_table = {p.index for p in paras if table[0] <= p.start < table[1]}
            if comp["commission"]:
                plan.append(_commission_rows(xml, table, comp["commission"]["tiers"]))
                skip |= in_table
                counts["commission_rows"] = len(comp["commission"]["tiers"])
                if any(t["max"] is None for t in comp["commission"]["tiers"]):
                    notes.add("The open-ended top tier prints as 'and above'; that wording is not in the master.")
            else:
                plan.append(_Replacement(anchors.commission.start, anchors.commission.end, b""))
                plan.append(_Replacement(table[0], table[1], b""))
                skip |= in_table | {anchors.commission.index}
                after = next((p for p in paras if p.start >= table[1]), None)
                if after and after.start == table[1] and _droppable_blank(xml, after):
                    plan.append(_Replacement(after.start, after.end, b""))
                    skip.add(after.index)
                removed.append("commission")
        elif comp["commission"]:
            raise TemplateShapeError("Commission was given, but the template has no Commission section.")

    for p in paras:
        if p.index in skip:
            continue
        edits = _field_edits(p.text, ctx, counts, notes) + extra.get(p.index, [])
        if edits:
            plan.append(_Replacement(p.start, p.end, _apply_edits(xml[p.start:p.end], edits)))
    for index, edits in extra.items():
        for e in edits:
            counts[e.field] = counts.get(e.field, 0) + 1
    return plan


def _apply_plan(xml: bytes, plan: list[_Replacement]) -> bytes:
    ordered = sorted(plan, key=lambda r: (r.start, r.end))
    previous = 0
    for r in ordered:
        if r.start < previous or r.end < r.start:
            raise OfferError("Internal error: overlapping document edits.")
        previous = r.end
    out = bytearray(xml)
    for r in reversed(ordered):
        out[r.start:r.end] = r.data
    return bytes(out)


def _expected_static(xml: bytes) -> list[str]:
    """Paragraph texts that must survive generation unchanged.

    Derived from the ORIGINAL by rule, never from the edit plan: a check built on
    the plan excuses any paragraph the plan wrongly decided to touch. A paragraph
    may change only if it holds a label, a date or the remote phrase, is one of
    the compensation anchors, or sits in the commission table.
    """
    paras = _paragraphs(xml)
    tables = _table_ranges(xml)
    anchors = _find_anchors(paras, tables)
    commission_table = _commission_table(xml, anchors.commission, tables) if anchors.commission else None
    editable = {a.index for a in (anchors.wage, anchors.commission, anchors.overtime) if a}
    out: list[str] = []
    for p in paras:
        if p.index in editable or (commission_table and commission_table[0] <= p.start < commission_table[1]):
            continue
        if _LABEL_RE.search(p.text) or _DATE_RE.search(p.text) or _REMOTE_PHRASE in p.text:
            continue
        if p.text.strip():
            out.append(p.text)
    return out


def _is_subsequence(needle: list[str], haystack: list[str]) -> bool:
    it = iter(haystack)
    return all(any(item == h for h in it) for item in needle)


def _verify(original: bytes, generated: bytes, edited_parts: set[str],
            untouched_texts: list[str], renames: dict[str, str] | None = None,
            media: dict[str, bytes] | None = None) -> None:
    """Refuse to return a file that is not what we meant to make."""
    renames, media = renames or {}, media or {}
    try:
        before = zipfile.ZipFile(io.BytesIO(original))
        after = zipfile.ZipFile(io.BytesIO(generated))
    except zipfile.BadZipFile as exc:
        raise IntegrityFailure("The generated file is not a valid .docx archive.") from exc
    expected = [renames.get(i.filename, i.filename) for i in before.infolist()]
    if expected != [i.filename for i in after.infolist()]:
        raise IntegrityFailure("The generated file changed the package's file list.")
    for info in before.infolist():
        new = after.read(renames.get(info.filename, info.filename))
        if info.filename in media:
            if new != media[info.filename]:
                raise IntegrityFailure("The logo was not written into the package correctly.")
        elif info.filename in edited_parts:
            try:
                ET.fromstring(new)
            except ET.ParseError as exc:
                raise IntegrityFailure(f"{info.filename} is no longer well-formed XML ({exc}).") from exc
        elif new != before.read(info.filename):
            raise IntegrityFailure(f"{info.filename} changed but was not meant to.")
    if renames:
        new_names = after.namelist()
        fresh = (letter_logo.dangling_relationships(new_names, after.read)
                 - letter_logo.dangling_relationships(before.namelist(), before.read))
        if fresh:
            raise IntegrityFailure("A relationship points at a part that no longer exists: "
                                   + "; ".join(sorted(fresh)[:3]))
        types = after.read("[Content_Types].xml")
        for new_name in renames.values():
            if not letter_logo.content_type_covers(types, new_name):
                raise IntegrityFailure(f"{new_name} has no declared content type.")
    texts = [p.text for p in _paragraphs(after.read("word/document.xml")) if p.text.strip()]
    if not _is_subsequence([t for t in untouched_texts if t.strip()], texts):
        raise IntegrityFailure("A paragraph that must stay static was altered or removed.")


def generate(docx: bytes, fields: dict[str, str], compensation: dict[str, Any],
             logo: bytes | None = None, logo_name: str = "") -> dict[str, Any]:
    """Fill the master. `fields` and `compensation` must already have passed
    `clean_fields` and `check_compensation`; they are re-checked cheaply here
    because this is the last stop before a binding document."""
    needed = ("employee_name", "address1", "address2", "city", "state", "zip", "email", "phone",
              "company_name", "job_title", "office_location", "report_to", "start_date",
              "document_date", "work_arrangement")
    absent = [k for k in needed if k not in fields]
    if absent or not isinstance(compensation, dict):
        raise ValidationFailed("Validated fields and compensation are required"
                               + (f" (missing: {', '.join(absent)})." if absent else "."))
    archive, parts = _read_docx(docx)
    info = inspect_template(docx)
    if info["missing_core"]:
        raise TemplateShapeError(
            "This template cannot be filled: it has no place for " + ", ".join(info["missing_core"])
            + ". Expected labels: " + ", ".join(f"'{v}'" for v in LABELS.values()) + ".",
            [_issue("error", "template", f"No slot for {name}.") for name in info["missing_core"]])

    template_freq = info["pay_frequency"]
    stated = compensation.get("pay_frequency_mentioned")
    if stated and template_freq and stated != template_freq:
        raise ValidationFailed(
            f"The compensation text says {stated}, but this template pays {template_freq}. "
            f"Use a {stated} template or correct the compensation.",
            [_issue("error", "pay_frequency_mentioned",
                    f"Text says {stated}; the template pays {template_freq}.")])

    ctx = _Context(values={**_label_values(fields),
                           "document_date": format_date(fields["document_date"]),
                           "start_date": format_date(fields["start_date"])},
                   remote=fields["work_arrangement"] == "remote", compensation=compensation)
    counts: dict[str, int] = {}
    notes: set[str] = set()
    removed: list[str] = []
    edited: dict[str, bytes] = {}
    untouched: list[str] = []

    for name, data in parts.items():
        if name == "word/document.xml":
            plan = _plan_document(data, ctx, counts, notes, removed)
            if plan:
                edited[name] = _apply_plan(data, plan)
            untouched = _expected_static(data)
        else:
            plan = []
            for p in _paragraphs(data):
                edits = _field_edits(p.text, ctx, counts, notes)
                if edits:
                    plan.append(_Replacement(p.start, p.end, _apply_edits(data[p.start:p.end], edits)))
            if plan:
                edited[name] = _apply_plan(data, plan)

    renames: dict[str, str] = {}
    media: dict[str, bytes] = {}
    logo_report: dict[str, Any] | None = None
    if logo is not None:
        def current(name: str) -> bytes:
            if name in edited:
                return edited[name]
            return parts[name] if name in parts else archive.read(name)
        try:
            change = letter_logo.replace_logo(archive.namelist(), current, logo, logo_name)
        except letter_logo.LogoProblem as exc:
            kind = TemplateShapeError if exc.template else ValidationFailed
            raise kind(str(exc), [_issue("error", "template" if exc.template else "logo_asset_id",
                                         str(exc))]) from exc
        edited.update(change.edited)
        renames, media, logo_report = change.renames, change.media, change.info
        notes.update(change.notes)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as out:
        for item in archive.infolist():
            payload = media[item.filename] if item.filename in media \
                else edited.get(item.filename, archive.read(item.filename))
            zi = zipfile.ZipInfo(renames.get(item.filename, item.filename), date_time=item.date_time)
            zi.compress_type = item.compress_type
            zi.external_attr = item.external_attr
            out.writestr(zi, payload)
    generated = buffer.getvalue()

    _verify(docx, generated, set(edited), untouched, renames, media)

    return {
        "docx": generated,
        "report": {
            "replaced": dict(sorted(counts.items())),
            "removed": removed,
            "parts_edited": sorted(edited),
            "static_paragraphs_verified": len(untouched),
            "notes": sorted(notes),
            "logo": logo_report,
        },
    }

"""Tests for the offer-letter engine. Standard library only, so they run anywhere:

    python3 -m unittest discover -s backend/tests -t backend

The fixture is built here, mimicking the master letter's real quirks: labels
split by line breaks, bold runs inside sentences, a commission table, list
paragraphs. A second class runs the same checks against the real master if it is
present at samples/NSG_Offer_Letter.docx (gitignored, so it is skipped in CI).
"""

from __future__ import annotations

import io
import pathlib
import struct
import unittest
import zipfile
import zlib

from app import letter_logo
from app import offer_letter as ol

NS = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
      'xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml"')


def run(text: str, bold: bool = False) -> str:
    props = "<w:rPr><w:b/></w:rPr>" if bold else ""
    return f'<w:r>{props}<w:t xml:space="preserve">{text}</w:t></w:r>'


def para(*runs: str, pid: str = "", extra_props: str = "") -> str:
    attr = f' w14:paraId="{pid}"' if pid else ""
    return f"<w:p{attr}><w:pPr>{extra_props}</w:pPr>{''.join(runs)}</w:p>"


def cell(text: str, pid: str) -> str:
    return f"<w:tc><w:tcPr/>{para(run(text), pid=pid)}</w:tc>"


def row(a: str, b: str, pid: int) -> str:
    return f"<w:tr><w:trPr/>{cell(a, f'{pid:08X}')}{cell(b, f'{pid + 1:08X}')}</w:tr>"


def png(width: int, height: int, full: bool = True) -> bytes:
    """A valid PNG. `full=False` is header-only: enough to parse, cheap at any size."""
    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    rows = b"".join(b"\x00" + b"\x20\x60\xc0" * width for _ in range(height)) if full else b""
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + (chunk(b"IDAT", zlib.compress(rows)) if full else b"") + chunk(b"IEND", b""))


def jpeg(width: int, height: int) -> bytes:
    sof = b"\x08" + struct.pack(">HH", height, width) + b"\x03" + b"\x01\x11\x00\x02\x11\x01\x03\x11\x01"
    return (b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
            + b"\xff\xc0" + struct.pack(">H", len(sof) + 2) + sof + b"\xff\xd9")


def gif(width: int, height: int) -> bytes:
    return b"GIF89a" + struct.pack("<HH", width, height) + b"\x00\x00\x00" + b"\x3b"


BOX = (1857375, 781050)
HEADER_XML = (
    f'<w:hdr {NS} xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><w:p><w:r><w:drawing>'
    f'<wp:inline><wp:extent cx="{BOX[0]}" cy="{BOX[1]}"/><wp:docPr descr="Old Co logo" id="1" name="image1.png"/>'
    '<a:graphic><a:graphicData uri="x"><pic:pic><pic:nvPicPr><pic:cNvPr descr="Old Co logo" id="0" name="image1.png"/>'
    '</pic:nvPicPr><pic:blipFill><a:blip r:embed="rId1"/></pic:blipFill><pic:spPr><a:xfrm>'
    f'<a:off x="0" y="0"/><a:ext cx="{BOX[0]}" cy="{BOX[1]}"/></a:xfrm></pic:spPr></pic:pic></a:graphicData></a:graphic>'
    '</wp:inline></w:drawing></w:r></w:p></w:hdr>')
REL = 'xmlns="http://schemas.openxmlformats.org/package/2006/relationships"'
IMG = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
TYPES = ('<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
         '<Default ContentType="application/xml" Extension="xml"/><Default ContentType="image/png" Extension="png"/>'
         '<Default ContentType="application/vnd.openxmlformats-package.relationships+xml" Extension="rels"/></Types>')


def build_docx(extra_paragraphs: str = "", logo: bool = False) -> bytes:
    br = '<w:br w:type="textWrapping"/>'
    body = "".join([
        para(run("Private and Confidential          ", True), run("September 23, 2026", True)),
        # The address block: labels separated by line breaks, as in the real file.
        f'<w:p><w:r><w:rPr><w:b/></w:rPr><w:t>Employee Name</w:t></w:r>'
        f'<w:r><w:t>,</w:t>{br}<w:t>Address</w:t></w:r><w:r><w:t>,</w:t></w:r>'
        f'<w:r>{br}<w:t>State</w:t></w:r><w:r><w:t> </w:t></w:r>'
        f'<w:r><w:t>Zip Code</w:t>{br}<w:t>Contact Number</w:t>{br}<w:t>Email Address</w:t></w:r></w:p>',
        para(run("Dear "), run("Employee Name", True), run(",")),
        para(run("On behalf of "), run("Company Name", True),
             run("(the “Company”), I am pleased to offer you a full-time position as a "),
             run("Job Title", True), run(" at our "), run("Office Location", True),
             run(" office. Your employment will be at-will.")),
        para(run("As of "), run("September 23, 2026", True), run(", this letter reflects the whole deal.")),
        para(run("Responsibilities:", True)),
        para(run("Your start date is "), run("October 05, 2026", True),
             run(". Upon your start date, you will report directly "), run("to the "),
             run("Manager Name", True), run(". Devote your best efforts.")),
        para(run("Compensation", True), run(":")),
        para(run("Wages/Rate of Pay", True), run(": rate of "), run("$22/hour", True),
             run(" as "), run("Job Title", True), run(" under "), run("Manager Name ", True),
             run("while employed by "), run("Company Name", True), run("."), extra_props="<w:numPr/>"),
        para(run("Commission:", True), run(" based on Gross Profit at the following rates:"),
             extra_props="<w:numPr/>"),
        f"<w:tbl><w:tblPr/><w:tblGrid/>"
        f"{row('Gross Profit', 'Commission Rate', 0xA0)}"
        f"{row('0 to 149,999.99', '5%', 0xB0)}"
        f"{row('150,000.00 to 299,999.99', '6%', 0xC0)}"
        f"{row('300,000.00 to 1,000,000.00', '7%', 0xD0)}</w:tbl>",
        para(),
        para(run("Overtime:", True), run(" time and one-half rate of "), run("$33 per hour.", True),
             extra_props="<w:numPr/>"),
        para(run("You will be paid "), run("weekly", True), run(" in accordance with the payroll cycle.")),
        para(run("Benefits:", True)),
        para(run("Health: eligible for the health package.")),
        para(run("Termination:", True)),
        para(run("This is an employment-at-will arrangement.")),
        extra_paragraphs,
    ])
    document = f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document {NS}><w:body>{body}</w:body></w:document>'
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", TYPES if logo else "<Types/>")
        z.writestr("word/document.xml", document)
        z.writestr("word/media/stuff.png", b"\x89PNG-not-really")
        if logo:
            z.writestr("word/header1.xml", HEADER_XML)
            z.writestr("word/_rels/header1.xml.rels",
                       f'<Relationships {REL}><Relationship Id="rId1" Type="{IMG}" Target="media/image1.png"/></Relationships>')
            z.writestr("word/_rels/footer1.xml.rels",
                       f'<Relationships {REL}><Relationship Id="rId7" Type="{IMG}" Target="media/image1.png"/></Relationships>')
            z.writestr("word/_rels/document.xml.rels", f'<Relationships {REL}/>')
            z.writestr("word/media/image1.png", png(499, 209))
    return out.getvalue()


FIELDS_RAW = dict(
    employee_name="Alex Morgan", address1="42 Example Lane", address2="Suite 5", city="Riverton",
    state="ca", zip="90001", email="alex.morgan@example.com", phone="(401) 555-0123",
    job_title="Account Coordinator", office_location="Riverton, California", report_to="Jordan Lee",
    start_date="2026-02-01", document_date="2026-01-15", work_arrangement="hybrid",
    employment_type="W2", onshore_offshore="onshore")

TIERS = [{"min": 0, "max": 99999.99, "rate_pct": 3}, {"min": 100000, "max": 249999.99, "rate_pct": 4}]
COMP_FULL = {"base": {"type": "hourly", "amount": 25},
             "commission": {"basis": "Gross Profit", "tiers": TIERS}, "overtime": {"amount": 37.5}}


def text_of(docx: bytes, part: str = "word/document.xml") -> list[str]:
    return [p.text for p in ol._paragraphs(zipfile.ZipFile(io.BytesIO(docx)).read(part)) if p.text.strip()]


def make(docx: bytes, raw: dict | None = None, comp: dict | None = None, **kw) -> dict:
    fields, _ = ol.clean_fields({**FIELDS_RAW, **(raw or {})}, "Sample Staffing LLC")
    clean, issues = ol.check_compensation(COMP_FULL if comp is None else comp)
    assert clean is not None, issues
    return ol.generate(docx, fields, clean, **kw)


class FieldValidation(unittest.TestCase):
    def test_normalises(self):
        fields, _ = ol.clean_fields(FIELDS_RAW, "Sample Staffing LLC")
        self.assertEqual(fields["state"], "CA")
        self.assertEqual(fields["phone"], "401-555-0123")

    def test_reports_every_problem_at_once(self):
        bad = {**FIELDS_RAW, "email": "nope", "zip": "1", "state": "California", "start_date": "2026-13-40"}
        with self.assertRaises(ol.ValidationFailed) as ctx:
            ol.clean_fields(bad, "Sample Staffing LLC")
        fields = {i["field"] for i in ctx.exception.issues}
        self.assertTrue({"email", "zip", "state", "start_date"} <= fields)

    def test_remote_needs_no_office(self):
        fields, _ = ol.clean_fields({**FIELDS_RAW, "work_arrangement": "remote", "office_location": ""}, "X LLC")
        self.assertEqual(fields["office_location"], "")

    def test_office_required_when_not_remote(self):
        with self.assertRaises(ol.ValidationFailed):
            ol.clean_fields({**FIELDS_RAW, "office_location": ""}, "X LLC")

    def test_rejects_non_text(self):
        with self.assertRaises(ol.ValidationFailed):
            ol.clean_fields({**FIELDS_RAW, "city": ["a"]}, "X LLC")

    def test_control_characters_and_newlines_are_flattened(self):
        fields, _ = ol.clean_fields({**FIELDS_RAW, "employee_name": "Alex\x00 \nMorgan"}, "X LLC")
        self.assertEqual(fields["employee_name"], "Alex Morgan")

    def test_start_before_document_is_a_warning(self):
        _, warnings = ol.clean_fields({**FIELDS_RAW, "start_date": "2025-01-01"}, "X LLC")
        self.assertTrue(any(w["field"] == "start_date" for w in warnings))


class CompensationValidation(unittest.TestCase):
    def check(self, comp):
        return ol.check_compensation(comp)

    def test_valid_full(self):
        clean, issues = self.check(COMP_FULL)
        self.assertIsNotNone(clean)
        self.assertFalse([i for i in issues if i["severity"] == "error"])

    def test_base_required(self):
        clean, issues = self.check({"base": None, "commission": None, "overtime": None})
        self.assertIsNone(clean)

    def test_overlap_gap_and_open_tier(self):
        def with_tiers(tiers):
            return self.check({"base": {"type": "hourly", "amount": 20},
                               "commission": {"basis": "Gross Profit", "tiers": tiers}, "overtime": None})
        overlap, _ = with_tiers([{"min": 0, "max": 100, "rate_pct": 5}, {"min": 50, "max": 200, "rate_pct": 6}])
        gap, _ = with_tiers([{"min": 0, "max": 100, "rate_pct": 5}, {"min": 500, "max": 900, "rate_pct": 6}])
        open_mid, _ = with_tiers([{"min": 0, "max": None, "rate_pct": 5}, {"min": 500, "max": 900, "rate_pct": 6}])
        self.assertIsNone(overlap)
        self.assertIsNone(gap)
        self.assertIsNone(open_mid)

    def test_basis_must_be_gross_profit(self):
        clean, issues = self.check({"base": {"type": "hourly", "amount": 20}, "overtime": None,
                                    "commission": {"basis": "sales", "tiers": [{"min": 0, "max": None, "rate_pct": 3}]}})
        self.assertIsNone(clean)
        self.assertTrue(any(i["field"] == "commission.basis" for i in issues))

    def test_bad_numbers(self):
        for amount in (0, -5, float("nan"), float("inf"), "abc", True, None, 10_000):
            clean, _ = self.check({"base": {"type": "hourly", "amount": amount}})
            self.assertIsNone(clean, amount)

    def test_numbers_may_arrive_as_strings(self):
        clean, _ = self.check({"base": {"type": "hourly", "amount": "$22.50"}})
        self.assertEqual(clean["base"]["amount"], 22.5)

    def test_too_many_tiers(self):
        tiers = [{"min": i * 10, "max": i * 10 + 9.99, "rate_pct": 1} for i in range(11)]
        clean, _ = self.check({"base": {"type": "hourly", "amount": 20},
                               "commission": {"basis": "Gross Profit", "tiers": tiers}})
        self.assertIsNone(clean)

    def test_overtime_that_is_not_time_and_a_half_warns(self):
        _, issues = self.check({"base": {"type": "hourly", "amount": 20}, "overtime": {"amount": 31}})
        self.assertTrue(any(i["field"] == "overtime.amount" and i["severity"] == "warning" for i in issues))

    def test_frequency_detection(self):
        f = ol.frequencies_in_text
        self.assertEqual(f("paid bi-weekly"), ["biweekly"])
        self.assertEqual(f("paid biweekly"), ["biweekly"])
        self.assertEqual(f("paid weekly on Friday"), ["weekly"])
        self.assertEqual(f("every other week"), ["biweekly"])
        self.assertEqual(f("$22/hour"), [])
        self.assertEqual(set(f("weekly, or biweekly for new hires")), {"weekly", "biweekly"})

    def test_unverified_numbers_catches_an_invented_figure(self):
        clean, _ = self.check({"base": {"type": "hourly", "amount": 23},
                               "commission": {"basis": "Gross Profit",
                                              "tiers": [{"min": 0, "max": 149999.99, "rate_pct": 5},
                                                        {"min": 150000, "max": None, "rate_pct": 6}]},
                               "overtime": {"amount": 33}})
        text = "$22/hour, 5% up to 150k and 6% above that. Overtime $33/hour."
        bad = [f for f, _ in ol.unverified_numbers(clean, text)]
        self.assertEqual(bad, ["base.amount"])


class Formatting(unittest.TestCase):
    def test_money_and_ranges_follow_the_masters_style(self):
        self.assertEqual(ol.format_base({"type": "hourly", "amount": 22.0}), "$22/hour")
        self.assertEqual(ol.format_base({"type": "hourly", "amount": 22.5}), "$22.50/hour")
        self.assertEqual(ol.format_base({"type": "salary", "amount": 65000.0}), "$65,000/year")
        self.assertEqual(ol.format_overtime({"amount": 37.5}), "$37.50 per hour")
        self.assertEqual(ol.format_range({"min": 0, "max": 149999.99, "rate_pct": 5}), "0 to 149,999.99")
        self.assertEqual(ol.format_range({"min": 150000, "max": 299999.99, "rate_pct": 6}), "150,000.00 to 299,999.99")
        self.assertEqual(ol.format_range({"min": 300000, "max": None, "rate_pct": 7}), "300,000.00 and above")
        self.assertEqual(ol.format_rate({"rate_pct": 5.0}), "5%")
        self.assertEqual(ol.format_rate({"rate_pct": 6.25}), "6.25%")
        self.assertEqual(ol.format_date("2026-10-05"), "October 05, 2026")


class Generation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.master = build_docx()
        cls.out = make(cls.master)
        cls.lines = text_of(cls.out["docx"])
        cls.blob = "\n".join(cls.lines)

    def test_inspect_finds_every_slot(self):
        info = ol.inspect_template(self.master)
        self.assertTrue(info["usable"], info)
        self.assertEqual(info["pay_frequency"], "weekly")
        self.assertEqual(info["slots"]["phone"], 1)
        self.assertEqual(info["slots"]["email"], 1)
        self.assertEqual(info["slots"]["state_zip"], 1)

    def test_address_block_is_filled_line_by_line(self):
        self.assertIn("Alex Morgan,\n42 Example Lane, Suite 5, Riverton,\nCA 90001\n401-555-0123\nalex.morgan@example.com",
                      self.lines[1])

    def test_no_placeholder_left_behind(self):
        for label in ol.LABELS.values():
            self.assertNotIn(label, self.blob, label)
        self.assertNotIn("September 23, 2026", self.blob.replace("January 15, 2026", ""))
        self.assertNotIn("$22/hour", self.blob)

    def test_dates_are_told_apart_by_context(self):
        self.assertIn("January 15, 2026", self.lines[0])
        self.assertIn("As of January 15, 2026", self.blob)
        self.assertIn("Your start date is February 01, 2026", self.blob)

    def test_manager_article_company_space_and_job_article(self):
        self.assertIn("report directly to Jordan Lee.", self.blob)
        self.assertNotIn("to the Jordan Lee", self.blob)
        self.assertIn("On behalf of Sample Staffing LLC (the", self.blob)
        self.assertIn("as an Account Coordinator at our Riverton, California office", self.blob)

    def test_wage_overtime_and_table(self):
        self.assertIn("rate of $25/hour as Account Coordinator", self.blob)
        self.assertIn("$37.50 per hour.", self.blob)
        self.assertIn("0 to 99,999.99", self.blob)
        self.assertIn("100,000.00 to 249,999.99", self.blob)
        self.assertNotIn("300,000.00 to 1,000,000.00", self.blob)

    def test_formatting_survives(self):
        xml = zipfile.ZipFile(io.BytesIO(self.out["docx"])).read("word/document.xml").decode()
        self.assertIn('<w:r><w:rPr><w:b/></w:rPr><w:t xml:space="preserve">Sample Staffing LLC </w:t>', xml)
        self.assertIn('<w:r><w:rPr><w:b/></w:rPr><w:t xml:space="preserve">$25/hour</w:t></w:r>', xml)

    def test_everything_outside_the_document_is_byte_identical(self):
        before = zipfile.ZipFile(io.BytesIO(self.master))
        after = zipfile.ZipFile(io.BytesIO(self.out["docx"]))
        for name in before.namelist():
            if name != "word/document.xml":
                self.assertEqual(before.read(name), after.read(name), name)

    def test_static_text_is_unchanged(self):
        for kept in ("Benefits:", "Health: eligible for the health package.", "Termination:",
                     "This is an employment-at-will arrangement.", "Gross Profit", "Commission Rate"):
            self.assertIn(kept, self.lines)

    def test_unique_paragraph_ids_after_cloning(self):
        comp = {**COMP_FULL, "commission": {"basis": "Gross Profit", "tiers": [
            {"min": 0, "max": 99.99, "rate_pct": 1}, {"min": 100, "max": 199.99, "rate_pct": 2},
            {"min": 200, "max": 299.99, "rate_pct": 3}, {"min": 300, "max": 399.99, "rate_pct": 4},
            {"min": 400, "max": 499.99, "rate_pct": 5}]}}
        xml = zipfile.ZipFile(io.BytesIO(make(self.master, comp=comp)["docx"])).read("word/document.xml").decode()
        import re
        ids = re.findall(r'w14:paraId="([0-9A-F]{8})"', xml)
        self.assertEqual(len(ids), len(set(ids)), "duplicate w14:paraId after cloning rows")
        self.assertTrue(all(int(i, 16) < 0x80000000 for i in ids))
        self.assertIn("400.00 to 499.99", xml)

    def test_values_cannot_inject_markup_or_new_placeholders(self):
        out = make(self.master, raw={"employee_name": "A & B <Co> Company Name"})
        blob = "\n".join(text_of(out["docx"]))
        self.assertIn("A & B <Co> Company Name", blob)           # printed literally, once
        zipfile.ZipFile(io.BytesIO(out["docx"])).read("word/document.xml").decode()
        import xml.etree.ElementTree as ET
        ET.fromstring(zipfile.ZipFile(io.BytesIO(out["docx"])).read("word/document.xml"))

    def test_only_overtime_removed(self):
        out = make(self.master, comp={**COMP_FULL, "overtime": None})
        blob = "\n".join(text_of(out["docx"]))
        self.assertNotIn("Overtime:", blob)
        self.assertIn("Commission:", blob)
        self.assertEqual(out["report"]["removed"], ["overtime"])

    def test_no_commission_removes_paragraph_table_and_spacer(self):
        out = make(self.master, comp={**COMP_FULL, "commission": None})
        xml = zipfile.ZipFile(io.BytesIO(out["docx"])).read("word/document.xml")
        blob = "\n".join(text_of(out["docx"]))
        self.assertNotIn(b"<w:tbl>", xml)
        self.assertNotIn("Commission", blob)
        self.assertNotIn("Gross Profit", blob)
        self.assertIn("Overtime:", blob)
        self.assertEqual(out["report"]["removed"], ["commission"])

    def test_base_only_letter(self):
        out = make(self.master, comp={"base": {"type": "salary", "amount": 70000}, "commission": None, "overtime": None})
        blob = "\n".join(text_of(out["docx"]))
        self.assertIn("$70,000/year", blob)
        self.assertNotIn("Overtime:", blob)
        self.assertNotIn("Commission:", blob)
        self.assertEqual(sorted(out["report"]["removed"]), ["commission", "overtime"])

    def test_remote_removes_the_office_phrase(self):
        out = make(self.master, raw={"work_arrangement": "remote", "office_location": ""})
        blob = "\n".join(text_of(out["docx"]))
        self.assertIn("as an Account Coordinator. Your employment", blob)
        self.assertNotIn("at our", blob)
        self.assertTrue(any("Remote role" in n for n in out["report"]["notes"]))

    def test_pay_frequency_conflict_is_refused(self):
        with self.assertRaises(ol.ValidationFailed) as ctx:
            make(self.master, comp={**COMP_FULL, "pay_frequency_mentioned": "biweekly"})
        self.assertIn("biweekly", str(ctx.exception))

    def test_matching_pay_frequency_is_fine(self):
        make(self.master, comp={**COMP_FULL, "pay_frequency_mentioned": "weekly"})

    def test_generation_is_deterministic(self):
        self.assertEqual(make(self.master)["docx"], self.out["docx"])


class TemplateProblems(unittest.TestCase):
    def test_not_a_zip(self):
        with self.assertRaises(ol.TemplateShapeError):
            ol.inspect_template(b"this is not a docx")

    def test_missing_document_part(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("hello.txt", "x")
        with self.assertRaises(ol.TemplateShapeError):
            ol.inspect_template(buf.getvalue())

    def test_dtd_is_refused(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("word/document.xml", '<!DOCTYPE x [<!ENTITY a "b">]><w:document/>')
        with self.assertRaises(ol.TemplateShapeError):
            ol.inspect_template(buf.getvalue())

    def test_template_without_slots_is_unusable_and_refused(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("word/document.xml", f'<w:document {NS}><w:body>{para(run("Hello there"))}</w:body></w:document>')
        docx = buf.getvalue()
        info = ol.inspect_template(docx)
        self.assertFalse(info["usable"])
        with self.assertRaises(ol.TemplateShapeError) as ctx:
            make(docx)
        self.assertIn("no place for", str(ctx.exception))

    def test_commission_given_but_template_has_none(self):
        master = build_docx()
        # Strip the commission paragraph and its table from the fixture.
        with zipfile.ZipFile(io.BytesIO(master)) as z:
            xml = z.read("word/document.xml").decode()
        a = xml.index("<w:p><w:pPr><w:numPr/></w:pPr><w:r><w:rPr><w:b/></w:rPr><w:t xml:space=\"preserve\">Commission:")
        b = xml.index("</w:tbl>") + len("</w:tbl>")
        stripped = xml[:a] + xml[b:]
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("word/document.xml", stripped)
        with self.assertRaises(ol.TemplateShapeError) as ctx:
            make(buf.getvalue())
        self.assertIn("no Commission section", str(ctx.exception))


class IntegrityGuard(unittest.TestCase):
    """The safety net must not trust the code it is guarding."""

    def test_a_wrongly_planned_edit_to_static_text_is_refused(self):
        from unittest import mock
        original = ol._plan_document

        def tampering(xml, ctx, counts, notes, removed):
            plan = original(xml, ctx, counts, notes, removed)
            at = xml.find(b"Health: eligible for the health package.")
            plan.append(ol._Replacement(at, at + 6, b"Wealth:"))
            return plan

        with mock.patch.object(ol, "_plan_document", tampering):
            with self.assertRaises(ol.IntegrityFailure):
                make(build_docx())

    def test_malformed_xml_output_is_refused(self):
        from unittest import mock
        original = ol._apply_plan
        with mock.patch.object(ol, "_apply_plan", lambda xml, plan: original(xml, plan) + b"<w:oops"):
            with self.assertRaises(ol.IntegrityFailure):
                make(build_docx())


class LogoReplacement(unittest.TestCase):
    def setUp(self):
        self.master = build_docx(logo=True)

    @staticmethod
    def open(out):
        return zipfile.ZipFile(io.BytesIO(out["docx"]))

    def test_inspect_reports_the_logo_slot(self):
        info = ol.inspect_template(self.master)
        self.assertEqual(info["logo"], {"found": True, "part": "word/media/image1.png", "box_emu": list(BOX)})
        self.assertFalse(ol.inspect_template(build_docx())["logo"]["found"])

    def test_png_over_png_keeps_names_and_fits_the_box(self):
        new = png(600, 200)
        z = self.open(make(self.master, logo=new, logo_name="Acme Co"))
        self.assertEqual(z.read("word/media/image1.png"), new)
        header = z.read("word/header1.xml").decode()
        self.assertEqual(header.count('cx="1857375" cy="619125"'), 2)       # wp:extent and a:ext
        self.assertIn('descr="Acme Co"', header)
        self.assertNotIn("Old Co", header)
        self.assertEqual(z.namelist(), zipfile.ZipFile(io.BytesIO(self.master)).namelist())

    def test_a_tall_logo_is_fitted_not_stretched(self):
        z = self.open(make(self.master, logo=png(100, 500)))
        self.assertIn('cx="156210" cy="781050"', z.read("word/header1.xml").decode())

    def test_jpeg_renames_the_part_and_every_reference(self):
        new = jpeg(400, 200)
        out = make(self.master, logo=new)
        z = self.open(out)
        names = z.namelist()
        self.assertIn("word/media/image1.jpeg", names)
        self.assertNotIn("word/media/image1.png", names)
        self.assertEqual(z.read("word/media/image1.jpeg"), new)
        self.assertEqual(names.index("word/media/image1.jpeg"),
                         zipfile.ZipFile(io.BytesIO(self.master)).namelist().index("word/media/image1.png"))
        self.assertIn('Target="media/image1.jpeg"', z.read("word/_rels/header1.xml.rels").decode())
        self.assertIn('Target="media/image1.jpeg"', z.read("word/_rels/footer1.xml.rels").decode())
        self.assertIn('Extension="jpeg"', z.read("[Content_Types].xml").decode())
        self.assertIn('r:embed="rId1"', z.read("word/header1.xml").decode())
        self.assertEqual(letter_logo.dangling_relationships(names, z.read), set())
        self.assertEqual(out["report"]["logo"]["format"], "image/jpeg")

    def test_gif_gets_its_own_content_type(self):
        z = self.open(make(self.master, logo=gif(500, 100)))
        self.assertIn("word/media/image1.gif", z.namelist())
        self.assertIn('Extension="gif"', z.read("[Content_Types].xml").decode())
        self.assertIn('cx="1857375" cy="371475"', z.read("word/header1.xml").decode())

    def test_nothing_unrelated_changes(self):
        before = zipfile.ZipFile(io.BytesIO(self.master))
        z = self.open(make(self.master, logo=jpeg(400, 200)))
        for name in ("word/_rels/document.xml.rels", "word/media/stuff.png"):
            self.assertEqual(before.read(name), z.read(name), name)

    def test_alt_text_cannot_break_the_xml(self):
        import xml.etree.ElementTree as ET
        z = self.open(make(self.master, logo=png(600, 200), logo_name='A" onload="x <b>'))
        header = z.read("word/header1.xml")
        ET.fromstring(header)
        self.assertIn(b"A&quot; onload=&quot;x &lt;b&gt;", header)

    def test_low_resolution_is_flagged(self):
        out = make(self.master, logo=png(120, 50))
        self.assertTrue(any("blurry" in n for n in out["report"]["notes"]))

    def test_deterministic(self):
        new = png(600, 200)
        self.assertEqual(make(self.master, logo=new)["docx"], make(self.master, logo=new)["docx"])

    def test_bad_images_are_refused_with_the_reason(self):
        cases = {
            "webp": (b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 20, "WebP"),
            "garbage": (b"hello, I am not an image", "not a PNG"),
            "empty": (b"", "empty"),
            "truncated png": (png(100, 100)[:-20], "truncated"),
            "truncated gif": (gif(10, 10)[:-1], "truncated"),
            "truncated jpeg": (jpeg(10, 10)[:-2], "truncated"),
            "huge dimensions": (png(20000, 10, full=False), "limit"),
            "decompression bomb": (png(9000, 9000, full=False), "limit"),
            "over 4 MB": (png(10, 10) + b"\x00" * (5 * 1024 * 1024), "4 MB"),
            "svg": (b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>", "not a PNG"),
        }
        for label, (data, needle) in cases.items():
            with self.subTest(label):
                with self.assertRaises(ol.ValidationFailed) as ctx:
                    make(self.master, logo=data)
                self.assertIn(needle, str(ctx.exception))

    def test_a_template_without_a_logo_says_so(self):
        with self.assertRaises(ol.TemplateShapeError) as ctx:
            make(build_docx(), logo=png(600, 200))
        self.assertIn("no logo", str(ctx.exception))

    def test_no_logo_means_the_original_is_untouched(self):
        z = self.open(make(self.master))
        self.assertEqual(z.read("word/media/image1.png"), zipfile.ZipFile(io.BytesIO(self.master)).read("word/media/image1.png"))
        self.assertIsNone(make(self.master)["report"]["logo"])

    def test_a_rename_that_leaves_a_dangling_relationship_is_refused(self):
        from unittest import mock
        original = letter_logo.replace_logo

        def forgetful(names, read, logo, alt=""):
            change = original(names, read, logo, alt)
            change.edited.pop("word/_rels/footer1.xml.rels", None)
            return change

        with mock.patch.object(letter_logo, "replace_logo", forgetful):
            with self.assertRaises(ol.IntegrityFailure) as ctx:
                make(self.master, logo=jpeg(400, 200))
        self.assertIn("relationship", str(ctx.exception))

    def test_a_missing_content_type_is_refused(self):
        from unittest import mock
        original = letter_logo.replace_logo

        def forgetful(names, read, logo, alt=""):
            change = original(names, read, logo, alt)
            change.edited.pop("[Content_Types].xml", None)
            return change

        with mock.patch.object(letter_logo, "replace_logo", forgetful):
            with self.assertRaises(ol.IntegrityFailure) as ctx:
                make(self.master, logo=gif(500, 100))
        self.assertIn("content type", str(ctx.exception))


class RealMaster(unittest.TestCase):
    path = pathlib.Path(__file__).resolve().parents[2] / "samples" / "NSG_Offer_Letter.docx"

    @unittest.skipUnless(path.exists(), "samples/NSG_Offer_Letter.docx not present")
    def test_real_master_round_trip(self):
        master = self.path.read_bytes()
        info = ol.inspect_template(master)
        self.assertTrue(info["usable"], info)
        out = make(master)
        blob = "\n".join(text_of(out["docx"]))
        for label in ol.LABELS.values():
            self.assertNotIn(label, blob, label)
        self.assertIn("Alex Morgan,\n42 Example Lane, Suite 5, Riverton,\nCA 90001\n401-555-0123\nalex.morgan@example.com", blob)
        self.assertIn("report directly to Jordan Lee.", blob)
        self.assertIn("On behalf of Sample Staffing LLC (the", blob)
        # Every static section is still there, word for word.
        original = "\n".join(text_of(master))
        for heading in ("Benefits:", "Lack of Restrictive Covenants", "Termination", "Onboarding Requirements:"):
            self.assertIn(heading, original)
            self.assertIn(heading, blob)
        # Swapping the logo on the real file keeps it a valid, fully-linked package.
        swapped = make(master, logo=jpeg(800, 300), logo_name="Other Co")
        z = zipfile.ZipFile(io.BytesIO(swapped["docx"]))
        self.assertIn("word/media/image1.jpeg", z.namelist())
        self.assertEqual(letter_logo.dangling_relationships(z.namelist(), z.read), set())
        self.assertEqual(swapped["report"]["logo"]["fitted_emu"], [1857375, 696516])
        start = original.index("Benefits:")
        end = original.index("Once again, I am very excited")
        self.assertIn(original[start:end], blob)


if __name__ == "__main__":
    unittest.main()

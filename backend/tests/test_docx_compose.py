"""The AI studio's compose path, and its logo handling.

Logo substitution has ONE implementation (`letter_logo`), shared with the offer
generator. These tests pin what the AI studio relies on from `compose`.
"""

from __future__ import annotations

import io
import unittest
import zipfile

from app import docx_compose, letter_logo
from tests.test_offer_letter import BOX, build_docx, gif, jpeg, png

BLOCKS = [{"type": "heading", "text": "Offer"}, {"type": "paragraph", "text": "Dear **{{NAME}}**,"}]


def open_zip(data: bytes) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(data))


class ComposeBody(unittest.TestCase):
    def setUp(self):
        self.donor = build_docx(logo=True)

    def test_body_is_replaced_and_stationery_kept(self):
        out = docx_compose.compose(self.donor, BLOCKS)
        z = open_zip(out["docx"])
        document = z.read("word/document.xml").decode()
        self.assertIn("Offer", document)
        self.assertNotIn("Private and Confidential", document)
        for name in ("word/header1.xml", "word/media/image1.png"):
            self.assertEqual(z.read(name), open_zip(self.donor).read(name), name)
        self.assertFalse(out["logo_replaced"])
        self.assertEqual(out["logo_part"], "word/media/image1.png")

    def test_a_donor_without_a_logo_keeps_working_when_a_logo_is_chosen(self):
        out = docx_compose.compose(build_docx(), BLOCKS, logo=png(600, 200))
        self.assertFalse(out["logo_replaced"])
        self.assertIsNone(out["logo_part"])

    def test_donor_logo_parts_agrees_with_the_shared_finder(self):
        self.assertEqual(docx_compose.donor_logo_parts(self.donor), ["word/media/image1.png"])
        self.assertEqual(docx_compose.donor_logo_parts(build_docx()), [])


class ComposeLogo(unittest.TestCase):
    def setUp(self):
        self.donor = build_docx(logo=True)

    def test_same_format_keeps_the_part_and_fits_the_box(self):
        new = png(600, 200)
        out = docx_compose.compose(self.donor, BLOCKS, logo=new, logo_name="Acme")
        z = open_zip(out["docx"])
        self.assertTrue(out["logo_replaced"])
        self.assertEqual(out["logo_part"], "word/media/image1.png")
        self.assertEqual(z.read("word/media/image1.png"), new)
        header = z.read("word/header1.xml").decode()
        self.assertEqual(header.count('cx="1857375" cy="619125"'), 2)
        self.assertIn('descr="Acme"', header)

    def test_a_jpeg_is_no_longer_stored_under_a_png_name(self):
        out = docx_compose.compose(self.donor, BLOCKS, logo=jpeg(400, 200))
        z = open_zip(out["docx"])
        self.assertEqual(out["logo_part"], "word/media/image1.jpeg")
        self.assertIn("word/media/image1.jpeg", z.namelist())
        self.assertNotIn("word/media/image1.png", z.namelist())
        self.assertIn('Target="media/image1.jpeg"', z.read("word/_rels/header1.xml.rels").decode())
        self.assertIn('Extension="jpeg"', z.read("[Content_Types].xml").decode())
        self.assertEqual(letter_logo.dangling_relationships(z.namelist(), z.read), set())

    def test_bad_images_raise_the_error_the_endpoints_already_handle(self):
        for label, data in (("webp", b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 20),
                            ("garbage", b"not an image"), ("truncated", png(50, 50)[:-20])):
            with self.subTest(label):
                with self.assertRaises(docx_compose.ComposeError):
                    docx_compose.compose(self.donor, BLOCKS, logo=data)

    def test_a_footer_logo_is_still_found(self):
        donor = build_docx(logo=True)
        z = open_zip(donor)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as out:
            for name in z.namelist():
                data = z.read(name)
                if name == "word/header1.xml":
                    name = "word/footer1.xml"
                elif name == "word/_rels/header1.xml.rels":
                    name = "word/_rels/footer1.xml.rels"
                elif name == "word/_rels/footer1.xml.rels":
                    continue
                out.writestr(name, data)
        footer_only = buf.getvalue()
        self.assertEqual(docx_compose.donor_logo_parts(footer_only), ["word/media/image1.png"])
        self.assertTrue(docx_compose.compose(footer_only, BLOCKS, logo=gif(500, 100))["logo_replaced"])


if __name__ == "__main__":
    unittest.main()

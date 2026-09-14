"""Scanned invoices (photos / image-only PDFs) are read with local OCR, no AI service."""
from decimal import Decimal

import pytest

from conftest import FIXTURES
from pms_to_odoo.invoices import extract_with_rules
from pms_to_odoo.invoices.ocr import has_text_layer, ocr_available

INV_TXT = FIXTURES / "invoices" / "acme_linen_INV-1001.txt"


def render_invoice_image(path):
    """Draw the sample invoice text onto a white image, like a clean scan."""
    from PIL import Image, ImageDraw, ImageFont
    lines = INV_TXT.read_text().splitlines()
    img = Image.new("RGB", (1700, 60 + 44 * len(lines)), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 28)
    except OSError:
        font = ImageFont.load_default()
    for i, line in enumerate(lines):
        draw.text((40, 30 + 44 * i), line, fill="black", font=font)
    img.save(path)
    return img


@pytest.mark.skipif(not ocr_available(), reason="tesseract not installed")
def test_scanned_png_is_read_by_ocr(tmp_path):
    png = tmp_path / "scan.png"
    render_invoice_image(png)
    data = extract_with_rules(png)
    assert "read by OCR" in data.review_notes
    assert data.invoice_number == "INV-1001"
    assert data.invoice_date == "2026-09-10" and data.due_date == "2026-10-10"
    assert data.dec("total") == Decimal("540.00") and data.dec("tax_amount") == Decimal("40.00")
    assert "ACME LINEN" in data.vendor_name.upper()


@pytest.mark.skipif(not ocr_available(), reason="tesseract not installed")
def test_image_only_pdf_is_read_by_ocr(tmp_path):
    png = tmp_path / "scan.png"
    img = render_invoice_image(png)
    pdf = tmp_path / "scan.pdf"
    img.convert("RGB").save(pdf, "PDF", resolution=200.0)
    from pms_to_odoo.parsers.base import read_text
    assert not has_text_layer(read_text(pdf))            # truly no text layer
    data = extract_with_rules(pdf)
    assert data.invoice_number == "INV-1001" and data.dec("total") == Decimal("540.00")
    assert "read by OCR" in data.review_notes


def test_text_pdf_path_unchanged():
    data = extract_with_rules(INV_TXT)
    assert "read by OCR" not in data.review_notes and data.invoice_number == "INV-1001"

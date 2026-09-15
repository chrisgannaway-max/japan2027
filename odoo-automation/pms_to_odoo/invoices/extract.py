"""Extract structured data from a vendor invoice (PDF or image) with Claude.

Design notes
------------
* One request per invoice, schema-validated output via ``client.messages.parse`` so we
  never hand-parse free text.  The PDF is sent as a document block (Claude reads the
  layout, not just the text, so scanned invoices work too).
* Model defaults to ``claude-opus-5``; override with ``INVOICE_MODEL`` if needed.
* Every extraction records a confidence + free-text ``review_notes`` so the Odoo bill can
  be left in *draft* and flagged when the model was unsure (e.g. hand-written totals,
  missing due date).  Nothing is posted automatically.
* Amounts are returned as strings in the schema and converted to Decimal here to avoid
  float rounding in accounting data.
"""
from __future__ import annotations

import base64
import mimetypes
import os
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field


class InvoiceLine(BaseModel):
    description: str = Field(description="Line description as printed")
    quantity: str = Field(default="1", description="Quantity as printed; '1' if not shown")
    unit_price: str = Field(description="Unit price as a plain decimal string, e.g. '12.50'")
    amount: str = Field(description="Line total (before tax) as a plain decimal string")
    category_hint: str = Field(
        default="",
        description="Short expense category guess: e.g. 'linen', 'utilities-electric', 'F&B supplies', "
                    "'repairs & maintenance', 'guest supplies', 'franchise fees', 'other'")


class InvoiceData(BaseModel):
    vendor_name: str
    vendor_tax_id: Optional[str] = Field(default=None, description="EIN/VAT/tax id if printed")
    vendor_email: Optional[str] = None
    vendor_address: Optional[str] = None
    invoice_number: str
    invoice_date: str = Field(description="ISO date YYYY-MM-DD")
    due_date: Optional[str] = Field(default=None, description="ISO date YYYY-MM-DD if printed or derivable from terms")
    payment_terms: Optional[str] = None
    currency: str = Field(default="USD")
    subtotal: str = Field(description="Sum of lines before tax, plain decimal string")
    tax_amount: str = Field(default="0", description="Total tax, plain decimal string")
    total: str = Field(description="Amount due, plain decimal string")
    bill_to_property: Optional[str] = Field(
        default=None, description="Hotel / property name the invoice is addressed to, if printed")
    purchase_order: Optional[str] = None
    lines: list[InvoiceLine]
    confidence: Literal["high", "medium", "low"] = Field(
        description="high = every field clearly printed; low = anything guessed or illegible")
    review_notes: str = Field(default="", description="Anything an accountant should double-check")

    # ---- typed helpers ---------------------------------------------------------
    def dec(self, field: str) -> Decimal:
        try:
            return Decimal(str(getattr(self, field)).replace(",", "").replace("$", ""))
        except (InvalidOperation, AttributeError):
            return Decimal("0")

    def lines_total(self) -> Decimal:
        total = Decimal("0")
        for l in self.lines:
            try:
                total += Decimal(l.amount.replace(",", "").replace("$", ""))
            except InvalidOperation:
                pass
        return total

    def arithmetic_ok(self, tolerance: Decimal = Decimal("0.02")) -> bool:
        return abs(self.lines_total() + self.dec("tax_amount") - self.dec("total")) <= tolerance


SYSTEM_PROMPT = """You extract accounting data from vendor invoices for a hotel management company.
Return values exactly as printed (do not re-compute totals unless a value is missing, and say so in
review_notes). Dates must be ISO YYYY-MM-DD. Amounts are plain decimal strings without currency
symbols or thousands separators. If the document is not an invoice (e.g. a statement, quote or
receipt), still fill the fields as best you can and explain in review_notes with confidence=low."""


def _document_block(path: Path) -> dict:
    data = base64.standard_b64encode(path.read_bytes()).decode("ascii")
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if mime == "application/pdf":
        return {"type": "document", "source": {"type": "base64", "media_type": mime, "data": data}}
    if mime in ("image/png", "image/jpeg", "image/gif", "image/webp"):
        return {"type": "image", "source": {"type": "base64", "media_type": mime, "data": data}}
    raise ValueError(f"Unsupported invoice file type {mime} for {path.name}; use PDF, PNG or JPEG")


def extract_invoice(path: str | Path, client: Optional["anthropic.Anthropic"] = None,
                    model: Optional[str] = None) -> InvoiceData:
    """Run one invoice through Claude and return validated InvoiceData.

    The anthropic package is imported here rather than at module level: the rules reader,
    and therefore the whole portal, must work without it installed.
    """
    import anthropic

    p = Path(path)
    client = client or anthropic.Anthropic()  # ANTHROPIC_API_KEY from the environment
    model = model or os.environ.get("INVOICE_MODEL", "claude-opus-5")
    response = client.messages.parse(
        model=model,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        system=SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": [
                _document_block(p),
                {"type": "text", "text": f"Extract this invoice (file name: {p.name})."},
            ],
        }],
        output_format=InvoiceData,
    )
    if response.stop_reason == "refusal":
        details = getattr(response, "stop_details", None)
        raise RuntimeError(f"Model declined to process {p.name}: "
                           f"{getattr(details, 'explanation', 'no explanation')}")
    data = response.parsed_output
    if data is None:
        raise RuntimeError(f"No structured output returned for {p.name} (stop_reason={response.stop_reason})")
    if not data.arithmetic_ok():
        data.confidence = "low" if data.confidence == "low" else "medium"
        data.review_notes = (data.review_notes + " | " if data.review_notes else "") + \
            f"Lines ({data.lines_total()}) + tax ({data.dec('tax_amount')}) != total ({data.dec('total')})"
    return data

"""Local OCR for scanned invoices (Tesseract via pytesseract). No external service.

* image files (PNG/JPG/...) -> Tesseract directly
* PDFs without a usable text layer -> each page rendered at 300 dpi -> Tesseract

`ocr_available()` is False when the tesseract binary is missing; callers then say so in the
review notes instead of failing.  Install with `apt-get install tesseract-ocr` (done in the
Dockerfile).
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".tif", ".tiff", ".bmp")


def ocr_available() -> bool:
    if shutil.which("tesseract") is None:
        return False
    try:
        import pytesseract  # noqa: F401
        from PIL import Image  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


def _ocr_image(img) -> str:
    import pytesseract
    from PIL import ImageOps
    g = ImageOps.grayscale(img)
    if g.width < 1500:                          # upscale small photos; Tesseract likes ~300 dpi
        g = g.resize((g.width * 2, g.height * 2))
    return pytesseract.image_to_string(g, config="--psm 6")


def ocr_text(path: str | Path, max_pages: int = 5) -> str:
    """Text recovered from an image or scanned PDF ('' if OCR is unavailable)."""
    if not ocr_available():
        return ""
    p = Path(path)
    from PIL import Image
    if p.suffix.lower() in IMAGE_SUFFIXES:
        with Image.open(p) as img:
            return _ocr_image(img.convert("RGB"))
    if p.suffix.lower() == ".pdf":
        import pdfplumber
        out = []
        with pdfplumber.open(str(p)) as pdf:
            for page in pdf.pages[:max_pages]:
                out.append(_ocr_image(page.to_image(resolution=300).original))
        return "\n".join(out)
    return ""


def has_text_layer(text: Optional[str], minimum_alnum: int = 40) -> bool:
    return sum(ch.isalnum() for ch in (text or "")) >= minimum_alnum

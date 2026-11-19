import base64
import math
from typing import Optional

import fitz  # PyMuPDF

# Work limits (security review 2026-11-18). A PDF is attacker-controlled
# input: page count and page dimensions are whatever the file says, and both
# OCR and rendering rasterise at a size derived from them.
MAX_PAGES = 500                 # pages read per request (text only is cheap)
MAX_OCR_PAGES = 25              # pages run through Tesseract per request
MAX_RENDER_PAGES = 50           # pages rendered to JPEG per request
MAX_PAGE_PIXELS = 20_000_000    # raster size cap per page (about 4500 x 4500)
MIN_USEFUL_DPI = 36             # below this a raster is not worth producing
OCR_DPI = 300
RENDER_TARGET_PX = 1568         # long edge wanted for vision rendering
RENDER_MAX_DPI = 150

VALID_OCR_MODES = ("off", "auto", "force")


class InvalidPDF(ValueError):
    """The upload is not a PDF this service can read."""


def capped_dpi(width_pts: float, height_pts: float, wanted_dpi: float) -> Optional[int]:
    """The DPI to rasterise a page at, never exceeding MAX_PAGE_PIXELS.

    Returns None when the page is degenerate or so large that staying under
    the cap would need a resolution too low to be useful.
    """
    if not (width_pts > 0 and height_pts > 0) or not math.isfinite(width_pts * height_pts):
        return None
    area_sq_in = (width_pts / 72.0) * (height_pts / 72.0)
    limit = math.sqrt(MAX_PAGE_PIXELS / area_sq_in)
    dpi = int(min(wanted_dpi, limit))
    return dpi if dpi >= MIN_USEFUL_DPI else None


def extract_text_from_pdf(
    pdf_bytes: bytes,
    max_pages: Optional[int] = None,
    test_mode: bool = False,
    ocr: str = "off",
    render_images: bool = False
) -> dict:
    """
    Extract text from a PDF file.

    Args:
        pdf_bytes: The PDF file as bytes
        max_pages: Maximum number of pages to extract (None = up to MAX_PAGES)
        test_mode: If True, adds a notice about limited extraction
        ocr: OCR mode - "off" (text only), "auto" (fallback), "force" (always OCR)

    Returns:
        Dictionary with extracted text and metadata

    Raises:
        InvalidPDF: the bytes are not a readable, unencrypted PDF
    """
    if ocr not in VALID_OCR_MODES:
        raise ValueError(f"ocr must be one of {VALID_OCR_MODES}")
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise InvalidPDF("File is not a readable PDF") from exc

    try:
        if doc.needs_pass:
            raise InvalidPDF("Password-protected PDFs are not supported")

        total_pages = len(doc)
        page_cap = MAX_PAGES if max_pages is None else min(max_pages, MAX_PAGES)
        pages_to_extract = min(page_cap, total_pages)

        pages = []
        full_text = []
        warnings = []
        ocr_pages = 0
        rendered_pages = 0

        def note(message: str) -> None:
            if message not in warnings:
                warnings.append(message)

        for page_num in range(pages_to_extract):
            page = doc[page_num]
            rect = page.rect
            used_ocr = False

            text = "" if ocr == "force" else page.get_text()
            wants_ocr = ocr == "force" or (ocr == "auto" and len(text.strip()) < 20)
            if wants_ocr:
                dpi = capped_dpi(rect.width, rect.height, OCR_DPI)
                if ocr_pages >= MAX_OCR_PAGES:
                    note(f"OCR limited to the first {MAX_OCR_PAGES} pages that needed it.")
                elif dpi is None:
                    note("Some pages were too large to OCR.")
                else:
                    tp = page.get_textpage_ocr(flags=0, language="eng", dpi=dpi)
                    text = page.get_text("text", textpage=tp)
                    used_ocr = True
                    ocr_pages += 1
                if not used_ocr and ocr == "force":
                    text = page.get_text()

            page_dict = {
                "page": page_num + 1,
                "text": text,
                "ocr": used_ocr
            }

            if render_images:
                page_max_pts = max(rect.width, rect.height)
                wanted = min(RENDER_TARGET_PX / page_max_pts * 72, RENDER_MAX_DPI) if page_max_pts > 0 else 0
                dpi = capped_dpi(rect.width, rect.height, max(wanted, MIN_USEFUL_DPI))
                if rendered_pages >= MAX_RENDER_PAGES:
                    note(f"Images limited to the first {MAX_RENDER_PAGES} pages.")
                elif dpi is None:
                    note("Some pages were too large to render.")
                else:
                    pix = page.get_pixmap(dpi=dpi)
                    img_bytes = pix.tobytes(output="jpeg", jpg_quality=80)
                    page_dict["image_base64"] = base64.b64encode(img_bytes).decode("ascii")
                    page_dict["image_media_type"] = "image/jpeg"
                    rendered_pages += 1

            pages.append(page_dict)
            full_text.append(text)
    finally:
        doc.close()

    result = {
        "total_pages": total_pages,
        "extracted_pages": pages_to_extract,
        "pages": pages,
        "full_text": "\n\n".join(full_text)
    }

    if test_mode and total_pages > pages_to_extract:
        result["notice"] = f"Test mode: Only first {pages_to_extract} of {total_pages} pages extracted. Use paid endpoint for full extraction."
    elif total_pages > pages_to_extract:
        note(f"Only the first {pages_to_extract} of {total_pages} pages were extracted.")
    if warnings:
        result["warnings"] = warnings

    return result

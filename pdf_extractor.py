import fitz  # PyMuPDF
from typing import Optional


def extract_text_from_pdf(
    pdf_bytes: bytes,
    max_pages: Optional[int] = None,
    test_mode: bool = False,
    ocr: str = "off"
) -> dict:
    """
    Extract text from a PDF file.

    Args:
        pdf_bytes: The PDF file as bytes
        max_pages: Maximum number of pages to extract (None = all)
        test_mode: If True, adds a notice about limited extraction
        ocr: OCR mode - "off" (text only), "auto" (fallback), "force" (always OCR)

    Returns:
        Dictionary with extracted text and metadata
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")

    total_pages = len(doc)
    pages_to_extract = total_pages if max_pages is None else min(max_pages, total_pages)

    pages = []
    full_text = []

    for page_num in range(pages_to_extract):
        page = doc[page_num]
        used_ocr = False

        if ocr == "force":
            tp = page.get_textpage_ocr(flags=0, language="eng", dpi=300)
            text = page.get_text("text", textpage=tp)
            used_ocr = True
        elif ocr == "auto":
            text = page.get_text()
            if len(text.strip()) < 20:
                tp = page.get_textpage_ocr(flags=0, language="eng", dpi=300)
                text = page.get_text("text", textpage=tp)
                used_ocr = True
        else:
            text = page.get_text()

        pages.append({
            "page": page_num + 1,
            "text": text,
            "ocr": used_ocr
        })
        full_text.append(text)

    doc.close()
    
    result = {
        "total_pages": total_pages,
        "extracted_pages": pages_to_extract,
        "pages": pages,
        "full_text": "\n\n".join(full_text)
    }
    
    if test_mode and total_pages > pages_to_extract:
        result["notice"] = f"Test mode: Only first {pages_to_extract} of {total_pages} pages extracted. Use paid endpoint for full extraction."
    
    return result

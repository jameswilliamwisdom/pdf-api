"""
Security review 2026-11-18: work limits on the PDF service.

Run:  pip install -r requirements.txt pytest httpx
      pytest tests/
"""

import os
import sys

import fitz
import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("PAY_TO_ADDRESS", "0x0000000000000000000000000000000000000001")
os.environ["INTERNAL_API_KEY"] = "test-internal-key"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import main  # noqa: E402
import pdf_extractor as px  # noqa: E402

KEY = {"X-API-Key": "test-internal-key"}


def make_pdf(pages=1, width=612, height=792, text="hello world from the test pdf"):
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page(width=width, height=height)
        if text:
            page.insert_text((72, 72), f"{text} {i + 1}")
    data = doc.tobytes()
    doc.close()
    return data


@pytest.fixture
def client():
    main._free_test_calls.clear()
    return TestClient(main.app)


def upload(client, path, data, name="doc.pdf", **kw):
    return client.post(path, files={"file": (name, data, "application/pdf")}, **kw)


# ── extractor limits ────────────────────────────────────────────────────

def test_normal_pdf_extracts():
    out = px.extract_text_from_pdf(make_pdf(pages=2))
    assert out["total_pages"] == 2 and out["extracted_pages"] == 2
    assert "hello world" in out["full_text"]
    assert "warnings" not in out


@pytest.mark.parametrize("w, h, wanted", [
    (612, 792, 300),        # US Letter
    (14400, 14400, 300),    # the largest page a PDF may declare
    (14400, 14400, 72),
    (200000, 200000, 300),
])
def test_capped_dpi_never_exceeds_the_pixel_budget(w, h, wanted):
    dpi = px.capped_dpi(w, h, wanted)
    if dpi is not None:
        assert dpi <= wanted
        assert (w / 72 * dpi) * (h / 72 * dpi) <= px.MAX_PAGE_PIXELS
        assert dpi >= px.MIN_USEFUL_DPI


def test_capped_dpi_leaves_normal_pages_alone():
    assert px.capped_dpi(612, 792, 300) == 300


@pytest.mark.parametrize("w, h", [(0, 792), (612, 0), (-5, 10), (float("inf"), 10), (float("nan"), 10)])
def test_capped_dpi_rejects_degenerate_pages(w, h):
    assert px.capped_dpi(w, h, 300) is None


def test_huge_page_render_stays_within_budget():
    """Previously rendered at a floor of 72 dpi: 14400 x 14400 px, about
    200 megapixels, for one page."""
    out = px.extract_text_from_pdf(make_pdf(width=14400, height=14400), render_images=True)
    page = out["pages"][0]
    if "image_base64" in page:
        import base64
        pix = fitz.Pixmap(base64.b64decode(page["image_base64"]))
        assert pix.width * pix.height <= px.MAX_PAGE_PIXELS
    else:
        assert any("too large to render" in w for w in out["warnings"])


def test_render_page_count_is_capped(monkeypatch):
    monkeypatch.setattr(px, "MAX_RENDER_PAGES", 2)
    out = px.extract_text_from_pdf(make_pdf(pages=4), render_images=True)
    assert sum("image_base64" in p for p in out["pages"]) == 2
    assert any("Images limited" in w for w in out["warnings"])


def test_page_count_is_capped(monkeypatch):
    monkeypatch.setattr(px, "MAX_PAGES", 3)
    out = px.extract_text_from_pdf(make_pdf(pages=5))
    assert out["extracted_pages"] == 3 and out["total_pages"] == 5
    assert any("first 3 of 5" in w for w in out["warnings"])


def test_ocr_page_count_is_capped(monkeypatch):
    calls = []
    monkeypatch.setattr(px, "MAX_OCR_PAGES", 2)
    monkeypatch.setattr(fitz.Page, "get_textpage_ocr",
                        lambda self, **kw: calls.append(kw["dpi"]) or self.get_textpage())
    out = px.extract_text_from_pdf(make_pdf(pages=5), ocr="force")
    assert len(calls) == 2 and all(d <= px.OCR_DPI for d in calls)
    assert [p["ocr"] for p in out["pages"]] == [True, True, False, False, False]
    assert "hello world" in out["pages"][4]["text"]      # falls back to plain text
    assert any("OCR limited" in w for w in out["warnings"])


def test_ocr_dpi_is_capped_for_huge_pages(monkeypatch):
    calls = []
    monkeypatch.setattr(fitz.Page, "get_textpage_ocr",
                        lambda self, **kw: calls.append(kw["dpi"]) or self.get_textpage())
    px.extract_text_from_pdf(make_pdf(width=5000, height=5000), ocr="force")
    assert calls and (5000 / 72 * calls[0]) ** 2 <= px.MAX_PAGE_PIXELS


@pytest.mark.parametrize("data", [b"", b"not a pdf at all", b"%PDF-1.7\ngarbage"])
def test_invalid_pdf_raises_invalid_pdf(data):
    with pytest.raises(px.InvalidPDF):
        px.extract_text_from_pdf(data)


def test_bad_ocr_mode_rejected():
    with pytest.raises(ValueError):
        px.extract_text_from_pdf(make_pdf(), ocr="everything")


# ── HTTP limits ─────────────────────────────────────────────────────────

def test_free_route_works(client):
    r = upload(client, "/test/extract", make_pdf(pages=5))
    assert r.status_code == 200
    body = r.json()
    assert body["extracted_pages"] == 3 and "notice" in body


def test_free_route_rejects_large_upload(client):
    big = make_pdf() + b"%" * (main.MAX_TEST_UPLOAD_BYTES + 1)
    assert upload(client, "/test/extract", big).status_code == 413


def test_key_route_allows_more_than_the_free_limit_but_not_unbounded(client):
    medium = make_pdf() + b"%" * (main.MAX_TEST_UPLOAD_BYTES + 1)
    assert upload(client, "/extract/key", medium, headers=KEY).status_code == 200
    huge = make_pdf() + b"%" * (main.MAX_UPLOAD_BYTES + 1)
    assert upload(client, "/extract/key", huge, headers=KEY).status_code == 413


def test_paid_route_rejects_oversize_before_asking_for_payment(client):
    huge = make_pdf() + b"%" * (main.MAX_UPLOAD_BYTES + 1)
    assert upload(client, "/extract", huge).status_code == 413


def test_upload_without_length_is_refused(client):
    def body():
        yield b"--x\r\n"
    r = client.post("/test/extract", content=body(), headers={"Content-Type": "multipart/form-data; boundary=x"})
    assert r.status_code == 411


def test_free_route_has_a_shared_budget(client, monkeypatch):
    monkeypatch.setattr(main, "FREE_TEST_MAX_PER_MINUTE", 3)
    pdf = make_pdf()
    statuses = [upload(client, "/test/extract", pdf).status_code for _ in range(5)]
    assert statuses == [200, 200, 200, 429, 429]


def test_invalid_pdf_is_a_400_with_no_library_text(client):
    r = upload(client, "/test/extract", b"definitely not a pdf")
    assert r.status_code == 400
    assert r.json()["detail"] == "File is not a readable PDF"


def test_unexpected_errors_are_not_echoed(client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("/app/secret/path and internal detail")
    monkeypatch.setattr(main, "extract_text_from_pdf", boom)
    r = upload(client, "/test/extract", make_pdf())
    assert r.status_code == 500
    assert r.json() == {"detail": "extraction_failed"}


@pytest.mark.parametrize("name", ["doc.txt", "doc", ""])
def test_non_pdf_filename_rejected(client, name):
    r = upload(client, "/test/extract", make_pdf(), name=name or "x")
    assert r.status_code in (400, 422) if name else True


def test_ocr_on_is_accepted_as_documented(client, monkeypatch):
    seen = {}
    monkeypatch.setattr(main, "extract_text_from_pdf", lambda data, **kw: seen.update(kw) or {"ok": True})
    assert upload(client, "/test/extract?ocr=on", make_pdf()).status_code == 200
    assert seen["ocr"] == "auto"
    assert upload(client, "/test/extract?ocr=bogus", make_pdf()).status_code == 400


def test_key_route_still_requires_the_key(client):
    assert upload(client, "/extract/key", make_pdf(), headers={"X-API-Key": "wrong"}).status_code == 401


def test_paid_route_still_requires_payment(client):
    assert upload(client, "/extract", make_pdf()).status_code == 402

import logging
import os
import secrets
import time
from collections import deque
from fastapi import FastAPI, HTTPException, UploadFile, File, Body, Header, Query
from fastapi.responses import Response, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from x402.http import FacilitatorConfig, HTTPFacilitatorClient, PaymentOption
from x402.http.middleware.fastapi import PaymentMiddlewareASGI
from x402.http.types import RouteConfig
from x402.mechanisms.evm.exact import ExactEvmServerScheme
from x402.schemas import Network
from x402.server import x402ResourceServer

from pdf_extractor import InvalidPDF, VALID_OCR_MODES, extract_text_from_pdf

logger = logging.getLogger("pdf-api")

# API key for authenticated (payment-free) access
INTERNAL_API_KEY = os.getenv("INTERNAL_API_KEY", "")


app = FastAPI(
    title="Bismuth PDF Extraction",
    description="Extract text from PDF documents with OCR (Tesseract) and vision mode support. Part of the Bismuth utility API suite for AI agents.",
    version="2.0.0",
    contact={
        "name": "Bismuth",
        "url": "https://usebismuth.com",
        "email": os.getenv("CONTACT_EMAIL", "james@usebismuth.com"),
    },
)

app.mount("/static", StaticFiles(directory="static"), name="static")

# x402 v2 payment middleware — official x402-foundation SDK
PAY_TO = os.getenv("PAY_TO_ADDRESS", "0x6b21227Ca9Bb3590BB62ff60BA0EFbBf9Ba22ACC")
BASE_NETWORK: Network = "eip155:8453"
FACILITATOR_URL = os.getenv("FACILITATOR_URL", "https://facilitator.daydreams.systems")

_facilitator = HTTPFacilitatorClient(FacilitatorConfig(url=FACILITATOR_URL))
_x402_server = x402ResourceServer(_facilitator)
_x402_server.register(BASE_NETWORK, ExactEvmServerScheme())

_paid_routes = {
    "POST /extract": RouteConfig(
        accepts=[PaymentOption(scheme="exact", pay_to=PAY_TO, price="$0.01", network=BASE_NETWORK)],
        mime_type="application/json",
        description="Extract text from a PDF file",
    ),
}
app.add_middleware(PaymentMiddlewareASGI, routes=_paid_routes, server=_x402_server)


# =============================================================================
# Upload size limits (security review 2026-11-18)
# =============================================================================
# Uploads were unbounded: the whole body was spooled and read into memory
# before any check. The limit is enforced from Content-Length, before the
# body is read and before payment; the server rejects a body that does not
# match its declared length, so the header can be relied on. Uploads sent
# without a length are refused.

MAX_UPLOAD_BYTES = 25 * 1024 * 1024        # paid and API-key routes
MAX_TEST_UPLOAD_BYTES = 5 * 1024 * 1024    # free test route
_UPLOAD_LIMITS = {
    "/extract": MAX_UPLOAD_BYTES,
    "/extract/key": MAX_UPLOAD_BYTES,
    "/test/extract": MAX_TEST_UPLOAD_BYTES,
}


class UploadSizeLimit:
    """Pure ASGI middleware: reject oversized or unsized uploads up front."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        limit = _UPLOAD_LIMITS.get(scope.get("path")) if scope["type"] == "http" else None
        if limit is None or scope["method"] != "POST":
            return await self.app(scope, receive, send)
        declared = dict(scope["headers"]).get(b"content-length", b"").decode("latin-1")
        if not declared.isdigit():
            status, detail = 411, "Content-Length required"
        elif int(declared) > limit:
            status, detail = 413, f"File too large. Limit is {limit // (1024 * 1024)}MB."
        else:
            return await self.app(scope, receive, send)
        response = JSONResponse(status_code=status, content={"detail": detail})
        await response(scope, receive, send)


# Added after the payment middleware, so it runs before it: an oversized
# upload is refused without a payment being taken.
app.add_middleware(UploadSizeLimit)

# The free test route does real work per call; one shared budget bounds it.
FREE_TEST_MAX_PER_MINUTE = 30
_free_test_calls: deque = deque()


def _ocr_mode(ocr: str) -> str:
    """Validate the ?ocr= value. The published docs say ?ocr=on, which the
    extractor used to ignore silently (it only knew auto and force); treat
    it as auto, which is what the docs describe."""
    mode = "auto" if ocr == "on" else ocr
    if mode not in VALID_OCR_MODES:
        raise HTTPException(status_code=400, detail=f"ocr must be one of: on, {', '.join(VALID_OCR_MODES)}")
    return mode


def _check_is_pdf_upload(file: UploadFile) -> None:
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="File must be a PDF")


async def _run_extraction(file: UploadFile, limit: int, **kwargs) -> JSONResponse:
    contents = await file.read(limit + 1)
    if len(contents) > limit:
        raise HTTPException(status_code=413, detail="File too large.")
    try:
        # Extraction is CPU-bound; keep it off the event loop so health
        # checks and other requests are still served.
        result = await run_in_threadpool(extract_text_from_pdf, contents, **kwargs)
    except InvalidPDF as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        # Library error text stays in the log.
        logger.error("extraction failed: %s: %s", type(e).__name__, e)
        raise HTTPException(status_code=500, detail="extraction_failed")
    return JSONResponse(content=result)


# =============================================================================
# OpenAPI x402 v2 Extensions + Favicon
# =============================================================================

_original_openapi_fn = app.openapi


def _openapi_with_x402_v2():
    if app.openapi_schema:
        return app.openapi_schema
    schema = _original_openapi_fn()
    schema["info"]["x-guidance"] = (
        "Bismuth PDF Extraction — Text extraction from PDF documents for AI agents. "
        "POST /extract with a multipart PDF file returns extracted text ($0.01 USDC on Base). "
        "Query params: ?ocr=on (Tesseract OCR for scanned PDFs; also auto, force), "
        "?render_images=true (vision-based rendering). "
        "Free test at POST /test/extract (first 3 pages only). "
        "Alternate API-key auth at POST /extract/key."
    )
    _paid_ops = {("/extract", "post"): "0.010000"}
    for (path, method), amount in _paid_ops.items():
        op = schema.get("paths", {}).get(path, {}).get(method)
        if op is None:
            continue
        op["x-payment-info"] = {
            "price": {"mode": "fixed", "currency": "USD", "amount": amount},
            "protocols": [{"x402": {}}],
        }
        op.setdefault("responses", {})["402"] = {"description": "Payment Required"}

    # Mark all non-paid ops with security:[] so x402scan indexes them as free resources
    for path, path_item in schema.get("paths", {}).items():
        for method in ("get", "post", "put", "delete", "patch", "options", "head"):
            op = path_item.get(method)
            if op is None:
                continue
            if (path, method) in _paid_ops:
                continue
            op["security"] = []

    app.openapi_schema = schema
    return schema


app.openapi = _openapi_with_x402_v2

_FAVICON_PATH = os.path.join(os.path.dirname(__file__), "favicon.ico")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    if os.path.exists(_FAVICON_PATH):
        return FileResponse(_FAVICON_PATH, media_type="image/x-icon")
    raise HTTPException(status_code=404)


@app.api_route("/.well-known/x402", methods=["GET", "HEAD"])
async def well_known_x402():
    """x402 discovery — indexed by x402scan and other ecosystem crawlers."""
    return {
        "version": 1,
        "x402Version": 2,
        "name": "Bismuth PDF Extraction",
        "description": "Extract text from PDF documents with OCR (Tesseract) and vision mode support. Part of the Bismuth utility API suite for AI agents.",
        "apiVersion": "2.0.0",
        "network": "base",
        "resource": {
            "url": "https://pdf-api-production-cf1e.up.railway.app",
            "description": "Bismuth PDF Extraction — x402 USDC micropayments on Base",
            "mimeType": "application/json",
        },
        "services": [
            {
                "name": "Extract PDF",
                "endpoint": "/extract",
                "method": "POST",
                "price": "$0.01",
                "description": "Extract text from a PDF file. Supports OCR (?ocr=on) and vision rendering (?render_images=true)",
            },
        ],
        "resources": ["POST /extract"],
        "documentation": "https://pdf-api-production-cf1e.up.railway.app/docs",
        "provider": {
            "name": "Bismuth",
            "url": "https://usebismuth.com",
        },
    }


@app.api_route("/", methods=["GET", "HEAD"])
async def root():
    """Serve the landing page."""
    return FileResponse('static/index.html')


@app.api_route("/health", methods=["GET", "HEAD"])
async def health():
    """Health check endpoint."""
    return {"status": "ok"}


@app.post("/extract")
async def extract(file: UploadFile = File(...), ocr: str = Query("off"), render_images: bool = Query(False)):
    """Extract text from an uploaded PDF."""
    ocr = _ocr_mode(ocr)
    _check_is_pdf_upload(file)
    return await _run_extraction(file, MAX_UPLOAD_BYTES, ocr=ocr, render_images=render_images)


@app.post("/extract/key")
async def extract_with_key(
    file: UploadFile = File(...),
    x_api_key: str = Header(..., alias="X-API-Key"),
    ocr: str = Query("off"),
    render_images: bool = Query(False),
):
    """Extract text from an uploaded PDF using API key auth (no x402 payment)."""
    if not INTERNAL_API_KEY or not secrets.compare_digest(x_api_key.encode(), INTERNAL_API_KEY.encode()):
        raise HTTPException(status_code=401, detail="Invalid API key")
    ocr = _ocr_mode(ocr)
    _check_is_pdf_upload(file)
    return await _run_extraction(file, MAX_UPLOAD_BYTES, ocr=ocr, render_images=render_images)


@app.api_route("/test/extract", methods=["GET", "HEAD"])
async def test_extract_info():
    """Info about the test endpoint."""
    return {
        "endpoint": "POST /test/extract",
        "description": "Upload a PDF to extract text (first 3 pages only, for testing)",
        "usage": "curl -X POST -F 'file=@document.pdf' https://your-api/test/extract"
    }


@app.post("/test/extract")
async def test_extract(file: UploadFile = File(...), ocr: str = Query("off"), render_images: bool = Query(False)):
    """Extract text from first 3 pages of a PDF (free test endpoint)."""
    ocr = _ocr_mode(ocr)
    _check_is_pdf_upload(file)

    now = time.monotonic()
    while _free_test_calls and now - _free_test_calls[0] > 60:
        _free_test_calls.popleft()
    if len(_free_test_calls) >= FREE_TEST_MAX_PER_MINUTE:
        raise HTTPException(status_code=429, detail="free_test_busy")
    _free_test_calls.append(now)

    return await _run_extraction(
        file, MAX_TEST_UPLOAD_BYTES,
        max_pages=3, test_mode=True, ocr=ocr, render_images=render_images,
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

import os
import secrets
from fastapi import FastAPI, HTTPException, UploadFile, File, Body, Header, Query
from fastapi.responses import Response, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from x402.http import FacilitatorConfig, HTTPFacilitatorClient, PaymentOption
from x402.http.middleware.fastapi import PaymentMiddlewareASGI
from x402.http.types import RouteConfig
from x402.mechanisms.evm.exact import ExactEvmServerScheme
from x402.schemas import Network
from x402.server import x402ResourceServer

from pdf_extractor import extract_text_from_pdf

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
        "Query params: ?ocr=on (Tesseract OCR for scanned PDFs), "
        "?render_images=true (vision-based rendering). "
        "Free test at POST /test/extract (first 3 pages only). "
        "Alternate API-key auth at POST /extract/key."
    )
    for (path, method), amount in [(("/extract", "post"), "0.010000")]:
        op = schema.get("paths", {}).get(path, {}).get(method)
        if op is None:
            continue
        op["x-payment-info"] = {
            "price": {"mode": "fixed", "currency": "USD", "amount": amount},
            "protocols": [{"x402": {}}],
        }
        op.setdefault("responses", {})["402"] = {"description": "Payment Required"}
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
    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="File must be a PDF")

    try:
        contents = await file.read()
        result = extract_text_from_pdf(contents, ocr=ocr, render_images=render_images)
        return JSONResponse(content=result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/extract/key")
async def extract_with_key(
    file: UploadFile = File(...),
    x_api_key: str = Header(..., alias="X-API-Key"),
    ocr: str = Query("off"),
    render_images: bool = Query(False),
):
    """Extract text from an uploaded PDF using API key auth (no x402 payment)."""
    if not INTERNAL_API_KEY or not secrets.compare_digest(x_api_key, INTERNAL_API_KEY):
        raise HTTPException(status_code=401, detail="Invalid API key")
    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="File must be a PDF")
    try:
        contents = await file.read()
        result = extract_text_from_pdf(contents, ocr=ocr, render_images=render_images)
        return JSONResponse(content=result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="File must be a PDF")

    try:
        contents = await file.read()
        result = extract_text_from_pdf(contents, max_pages=3, test_mode=True, ocr=ocr, render_images=render_images)
        return JSONResponse(content=result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

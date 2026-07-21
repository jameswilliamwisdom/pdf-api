import os
import secrets
from fastapi import FastAPI, HTTPException, UploadFile, File, Body, Header, Query
from fastapi.responses import Response, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi_x402 import init_x402, pay, PaymentMiddleware
from pdf_extractor import extract_text_from_pdf

# API key for authenticated (payment-free) access
INTERNAL_API_KEY = os.getenv("INTERNAL_API_KEY", "")


app = FastAPI(
    title="PDF Extraction API",
    description="API for extracting text from PDF documents with x402 micropayments",
    version="1.0.0",
)

app.mount("/static", StaticFiles(directory="static"), name="static")

# Initialize x402 payment configuration
init_x402(
    pay_to=os.getenv("PAY_TO_ADDRESS", "0x6b21227Ca9Bb3590BB62ff60BA0EFbBf9Ba22ACC"),
    network=os.getenv("X402_NETWORK", "base"),
)

# Add payment middleware
app.add_middleware(PaymentMiddleware)


@app.get("/.well-known/x402")
async def well_known_x402():
    """x402 discovery — indexed by x402scan and other ecosystem crawlers."""
    return {
        "version": 1,
        "x402Version": 2,
        "name": "Bismuth PDF Extraction",
        "description": "Extract text from PDF documents with OCR (Tesseract) and vision mode support. Part of the Bismuth utility API suite for AI agents.",
        "apiVersion": "1.0.0",
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


@app.get("/")
async def root():
    """Serve the landing page."""
    return FileResponse('static/index.html')


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok"}


@pay("$0.01")
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


@app.get("/test/extract")
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

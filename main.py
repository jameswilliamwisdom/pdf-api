import os
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, UploadFile, File, Body
from fastapi.responses import Response, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi_x402 import init_x402, pay, PaymentMiddleware
from pdf_extractor import extract_text_from_pdf

load_dotenv()

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
async def extract(file: UploadFile = File(...)):
    """Extract text from an uploaded PDF."""
    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="File must be a PDF")
    
    try:
        contents = await file.read()
        result = extract_text_from_pdf(contents)
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
async def test_extract(file: UploadFile = File(...)):
    """Extract text from first 3 pages of a PDF (free test endpoint)."""
    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="File must be a PDF")
    
    try:
        contents = await file.read()
        result = extract_text_from_pdf(contents, max_pages=3, test_mode=True)
        return JSONResponse(content=result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

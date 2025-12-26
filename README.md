# PDF Extraction API

Extract text from PDF documents with x402 micropayments. No API keys required.

## Endpoints

| Endpoint | Method | Price | Description |
|----------|--------|-------|-------------|
| `/extract` | POST | $0.01 | Extract text from all pages |
| `/test/extract` | POST | Free | Extract first 3 pages (testing) |
| `/health` | GET | Free | Health check |

## Usage

```bash
# Test endpoint (free, limited to 3 pages)
curl -X POST -F "file=@document.pdf" https://your-api/test/extract

# Paid endpoint (requires x402 payment)
curl -X POST -F "file=@document.pdf" https://your-api/extract
```

## Response Format

```json
{
  "total_pages": 10,
  "extracted_pages": 10,
  "pages": [
    {"page": 1, "text": "Page 1 content..."},
    {"page": 2, "text": "Page 2 content..."}
  ],
  "full_text": "All pages concatenated..."
}
```

## Payment

This API uses the [x402 protocol](https://www.x402.org/) for micropayments.

- **Network:** Base (mainnet)
- **Currency:** USDC
- **Price:** $0.01 per extraction
- **Wallet:** `0x6b21227Ca9Bb3590BB62ff60BA0EFbBf9Ba22ACC`

## Local Development

```bash
pip install -r requirements.txt
uvicorn main:app --reload
```

## Deploy to Railway

1. Push to GitHub
2. Connect repo to Railway
3. Deploy (no build command needed)

## License

MIT

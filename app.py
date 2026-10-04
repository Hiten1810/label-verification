"""
app.py - FastAPI server for the label verification prototype.

Run:  uvicorn app:app --reload
Open: http://127.0.0.1:8000
"""
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

import ocr
from checkers import overall_status, verify_label

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB
STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    ocr.configure_tesseract()
    if ocr.tesseract_available():
        ocr.warm_up()
    yield


app = FastAPI(title="Label Verification Prototype", lifespan=lifespan)


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health():
    return {"status": "ok", "ocr_ready": ocr.tesseract_available()}


@app.post("/api/verify")
def verify(
    brand_name: str = Form(""),
    class_type: str = Form(""),
    abv: str = Form(""),
    net_contents: str = Form(""),
    image: UploadFile = File(...),
):
    """Compare application fields to the uploaded label image."""
    application = {
        "brand_name": brand_name.strip(),
        "class_type": class_type.strip(),
        "abv": abv.strip(),
        "net_contents": net_contents.strip(),
    }

    labels = {"brand_name": "Brand name", "class_type": "Class/type",
              "abv": "Alcohol content", "net_contents": "Net contents"}
    missing = [labels[k] for k, v in application.items() if not v]
    if missing:
        raise HTTPException(400, "Please fill in: " + ", ".join(missing) + ".")
    if not re.search(r"\d", application["abv"]):
        raise HTTPException(400, "Alcohol content should be a number, like 45.")

    data = image.file.read(MAX_UPLOAD_BYTES + 1)
    if not data:
        raise HTTPException(400, "Please choose a label image to upload.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, "That image is larger than 10 MB. Please use a smaller file.")

    if not ocr.tesseract_available():
        raise HTTPException(503, "The text-reading engine isn't available on the server.")

    start = time.perf_counter()
    try:
        text = ocr.read_label(data)
    except ocr.UnreadableImage as e:
        raise HTTPException(400, str(e))
    results = verify_label(application, text)
    elapsed = time.perf_counter() - start

    return {
        "overall": overall_status(results).value,
        "results": [r.to_dict() for r in results],
        "ocr_text": text.strip(),
        "seconds": round(elapsed, 2),
    }
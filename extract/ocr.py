"""
IPAA — OCR Engine
Extracts raw text from uploaded medical documents.
Supports PNG, JPG, JPEG images and PDF (via pdf2image).
"""

import logging
import time
from pathlib import Path
from typing import Tuple

logger = logging.getLogger("extract")


def extract_text_from_file(file_path: str) -> Tuple[str, float]:
    """
    Master OCR dispatcher. Returns (extracted_text, confidence_score 0-1).

    Args:
        file_path: Absolute path to the uploaded file.

    Returns:
        Tuple of (raw_text, ocr_confidence).
    """
    path = Path(file_path)
    ext = path.suffix.lower()

    logger.info(f"[OCR] Processing file: {path.name} (type: {ext})")
    start = time.time()

    try:
        if ext in (".png", ".jpg", ".jpeg"):
            text, confidence = _ocr_image(file_path)
        elif ext == ".pdf":
            text, confidence = _ocr_pdf(file_path)
        else:
            logger.warning(f"[OCR] Unsupported file type: {ext}")
            return "", 0.0

        elapsed = round((time.time() - start) * 1000, 2)
        logger.info(f"[OCR] Extracted {len(text)} chars in {elapsed}ms | confidence={confidence:.2f}")
        return text.strip(), confidence

    except Exception as exc:
        logger.error(f"[OCR] Extraction failed for {path.name}: {exc}", exc_info=True)
        return "", 0.0


def _ocr_image(file_path: str) -> Tuple[str, float]:
    """Run Tesseract OCR on an image file."""
    try:
        import pytesseract
        from PIL import Image, ImageEnhance, ImageFilter

        image = Image.open(file_path)

        # Pre-processing for better accuracy
        image = image.convert("L")                          # Grayscale
        image = ImageEnhance.Contrast(image).enhance(2.0)  # Boost contrast
        image = image.filter(ImageFilter.SHARPEN)           # Sharpen edges

        # Get detailed OCR data for confidence estimation
        data = pytesseract.image_to_data(
            image,
            output_type=pytesseract.Output.DICT,
            config="--oem 3 --psm 6",
        )

        # Compute average confidence (ignore -1 values)
        confidences = [
            int(c) for c in data["conf"] if str(c).lstrip("-").isdigit() and int(c) != -1
        ]
        avg_confidence = (sum(confidences) / len(confidences) / 100) if confidences else 0.5

        text = pytesseract.image_to_string(image, config="--oem 3 --psm 6")
        return text, min(avg_confidence, 1.0)

    except ImportError:
        logger.error("[OCR] pytesseract or Pillow not installed.")
        return "", 0.0


def _ocr_pdf(file_path: str) -> Tuple[str, float]:
    """
    Extract text from PDF.
    First tries direct text extraction (pypdf); falls back to image OCR via pdf2image.
    """
    # Strategy 1: Direct text extraction (fast, high quality for digital PDFs)
    try:
        from pypdf import PdfReader
        reader = PdfReader(file_path)
        pages_text = [page.extract_text() or "" for page in reader.pages]
        full_text = "\n\n".join(pages_text).strip()

        if len(full_text) > 100:
            logger.info(f"[OCR] PDF direct extraction succeeded ({len(reader.pages)} pages)")
            return full_text, 0.92   # High confidence — native text
    except Exception as e:
        logger.warning(f"[OCR] PDF direct extraction failed: {e}")

    # Strategy 2: Rasterize and OCR (for scanned PDFs)
    try:
        from pdf2image import convert_from_path
        images = convert_from_path(file_path, dpi=200)
        all_text = []
        confidences = []

        for i, img in enumerate(images):
            text, conf = _ocr_image_from_pil(img)
            all_text.append(text)
            confidences.append(conf)
            logger.debug(f"[OCR] PDF page {i+1} extracted: conf={conf:.2f}")

        combined_text = "\n\n".join(all_text).strip()
        avg_conf = sum(confidences) / len(confidences) if confidences else 0.0
        return combined_text, avg_conf

    except ImportError:
        logger.error("[OCR] pdf2image not installed. Cannot OCR scanned PDF.")
        return "", 0.0
    except Exception as e:
        logger.error(f"[OCR] PDF rasterization OCR failed: {e}")
        return "", 0.0


def _ocr_image_from_pil(image) -> Tuple[str, float]:
    """Run Tesseract on a PIL Image object (used internally for PDF pages)."""
    try:
        import pytesseract
        from PIL import ImageEnhance, ImageFilter

        image = image.convert("L")
        image = ImageEnhance.Contrast(image).enhance(1.8)
        image = image.filter(ImageFilter.SHARPEN)

        data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
        confidences = [int(c) for c in data["conf"] if str(c).lstrip("-").isdigit() and int(c) != -1]
        conf = (sum(confidences) / len(confidences) / 100) if confidences else 0.5

        text = pytesseract.image_to_string(image, config="--oem 3 --psm 6")
        return text, min(conf, 1.0)
    except Exception as e:
        logger.error(f"[OCR] PIL image OCR error: {e}")
        return "", 0.0

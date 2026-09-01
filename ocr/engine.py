"""OCR engine abstraction. Backend selection (tesseract/trocr/textract) is
driven by config.Settings.ocr_engine. Implementation lands in a later stage.
"""

from uuid import UUID


def run_ocr(document_id: UUID, image_path: str) -> str:
    """Run OCR on a preprocessed image and return extracted text.

    Stub: not yet implemented.
    """
    raise NotImplementedError

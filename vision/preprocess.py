from __future__ import annotations

from io import BytesIO
from pathlib import Path

import cv2
import numpy as np
import pymupdf as fitz
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_RECEIPT_BYTES = 20 * 1024 * 1024
MAX_PDF_PAGES = 10
MAX_IMAGE_DIMENSION = 2400
MAX_IMAGE_PIXELS = 50_000_000
_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
_MIN_RECEIPT_AREA_RATIO = 0.05


class ImagePreprocessingError(ValueError):
    """Raised when a receipt image cannot be safely prepared."""


def preprocess_receipt(
    file_bytes: bytes,
    file_name: str,
    *,
    max_pdf_pages: int = MAX_PDF_PAGES,
) -> list[bytes]:
    if not file_bytes:
        raise ImagePreprocessingError("Receipt file must not be empty")
    if len(file_bytes) > MAX_RECEIPT_BYTES:
        raise ImagePreprocessingError("Receipt file exceeds the supported size limit")
    if max_pdf_pages <= 0:
        raise ValueError("max_pdf_pages must be positive")

    extension = Path(file_name).suffix.lower()
    if extension == ".pdf":
        return _preprocess_pdf(file_bytes, max_pdf_pages)
    if extension not in _IMAGE_EXTENSIONS:
        raise ImagePreprocessingError("Unsupported receipt file type")
    return [preprocess_image(file_bytes)]


def preprocess_image(image_bytes: bytes) -> bytes:
    try:
        with Image.open(BytesIO(image_bytes)) as source:
            if source.width <= 0 or source.height <= 0:
                raise ImagePreprocessingError("Receipt image has invalid dimensions")
            if source.width * source.height > MAX_IMAGE_PIXELS:
                raise ImagePreprocessingError(
                    "Receipt image exceeds the supported pixel limit"
                )
            source.load()
            corrected = ImageOps.exif_transpose(source).convert("RGB")
    except UnidentifiedImageError as error:
        raise ImagePreprocessingError("Receipt image could not be opened") from error

    rgb_image = np.array(corrected)
    corrected.close()
    bgr_image = cv2.cvtColor(rgb_image, cv2.COLOR_RGB2BGR)
    cropped_image = _crop_and_rectify_receipt(bgr_image)
    enhanced_image = _enhance_contrast(cropped_image)
    resized_image = _resize_image(enhanced_image)

    success, encoded_image = cv2.imencode(
        ".png",
        resized_image,
        [cv2.IMWRITE_PNG_COMPRESSION, 3],
    )
    if not success:
        raise ImagePreprocessingError("Receipt image could not be encoded")
    return encoded_image.tobytes()


def _preprocess_pdf(file_bytes: bytes, max_pdf_pages: int) -> list[bytes]:
    try:
        document = fitz.open(stream=file_bytes, filetype="pdf")
    except fitz.FileDataError as error:
        raise ImagePreprocessingError("Receipt PDF could not be opened") from error

    processed_pages: list[bytes] = []
    with document:
        if document.is_encrypted:
            raise ImagePreprocessingError("Encrypted receipt PDFs are not supported")
        if document.page_count == 0:
            raise ImagePreprocessingError("Receipt PDF contains no pages")
        if document.page_count > max_pdf_pages:
            raise ImagePreprocessingError("Receipt PDF contains too many pages")

        for page in document:
            page_size = max(page.rect.width, page.rect.height)
            if page_size <= 0:
                raise ImagePreprocessingError("Receipt PDF contains an invalid page")
            scale = min(2.0, MAX_IMAGE_DIMENSION / page_size)
            pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            image = Image.frombytes(
                "RGB",
                (pixmap.width, pixmap.height),
                pixmap.samples,
            )
            try:
                buffer = BytesIO()
                image.save(buffer, format="PNG")
                processed_pages.append(preprocess_image(buffer.getvalue()))
            finally:
                image.close()
    return processed_pages


def _crop_and_rectify_receipt(image: np.ndarray) -> np.ndarray:
    gray_image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blurred_image = cv2.GaussianBlur(gray_image, (5, 5), 0)
    edges = cv2.Canny(blurred_image, 40, 120)
    kernel = np.ones((5, 5), dtype=np.uint8)
    closed_edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel, iterations=2)
    contours, _ = cv2.findContours(
        closed_edges,
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE,
    )
    image_area = float(image.shape[0] * image.shape[1])

    for contour in sorted(contours, key=cv2.contourArea, reverse=True):
        if cv2.contourArea(contour) < image_area * _MIN_RECEIPT_AREA_RATIO:
            break
        perimeter = cv2.arcLength(contour, True)
        polygon = cv2.approxPolyDP(contour, 0.02 * perimeter, True)
        if len(polygon) == 4 and cv2.isContourConvex(polygon):
            return _perspective_transform(image, polygon.reshape(4, 2))
    return image


def _perspective_transform(image: np.ndarray, points: np.ndarray) -> np.ndarray:
    ordered_points = _order_points(points.astype("float32"))
    top_left, top_right, bottom_right, bottom_left = ordered_points

    width = int(
        max(
            np.linalg.norm(bottom_right - bottom_left),
            np.linalg.norm(top_right - top_left),
        )
    )
    height = int(
        max(
            np.linalg.norm(top_right - bottom_right),
            np.linalg.norm(top_left - bottom_left),
        )
    )
    if width <= 0 or height <= 0:
        return image

    destination = np.array(
        [
            [0, 0],
            [width - 1, 0],
            [width - 1, height - 1],
            [0, height - 1],
        ],
        dtype="float32",
    )
    transform = cv2.getPerspectiveTransform(ordered_points, destination)
    return cv2.warpPerspective(image, transform, (width, height))


def _order_points(points: np.ndarray) -> np.ndarray:
    # Angular ordering avoids assigning the same corner twice at 45 degrees.
    center = points.mean(axis=0)
    angles = np.arctan2(points[:, 1] - center[1], points[:, 0] - center[0])
    ordered = points[np.argsort(angles)]
    start = int(np.argmin(ordered.sum(axis=1)))
    return np.roll(ordered, -start, axis=0).astype("float32")


def _enhance_contrast(image: np.ndarray) -> np.ndarray:
    lab_image = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    lightness, channel_a, channel_b = cv2.split(lab_image)
    enhanced_lightness = cv2.createCLAHE(
        clipLimit=2.0,
        tileGridSize=(8, 8),
    ).apply(lightness)
    enhanced_lab = cv2.merge((enhanced_lightness, channel_a, channel_b))
    return cv2.cvtColor(enhanced_lab, cv2.COLOR_LAB2BGR)


def _resize_image(image: np.ndarray) -> np.ndarray:
    height, width = image.shape[:2]
    largest_dimension = max(height, width)
    if largest_dimension <= MAX_IMAGE_DIMENSION:
        return image
    scale = MAX_IMAGE_DIMENSION / largest_dimension
    return cv2.resize(
        image,
        (int(width * scale), int(height * scale)),
        interpolation=cv2.INTER_AREA,
    )

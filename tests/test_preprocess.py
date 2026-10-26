import unittest
from io import BytesIO

import cv2
import numpy as np
import pymupdf as fitz
from PIL import Image

from vision.preprocess import (
    ImagePreprocessingError,
    preprocess_image,
    preprocess_receipt,
)


class ImagePreprocessingTests(unittest.TestCase):
    @staticmethod
    def encode_image(image: np.ndarray, extension: str = ".png") -> bytes:
        success, encoded = cv2.imencode(extension, image)
        if not success:
            raise RuntimeError("Test image could not be encoded")
        return encoded.tobytes()

    def test_applies_exif_rotation_before_processing(self) -> None:
        image = Image.new("RGB", (100, 200), "white")
        exif = Image.Exif()
        exif[274] = 6
        source = BytesIO()
        image.save(source, format="JPEG", exif=exif)
        image.close()

        processed = preprocess_image(source.getvalue())
        decoded = cv2.imdecode(
            np.frombuffer(processed, dtype=np.uint8),
            cv2.IMREAD_COLOR,
        )

        self.assertIsNotNone(decoded)
        assert decoded is not None
        self.assertGreater(decoded.shape[1], decoded.shape[0])

    def test_crops_and_rectifies_a_perspective_receipt(self) -> None:
        image = np.full((600, 800, 3), 35, dtype=np.uint8)
        receipt_corners = np.array(
            [[190, 55], [600, 95], [565, 535], [150, 485]],
            dtype=np.int32,
        )
        cv2.fillConvexPoly(image, receipt_corners, (245, 245, 245))
        cv2.putText(
            image,
            "RECEIPT",
            (250, 200),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.0,
            (20, 20, 20),
            2,
        )

        processed = preprocess_image(self.encode_image(image))
        decoded = cv2.imdecode(
            np.frombuffer(processed, dtype=np.uint8),
            cv2.IMREAD_COLOR,
        )

        self.assertIsNotNone(decoded)
        assert decoded is not None
        self.assertLess(decoded.shape[1], 700)
        self.assertLess(decoded.shape[0], 550)
        self.assertAlmostEqual(
            decoded.shape[1] / decoded.shape[0],
            0.95,
            delta=0.15,
        )

    def test_preprocesses_each_pdf_page(self) -> None:
        document = fitz.open()
        for page_number in range(2):
            page = document.new_page()
            page.insert_text((72, 72), f"Receipt page {page_number + 1}")
        pdf_bytes = document.tobytes()
        document.close()

        processed_pages = preprocess_receipt(pdf_bytes, "receipt.pdf")

        self.assertEqual(len(processed_pages), 2)
        for page in processed_pages:
            self.assertTrue(page.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_rejects_pdf_over_page_limit(self) -> None:
        document = fitz.open()
        document.new_page()
        document.new_page()
        pdf_bytes = document.tobytes()
        document.close()

        with self.assertRaisesRegex(ImagePreprocessingError, "too many pages"):
            preprocess_receipt(pdf_bytes, "receipt.pdf", max_pdf_pages=1)

    def test_rejects_unsupported_file_type(self) -> None:
        with self.assertRaisesRegex(ImagePreprocessingError, "file type"):
            preprocess_receipt(b"data", "receipt.txt")

    def test_rejects_empty_file(self) -> None:
        with self.assertRaisesRegex(ImagePreprocessingError, "empty"):
            preprocess_receipt(b"", "receipt.jpg")


if __name__ == "__main__":
    unittest.main()

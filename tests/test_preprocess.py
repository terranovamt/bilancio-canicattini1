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
    _order_points,
)


class ImagePreprocessingTests(unittest.TestCase):
    def test_crops_small_rotated_receipt_with_background(self) -> None:
        image = np.full((1000, 1200, 3), 45, dtype=np.uint8)
        corners = cv2.boxPoints(((600, 500), (190, 570), 28)).astype(np.int32)
        cv2.fillConvexPoly(image, corners, (245, 245, 245))
        processed = preprocess_image(self.encode_image(image))
        decoded = cv2.imdecode(np.frombuffer(processed, dtype=np.uint8), cv2.IMREAD_COLOR)
        self.assertLess(decoded.shape[0] * decoded.shape[1], 150_000)
        self.assertGreater(decoded.mean(), 210)
        self.assertAlmostEqual(
            min(decoded.shape[:2]) / max(decoded.shape[:2]), 190 / 570, delta=0.06
        )

    def test_orders_diamond_corners_without_duplicate_points(self) -> None:
        points = np.array([[100, 0], [200, 100], [100, 200], [0, 100]], dtype=np.float32)
        for order in ([0, 1, 2, 3], [3, 1, 0, 2]):
            ordered = _order_points(points[order])
            self.assertEqual(len(np.unique(ordered, axis=0)), 4)
            edges = np.roll(ordered, -1, axis=0) - ordered
            self.assertTrue(np.all(np.linalg.norm(edges, axis=1) > 100))

    def test_keeps_whole_image_when_no_receipt_boundary_is_visible(self) -> None:
        image = np.full((400, 600, 3), 180, dtype=np.uint8)
        decoded = cv2.imdecode(
            np.frombuffer(preprocess_image(self.encode_image(image)), dtype=np.uint8),
            cv2.IMREAD_COLOR,
        )
        self.assertEqual(decoded.shape, image.shape)

    def test_rectification_keeps_print_near_all_four_corners(self) -> None:
        receipt = np.full((500, 220, 3), 245, dtype=np.uint8)
        for position in ((12, 12), (188, 12), (12, 468), (188, 468)):
            x, y = position
            cv2.rectangle(receipt, (x, y), (x + 18, y + 18), (0, 0, 0), -1)
        source = np.array([[0, 0], [219, 0], [219, 499], [0, 499]], dtype=np.float32)
        target = np.array([[370, 110], [600, 180], [485, 705], [245, 620]], dtype=np.float32)
        warped = cv2.warpPerspective(
            receipt, cv2.getPerspectiveTransform(source, target),
            (900, 850), borderValue=(35, 35, 35),
        )
        decoded = cv2.imdecode(
            np.frombuffer(preprocess_image(self.encode_image(warped)), dtype=np.uint8),
            cv2.IMREAD_GRAYSCALE,
        )
        height, width = decoded.shape
        for quadrant in (
            decoded[:height // 2, :width // 2], decoded[:height // 2, width // 2:],
            decoded[height // 2:, :width // 2], decoded[height // 2:, width // 2:],
        ):
            self.assertGreater(np.count_nonzero(quadrant < 60), 100)
        self.assertLess(width, 300)
        self.assertLess(height, 650)

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

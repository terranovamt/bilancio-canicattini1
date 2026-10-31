import hashlib
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from io import BytesIO

from PIL import Image
import pymupdf

from storage.archive import FileArchive, build_receipt_filename


class ReceiptFilenameTests(unittest.TestCase):
    def test_builds_sanitized_italian_date_filename(self) -> None:
        filename = build_receipt_filename(
            transaction_date=date(2026, 10, 26),
            merchant="Caffè & Due",
            account="Cassa Co.ca",
            amount=Decimal("12.50"),
            extension="JPG",
        )

        self.assertEqual(
            filename,
            "26_10_2026_caffe_due_cassa_co_ca_12.50.jpg",
        )

    def test_rejects_empty_sanitized_name(self) -> None:
        with self.assertRaisesRegex(ValueError, "merchant"):
            build_receipt_filename(
                transaction_date=date(2026, 10, 26),
                merchant="!!!",
                account="Cassa Co.ca",
                amount=Decimal("12.50"),
                extension=".jpg",
            )

    def test_rejects_invalid_amount(self) -> None:
        with self.assertRaisesRegex(ValueError, "amount"):
            build_receipt_filename(
                transaction_date=date(2026, 10, 26),
                merchant="Test Shop",
                account="Cassa Co.ca",
                amount=Decimal("NaN"),
                extension=".jpg",
            )

    def test_rejects_unsupported_extension(self) -> None:
        with self.assertRaisesRegex(ValueError, "extension"):
            build_receipt_filename(
                transaction_date=date(2026, 10, 26),
                merchant="Test Shop",
                account="Cassa Co.ca",
                amount=Decimal("12.50"),
                extension=".exe",
            )


class FileArchiveTests(unittest.TestCase):
    def test_saves_original_and_corrected_png_without_overwriting_on_collision(self) -> None:
        buffer = BytesIO()
        with Image.new("RGB", (120, 250), "white") as image:
            image.save(buffer, format="JPEG")
        self.source.write_bytes(buffer.getvalue())
        results = [
            self.archive.archive(
                self.source, transaction_date=date(2026, 10, 31), merchant="Test",
                account="Cassa", amount=Decimal("1"), save_corrected=True,
            )
            for _ in range(2)
        ]
        for archived in results:
            self.assertEqual(archived.path.read_bytes(), buffer.getvalue())
            self.assertFalse(archived.correction_failed)
            self.assertEqual(len(archived.corrected_paths), 1)
            self.assertEqual(archived.corrected_paths[0].parent.name, "corrette")
            self.assertTrue(archived.corrected_paths[0].read_bytes().startswith(b"\x89PNG"))
        self.assertNotEqual(results[0].corrected_paths, results[1].corrected_paths)

    def test_saves_each_corrected_pdf_page(self) -> None:
        source = self.root / "receipt.pdf"
        with pymupdf.open() as document:
            document.new_page()
            document.new_page()
            source.write_bytes(document.tobytes())
        result = self.archive.archive(
            source, transaction_date=date(2026, 10, 31), merchant="Test",
            account="Cassa", amount=Decimal("1"), save_corrected=True,
        )
        self.assertEqual(result.path.read_bytes(), source.read_bytes())
        self.assertEqual(len(result.corrected_paths), 2)
        self.assertTrue(all(path.is_file() for path in result.corrected_paths))

    def test_copy_write_failure_keeps_original_and_removes_partial_copies(self) -> None:
        original_open = Path.open

        def failing_open(path, *args, **kwargs):
            if path.name.endswith("_pagina_02.png"):
                raise OSError("synthetic failure")
            return original_open(path, *args, **kwargs)

        with (
            patch("storage.archive.preprocess_receipt", return_value=[b"one", b"two"]),
            patch.object(Path, "open", failing_open),
            self.assertLogs("storage.archive", level="WARNING"),
        ):
            result = self.archive.archive(
                self.source, transaction_date=date(2026, 10, 31), merchant="Test",
                account="Cassa", amount=Decimal("1"), save_corrected=True,
            )
        self.assertTrue(result.correction_failed)
        self.assertEqual(result.path.read_bytes(), b"safe test receipt")
        self.assertEqual(list((result.path.parent / "corrette").iterdir()), [])

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.source = self.root / "receipt.jpg"
        self.source.write_bytes(b"safe test receipt")
        self.archive = FileArchive(self.root / "archive")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def archive_receipt(self):
        return self.archive.archive(
            self.source,
            transaction_date=date(2026, 10, 26),
            merchant="Test Shop",
            account="Cassa Co.ca",
            amount=Decimal("12.50"),
        )

    def test_archives_under_year_and_month_with_hash(self) -> None:
        result = self.archive_receipt()

        self.assertEqual(result.path.parent.name, "10")
        self.assertEqual(result.path.parent.parent.name, "2026")
        self.assertEqual(result.path.name, "26_10_2026_test_shop_cassa_co_ca_12.50.jpg")
        self.assertEqual(result.path.read_bytes(), b"safe test receipt")
        self.assertEqual(result.sha256, hashlib.sha256(b"safe test receipt").hexdigest())

    def test_uses_suffix_when_filename_already_exists(self) -> None:
        first = self.archive_receipt()
        second = self.archive_receipt()

        self.assertNotEqual(first.path, second.path)
        self.assertEqual(
            second.path.name,
            "26_10_2026_test_shop_cassa_co_ca_12.50_2.jpg",
        )

    def test_rejects_unsupported_file_type(self) -> None:
        unsupported = self.root / "receipt.txt"
        unsupported.write_text("not a receipt", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "extension"):
            self.archive.archive(
                unsupported,
                transaction_date=date(2026, 10, 26),
                merchant="Test Shop",
                account="Cassa Co.ca",
                amount=Decimal("12.50"),
            )


if __name__ == "__main__":
    unittest.main()

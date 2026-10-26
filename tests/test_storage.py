import hashlib
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

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

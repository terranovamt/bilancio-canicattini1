import tempfile
import unittest
from pathlib import Path

from storage.pending import PendingReceiptStore


class PendingReceiptStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp_dir.name) / "pending"
        self.store = PendingReceiptStore(self.directory)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_saves_file_with_generated_id_and_hash(self) -> None:
        receipt = self.store.save(b"receipt bytes", "telegram-photo.jpg")

        self.assertEqual(receipt.path.parent, self.directory)
        self.assertEqual(receipt.path.suffix, ".jpg")
        self.assertEqual(receipt.path.read_bytes(), b"receipt bytes")
        self.assertEqual(len(receipt.sha256), 64)

    def test_deletes_only_files_inside_pending_directory(self) -> None:
        receipt = self.store.save(b"receipt bytes", "receipt.pdf")

        self.assertTrue(self.store.delete(receipt.path))
        self.assertFalse(self.store.delete(receipt.path))

    def test_rejects_path_outside_pending_directory(self) -> None:
        outside_path = Path(self.temp_dir.name) / "other.jpg"

        with self.assertRaisesRegex(ValueError, "outside"):
            self.store.delete(outside_path)

    def test_rejects_unsupported_extension(self) -> None:
        with self.assertRaisesRegex(ValueError, "file type"):
            self.store.save(b"file", "receipt.txt")

    def test_rejects_empty_content(self) -> None:
        with self.assertRaisesRegex(ValueError, "empty"):
            self.store.save(b"", "receipt.jpg")

    def test_reads_only_pending_receipts(self) -> None:
        receipt = self.store.save(b"synthetic receipt", "receipt.jpg")
        self.assertEqual(self.store.read(receipt.path), b"synthetic receipt")
        outside = Path(self.temp_dir.name) / "other.jpg"
        outside.write_bytes(b"not a receipt")
        with self.assertRaisesRegex(ValueError, "outside"):
            self.store.read(outside)
        receipt.path.write_bytes(b"")
        with self.assertRaisesRegex(ValueError, "empty"):
            self.store.read(receipt.path)


if __name__ == "__main__":
    unittest.main()

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


if __name__ == "__main__":
    unittest.main()

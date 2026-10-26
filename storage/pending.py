from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path

MAX_PENDING_RECEIPT_BYTES = 20 * 1024 * 1024
_ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".pdf"}


@dataclass(frozen=True, slots=True)
class PendingReceipt:
    receipt_id: str
    path: Path
    sha256: str


class PendingReceiptStore:
    def __init__(self, directory: str | Path) -> None:
        self._directory = Path(directory)

    def save(self, file_bytes: bytes, file_name: str) -> PendingReceipt:
        if not file_bytes:
            raise ValueError("Receipt file must not be empty")
        if len(file_bytes) > MAX_PENDING_RECEIPT_BYTES:
            raise ValueError("Receipt file exceeds the supported size limit")

        extension = Path(file_name).suffix.lower()
        if extension not in _ALLOWED_EXTENSIONS:
            raise ValueError("Unsupported receipt file type")

        self._directory.mkdir(parents=True, exist_ok=True)
        receipt_id = uuid.uuid4().hex
        path = self._directory / f"{receipt_id}{extension}"
        with path.open("xb") as pending_file:
            pending_file.write(file_bytes)
        return PendingReceipt(
            receipt_id=receipt_id,
            path=path,
            sha256=hashlib.sha256(file_bytes).hexdigest(),
        )

    def delete(self, path: str | Path) -> bool:
        candidate = Path(path).resolve()
        directory = self._directory.resolve()
        try:
            candidate.relative_to(directory)
        except ValueError as error:
            raise ValueError("Pending receipt path is outside the pending directory") from error
        if not candidate.is_file():
            return False
        candidate.unlink()
        return True

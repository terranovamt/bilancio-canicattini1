from __future__ import annotations

import hashlib
import logging
import re
import shutil
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

import cv2

from vision.preprocess import ImagePreprocessingError, preprocess_receipt

logger = logging.getLogger(__name__)

_ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".pdf"}
_CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True, slots=True)
class ArchivedReceipt:
    path: Path
    sha256: str
    corrected_paths: tuple[Path, ...] = ()
    correction_failed: bool = False


def build_receipt_filename(
    *,
    transaction_date: date,
    merchant: str,
    account: str,
    amount: Decimal,
    extension: str,
) -> str:
    if not amount.is_finite() or amount < 0:
        raise ValueError("amount must be a finite non-negative value")
    normalized_extension = extension.lower()
    if not normalized_extension.startswith("."):
        normalized_extension = f".{normalized_extension}"
    if normalized_extension not in _ALLOWED_EXTENSIONS:
        raise ValueError("Unsupported receipt file extension")

    date_part = transaction_date.strftime("%d_%m_%Y")
    merchant_part = _safe_name_part(merchant, "merchant")
    account_part = _safe_name_part(account, "account")
    amount_part = f"{amount:.2f}"
    return (
        f"{date_part}_{merchant_part}_{account_part}_{amount_part}"
        f"{normalized_extension}"
    )


class FileArchive:
    def __init__(self, storage_path: str | Path) -> None:
        self._storage_path = Path(storage_path)

    def archive(
        self,
        source_path: str | Path,
        *,
        transaction_date: date,
        merchant: str,
        account: str,
        amount: Decimal,
        save_corrected: bool = False,
    ) -> ArchivedReceipt:
        source = Path(source_path)
        if not source.is_file():
            raise FileNotFoundError(f"Receipt file not found: {source}")

        extension = source.suffix.lower()
        filename = build_receipt_filename(
            transaction_date=transaction_date,
            merchant=merchant,
            account=account,
            amount=amount,
            extension=extension,
        )
        sha256 = calculate_file_hash(source)
        destination_directory = (
            self._storage_path
            / transaction_date.strftime("%Y")
            / transaction_date.strftime("%m")
        )
        destination_directory.mkdir(parents=True, exist_ok=True)

        base_path = destination_directory / filename
        for suffix in range(1, 1000):
            destination = _collision_path(base_path, suffix)
            created = False
            try:
                with destination.open("xb") as target:
                    created = True
                    with source.open("rb") as source_file:
                        shutil.copyfileobj(source_file, target, length=_CHUNK_SIZE)
            except FileExistsError:
                continue
            except OSError:
                if created:
                    destination.unlink(missing_ok=True)
                raise
            corrected_paths: tuple[Path, ...] = ()
            correction_failed = False
            if save_corrected:
                try:
                    corrected_paths = self._save_corrected(destination)
                except (ImagePreprocessingError, OSError, cv2.error) as error:
                    logger.warning("Could not archive corrected receipt: %s", type(error).__name__)
                    correction_failed = True
            return ArchivedReceipt(
                path=destination, sha256=sha256,
                corrected_paths=corrected_paths, correction_failed=correction_failed,
            )

        raise FileExistsError("Could not find an unused receipt filename")

    @staticmethod
    def _save_corrected(original: Path) -> tuple[Path, ...]:
        images = preprocess_receipt(original.read_bytes(), original.name)
        directory = original.parent / "corrette"
        directory.mkdir(parents=True, exist_ok=True)
        created: list[Path] = []
        try:
            for index, image in enumerate(images, start=1):
                # Include the original extension to distinguish a PDF from a same-name photo.
                destination = directory / f"{original.name}_pagina_{index:02d}.png"
                with destination.open("xb") as target:
                    created.append(destination)
                    target.write(image)
        except OSError:
            for path in created:
                path.unlink(missing_ok=True)
            raise
        return tuple(created)


def calculate_file_hash(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as file:
        while chunk := file.read(_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_name_part(value: str, field_name: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii").lower()
    safe_value = re.sub(r"[^a-z0-9]+", "_", ascii_value).strip("_")
    if not safe_value:
        raise ValueError(f"{field_name} must contain letters or numbers")
    return safe_value[:40].rstrip("_")


def _collision_path(base_path: Path, suffix: int) -> Path:
    if suffix == 1:
        return base_path
    return base_path.with_name(f"{base_path.stem}_{suffix}{base_path.suffix}")

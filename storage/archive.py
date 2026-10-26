from __future__ import annotations

import hashlib
import re
import shutil
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

_ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".pdf"}
_CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True, slots=True)
class ArchivedReceipt:
    path: Path
    sha256: str


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
            return ArchivedReceipt(path=destination, sha256=sha256)

        raise FileExistsError("Could not find an unused receipt filename")


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

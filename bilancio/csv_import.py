from __future__ import annotations

import argparse
import asyncio
import csv
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Sequence

from dotenv import load_dotenv

from bilancio.client import (
    ACCOUNTING_VOICE_OPTIONS,
    BilancioClientError,
    BilancioClient,
    PortalExpense,
    PortalOption,
    TransactionDirection,
    scout_year_label,
)
from bilancio.playwright_client import PlaywrightBilancioClient
from config import ConfigurationError, Settings


class CsvImportError(ValueError):
    """Raised when a CSV entry cannot be imported safely."""


@dataclass(frozen=True, slots=True)
class CsvExpenseRow:
    row_number: int
    transaction_date: date
    description: str = field(repr=False)
    signed_amount: Decimal
    direction: TransactionDirection
    account_reference: str = field(repr=False)
    accounting_voice: str
    transaction_type_reference: str = field(repr=False)
    unit_reference: str = field(repr=False)
    category_reference: str | None = field(repr=False)

    @property
    def amount(self) -> Decimal:
        return abs(self.signed_amount)


_REQUIRED_COLUMNS = (
    "data",
    "nota",
    "tipo",
    "importo",
    "cassa",
    "voce",
    "tipologia",
    "unita",
    "categoria",
)
_DATE_FORMATS = ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%y", "%d-%m-%y")
_DIRECTION_VALUES = {
    "entrata": TransactionDirection.INCOME,
    "entrate": TransactionDirection.INCOME,
    "uscita": TransactionDirection.EXPENSE,
    "uscite": TransactionDirection.EXPENSE,
}
_OPTION_PREFIX_SEPARATORS = frozenset(" \t-:–—(")


def read_expense_csv(path: str | Path) -> list[CsvExpenseRow]:
    csv_path = Path(path)
    try:
        content = csv_path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as error:
        raise CsvImportError("Non riesco a leggere il file CSV UTF-8.") from error

    try:
        dialect = csv.Sniffer().sniff(content[:8192], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel

    reader = csv.DictReader(content.splitlines(), dialect=dialect)
    if reader.fieldnames is None:
        raise CsvImportError("Il CSV non contiene intestazioni.")

    header_map: dict[str, str] = {}
    for header in reader.fieldnames:
        normalized = _normalize(header or "")
        if not normalized or normalized in header_map:
            raise CsvImportError("Il CSV contiene intestazioni vuote o duplicate.")
        header_map[normalized] = header

    missing_columns = [
        column for column in _REQUIRED_COLUMNS if column not in header_map
    ]
    if missing_columns:
        raise CsvImportError(
            "Mancano una o più colonne richieste nel CSV: "
            + ", ".join(missing_columns)
            + "."
        )

    entries: list[CsvExpenseRow] = []
    for row_number, raw_row in enumerate(reader, start=2):
        if None in raw_row:
            raise CsvImportError(
                f"La riga {row_number} contiene più campi delle intestazioni."
            )
        values = {
            column: (raw_row.get(header) or "").strip()
            for column, header in header_map.items()
        }
        if not any(values.values()):
            continue
        entries.append(_parse_row(values, row_number))

    if not entries:
        raise CsvImportError("Il CSV non contiene spese da importare.")
    return entries


def _parse_row(values: dict[str, str], row_number: int) -> CsvExpenseRow:
    direction_text = _normalize(values["tipo"])
    direction = _DIRECTION_VALUES.get(direction_text)
    if direction is None:
        raise CsvImportError(
            f"La riga {row_number} deve indicare entrata oppure uscita nel campo tipo."
        )

    transaction_date = _parse_date(values["data"], row_number)
    signed_amount = _parse_amount(values["importo"], row_number)
    if direction is TransactionDirection.INCOME and signed_amount <= 0:
        raise CsvImportError(
            f"La riga {row_number} ha un importo non coerente con un'entrata."
        )
    if direction is TransactionDirection.EXPENSE and signed_amount >= 0:
        raise CsvImportError(
            f"La riga {row_number} ha un importo non coerente con un'uscita."
        )

    description = values["nota"].strip()
    if not description:
        raise CsvImportError(
            f"La riga {row_number} non ha una descrizione nel campo nota."
        )

    account_reference = values["cassa"].strip()
    if not account_reference:
        raise CsvImportError(f"La riga {row_number} non indica la cassa.")

    accounting_voice = _resolve_accounting_voice(values["voce"], row_number)
    transaction_type_reference = values["tipologia"].strip()
    if not transaction_type_reference:
        raise CsvImportError(f"La riga {row_number} non indica la tipologia.")

    unit_reference = values["unita"].strip()
    if not unit_reference:
        raise CsvImportError(f"La riga {row_number} non indica l'unità.")

    category_reference = values["categoria"].strip() or None

    return CsvExpenseRow(
        row_number=row_number,
        transaction_date=transaction_date,
        description=description,
        signed_amount=signed_amount,
        direction=direction,
        account_reference=account_reference,
        accounting_voice=accounting_voice,
        transaction_type_reference=transaction_type_reference,
        unit_reference=unit_reference,
        category_reference=category_reference,
    )


def _parse_date(value: str, row_number: int) -> date:
    for date_format in _DATE_FORMATS:
        try:
            return datetime.strptime(value.strip(), date_format).date()
        except ValueError:
            continue
    raise CsvImportError(
        f"La data della riga {row_number} non è in un formato riconosciuto."
    )


def _parse_amount(value: str, row_number: int) -> Decimal:
    normalized = (
        value.replace("\u00a0", "")
        .replace(" ", "")
        .replace("€", "")
        .strip()
    )
    if "," in normalized:
        normalized = normalized.replace(".", "").replace(",", ".")
    try:
        amount = Decimal(normalized)
    except InvalidOperation as error:
        raise CsvImportError(
            f"L'importo della riga {row_number} non è valido."
        ) from error
    if not amount.is_finite() or amount == 0:
        raise CsvImportError(
            f"L'importo della riga {row_number} deve essere diverso da zero."
        )
    return amount


def _resolve_accounting_voice(value: str, row_number: int) -> str:
    normalized = _normalize(value)
    for accounting_voice in ACCOUNTING_VOICE_OPTIONS:
        if normalized == _normalize(accounting_voice):
            return accounting_voice
    raise CsvImportError(
        f"La voce Scout/Ministero della riga {row_number} non è riconosciuta."
    )


def resolve_portal_option(
    reference: str,
    options: Sequence[PortalOption],
    *,
    field_name: str,
    row_number: int,
) -> PortalOption:
    normalized_reference = _normalize(reference)
    exact_matches = [
        option
        for option in options
        if normalized_reference
        in {_normalize(option.label), _normalize(option.value)}
    ]
    if len(exact_matches) == 1:
        return exact_matches[0]
    if len(exact_matches) > 1:
        raise CsvImportError(
            f"La riga {row_number} ha un valore ambiguo per {field_name}."
        )

    prefix_matches = [
        option
        for option in options
        if _has_code_prefix(option.label, normalized_reference)
        or _has_code_prefix(option.value, normalized_reference)
    ]
    if len(prefix_matches) == 1:
        return prefix_matches[0]
    if len(prefix_matches) > 1:
        raise CsvImportError(
            f"La riga {row_number} ha un codice ambiguo per {field_name}."
        )
    raise CsvImportError(
        f"Il valore di {field_name} della riga {row_number} "
        "non corrisponde a un'opzione disponibile nel portale."
    )


def _has_code_prefix(value: str, normalized_reference: str) -> bool:
    normalized_value = _normalize(value)
    if not normalized_reference or not normalized_value.startswith(
        normalized_reference
    ):
        return False
    if len(normalized_value) == len(normalized_reference):
        return True
    return normalized_value[len(normalized_reference)] in _OPTION_PREFIX_SEPARATORS


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


async def prepare_portal_expenses(
    rows: Sequence[CsvExpenseRow],
    client: BilancioClient,
) -> list[PortalExpense]:
    account_cache: dict[str, Sequence[PortalOption]] = {}
    type_cache: dict[
        tuple[str, str, TransactionDirection, str],
        Sequence[PortalOption],
    ] = {}
    unit_cache: dict[
        tuple[str, str, TransactionDirection, str, str],
        Sequence[PortalOption],
    ] = {}
    category_cache: dict[
        tuple[str, str, TransactionDirection, str, str, str],
        Sequence[PortalOption],
    ] = {}
    prepared: list[PortalExpense] = []

    for row in rows:
        scout_year = scout_year_label(row.transaction_date)
        if scout_year not in account_cache:
            account_cache[scout_year] = await client.list_accounts(
                transaction_date=row.transaction_date
            )
        account = resolve_portal_option(
            row.account_reference,
            account_cache[scout_year],
            field_name="cassa",
            row_number=row.row_number,
        )

        type_key = (
            scout_year,
            account.label,
            row.direction,
            row.accounting_voice,
        )
        if type_key not in type_cache:
            type_cache[type_key] = await client.list_transaction_types(
                account,
                row.direction,
                transaction_date=row.transaction_date,
                accounting_voice=row.accounting_voice,
            )
        transaction_type = resolve_portal_option(
            row.transaction_type_reference,
            type_cache[type_key],
            field_name="tipologia",
            row_number=row.row_number,
        )

        unit_key = (
            scout_year,
            account.label,
            row.direction,
            transaction_type.label,
            row.accounting_voice,
        )
        if unit_key not in unit_cache:
            unit_cache[unit_key] = await client.list_units(
                account,
                row.direction,
                transaction_type,
                transaction_date=row.transaction_date,
                accounting_voice=row.accounting_voice,
            )
        unit = resolve_portal_option(
            row.unit_reference,
            unit_cache[unit_key],
            field_name="unità",
            row_number=row.row_number,
        )

        category = None
        if row.category_reference is not None:
            category_key = (
                scout_year,
                account.label,
                row.direction,
                transaction_type.label,
                unit.label,
                row.accounting_voice,
            )
            if category_key not in category_cache:
                category_cache[category_key] = await client.list_categories(
                    account,
                    row.direction,
                    transaction_type,
                    unit,
                    transaction_date=row.transaction_date,
                    accounting_voice=row.accounting_voice,
                )
            category = resolve_portal_option(
                row.category_reference,
                category_cache[category_key],
                field_name="categoria",
                row_number=row.row_number,
            )

        prepared.append(
            PortalExpense(
                transaction_date=row.transaction_date,
                description=row.description,
                amount=row.amount,
                direction=row.direction,
                account=account,
                transaction_type=transaction_type,
                unit=unit,
                category=category,
                notes=None,
                accounting_voice=row.accounting_voice,
            )
        )
    return prepared


def _summary(rows: Sequence[CsvExpenseRow]) -> str:
    directions = Counter(row.direction.value for row in rows)
    categories_missing = sum(row.category_reference is None for row in rows)
    return (
        f"Righe lette: {len(rows)}; entrate: {directions['entrata']}; "
        f"uscite: {directions['uscita']}; categorie vuote: {categories_missing}."
    )


async def _run_import(
    csv_path: Path,
    *,
    apply: bool,
    confirmation_count: int | None,
    browser_channel: str | None,
) -> int:
    rows = read_expense_csv(csv_path)
    print(_summary(rows))

    load_dotenv(dotenv_path=Path.cwd() / ".env")
    settings = Settings.from_env()
    if (
        settings.bilancio_url is None
        or settings.bilancio_user is None
        or settings.bilancio_password is None
    ):
        print("Le impostazioni AGESCI nel file .env sono incomplete.", file=sys.stderr)
        return 2
    if apply:
        if settings.dry_run:
            print(
                "Importazione bloccata: imposta DRY_RUN=false solo dopo "
                "l'approvazione finale.",
                file=sys.stderr,
            )
            return 2
        if confirmation_count != len(rows):
            print(
                "Importazione bloccata: il numero di conferma deve corrispondere "
                "alle righe del CSV.",
                file=sys.stderr,
            )
            return 2

    data_path = Path(settings.data_path)
    client = PlaywrightBilancioClient(
        base_url=settings.bilancio_url,
        username=settings.bilancio_user,
        password=settings.bilancio_password,
        selectors_path=Path(__file__).with_name("selectors.yaml"),
        session_state_path=data_path / "bilancio-session.json",
        debug_path=data_path / "debug",
        browser_channel=browser_channel or settings.playwright_browser_channel,
    )
    try:
        expenses = await prepare_portal_expenses(rows, client)
    except (BilancioClientError, CsvImportError) as error:
        print(
            f"Importazione interrotta prima del salvataggio: {error}",
            file=sys.stderr,
        )
        return 1

    if apply:
        expected_confirmation = f"INSERISCI {len(expenses)} SPESE"
        print(
            "Attenzione: la modalità reale può registrare spese nel portale. "
            "Controlla prima tutto il CSV."
        )
        try:
            confirmation = input(
                f"Per continuare, scrivi esattamente «{expected_confirmation}»: "
            )
        except EOFError:
            print("Importazione interrotta: conferma non ricevuta.")
            return 2
        if confirmation.strip() != expected_confirmation:
            print("Importazione interrotta: conferma non corrispondente.")
            return 2

    existing_count = 0
    for index, expense in enumerate(expenses, start=1):
        try:
            result = await client.create_expense(expense, dry_run=not apply)
        except BilancioClientError as error:
            print(
                f"Interruzione alla riga {rows[index - 1].row_number}: "
                f"{type(error).__name__}. Le righe successive non sono state tentate.",
                file=sys.stderr,
            )
            return 1
        if result.confirmation_id == "existing-entry":
            existing_count += 1
        print(f"Verificata riga {index} di {len(expenses)}.")

    if apply:
        print(
            f"Operazione terminata: {len(expenses) - existing_count} spese "
            f"registrate e verificate; {existing_count} già presenti."
        )
    else:
        print(
            f"Simulazione completata: {len(expenses) - existing_count} spese "
            f"compilate senza salvataggio; {existing_count} già presenti."
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Valida e simula l'importazione controllata di un CSV AGESCI."
    )
    parser.add_argument("csv_path", type=Path, help="Percorso del file CSV")
    parser.add_argument(
        "--browser-channel",
        choices=("chrome", "msedge"),
        help="Browser locale da usare al posto di Chromium",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Abilita l'importazione reale, con ulteriori conferme obbligatorie",
    )
    parser.add_argument(
        "--confirm-real-insert",
        type=int,
        help="Numero esatto di righe approvate per l'importazione reale",
    )
    args = parser.parse_args()

    try:
        return asyncio.run(
            _run_import(
                args.csv_path,
                apply=args.apply,
                confirmation_count=args.confirm_real_insert,
                browser_channel=args.browser_channel,
            )
        )
    except CsvImportError as error:
        print(f"CSV non valido: {error}", file=sys.stderr)
        return 2
    except ConfigurationError as error:
        print(f"Configurazione non valida: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

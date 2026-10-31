import unittest
from datetime import date
from decimal import Decimal

from bilancio.client import AccountLedger, LedgerEntry, PortalOption
from bot.summary import (
    format_euro,
    format_ledger_summary,
    reference_date_for,
    scout_year_name,
    scout_year_start,
)


def entry(day: date, description: str, income: str = "0", expense: str = "0") -> LedgerEntry:
    return LedgerEntry(day, description, Decimal(income), Decimal(expense))


def ledger(label: str, entries: list[LedgerEntry], balance: str, *, complete: bool = True) -> AccountLedger:
    opening = entries[0].income - entries[0].expense
    movements = entries[1:]
    return AccountLedger(
        account=PortalOption(label, label),
        opening_balance=opening,
        total_income=sum((e.income for e in movements), Decimal("0")),
        total_expense=sum((e.expense for e in movements), Decimal("0")),
        balance=Decimal(balance),
        entries=tuple(entries),
        total_rows=len(entries) if complete else 40,
        complete=complete,
    )


class SummaryFormatTests(unittest.TestCase):
    def test_totals_sum_all_accounts_without_float_rounding(self) -> None:
        samples = [
            AccountLedger(
                account=PortalOption(label, label), opening_balance=Decimal(opening),
                total_income=Decimal(income), total_expense=Decimal(expense),
                balance=Decimal(balance), entries=(), total_rows=0, complete=True,
                entries_available=False,
            )
            for label, opening, income, expense, balance in (
                ("A", "10.10", "2.20", "3.30", "9.00"),
                ("B", "0.00", "0.00", "0.01", "-0.01"),
            )
        ]
        text = "\n".join(format_ledger_summary(samples, 2026, include_totals=True))
        self.assertIn(
            "Totale di tutte le casse e conti\nSaldo iniziale: 10,10 €\n"
            "Entrate: 2,20 €\nUscite: 3,31 €\nSaldo finale: 8,99 €", text,
        )
        self.assertNotIn(
            "Totale di tutte le casse", "\n".join(format_ledger_summary(samples, 2026)),
        )

    def test_overview_does_not_claim_no_movements_when_entries_were_not_read(self) -> None:
        sample = AccountLedger(
            account=PortalOption("Cassa test", "Cassa test"),
            opening_balance=Decimal("10"), total_income=Decimal("2"),
            total_expense=Decimal("3"), balance=Decimal("9"), entries=(),
            total_rows=0, complete=True, entries_available=False,
        )
        text = "\n".join(format_ledger_summary([sample], 2026))
        self.assertIn("Saldo: 9,00 €", text)
        self.assertIn("Saldo iniziale: 10,00 €", text)
        self.assertNotIn("Nessun", text)
        self.assertNotIn("Ultima operazione", text)

    def test_default_summary_shows_only_latest_operation_without_details(self) -> None:
        sample = ledger(
            "Cassa esempio",
            [
                entry(date(2026, 10, 1), "Saldo Iniziale Calcolato", income="100"),
                entry(date(2026, 10, 2), "Vecchia spesa", expense="5"),
                entry(date(2026, 10, 29), "Negozio esempio", expense="12.50"),
            ],
            "82.50",
        )
        text = "\n".join(format_ledger_summary([sample], 2026))
        self.assertIn("💰 Saldo: 82,50 €", text)
        self.assertIn("Entrate 0,00 € · uscite 17,50 €", text)
        self.assertIn("Ultima operazione: 29/10 Negozio esempio -12,50 €", text)
        for hidden in ("Saldo iniziale", "Andamento mensile", "Ultime operazioni", "Vecchia spesa"):
            self.assertNotIn(hidden, text)
        details = "\n".join(format_ledger_summary([sample], 2026, details=True))
        for shown in ("Saldo iniziale", "Andamento mensile", "Ultime operazioni", "Vecchia spesa"):
            self.assertIn(shown, details)

    def test_scout_year_boundaries(self) -> None:
        self.assertEqual(scout_year_start(date(2026, 9, 30)), 2025)
        self.assertEqual(scout_year_start(date(2026, 10, 1)), 2026)
        self.assertEqual(scout_year_name(2026), "26-27")
        today = date(2026, 10, 29)
        self.assertEqual(reference_date_for(2026, today), today)
        self.assertEqual(reference_date_for(2025, today), date(2026, 9, 30))

    def test_formats_italian_amounts(self) -> None:
        self.assertEqual(format_euro(Decimal("1352.26")), "1.352,26 €")
        self.assertEqual(format_euro(Decimal("-0.1")), "-0,10 €")
        self.assertEqual(format_euro(Decimal("5"), signed=True), "+5,00 €")

    def test_reports_balance_monthly_trend_and_latest_operations(self) -> None:
        text = "\n".join(
            format_ledger_summary(
                [
                    ledger(
                        "Cassa EG",
                        [
                            entry(date(2026, 10, 1), "Saldo Iniziale Calcolato", income="100"),
                            entry(date(2026, 10, 3), "Ricordi", expense="17.79"),
                            entry(date(2026, 12, 6), "Quote", income="50"),
                        ],
                        "132.21",
                    ),
                    ledger("Cassa RS", [entry(date(2026, 10, 1), "Saldo Iniziale Calcolato", income="97.75")], "97.75"),
                ],
                2026,
                details=True,
            )
        )
        self.assertIn("Bilancio 26-27", text)
        self.assertIn("🏦 Cassa EG\n💰 Saldo: 132,21 €", text)
        self.assertIn("Entrate 50,00 € · uscite 17,79 €", text)
        self.assertIn("Saldo iniziale: 100,00 €", text)
        self.assertIn("• Ottobre 2026: entrate 0,00 € · uscite 17,79 € → saldo 82,21 €", text)
        self.assertIn("• Novembre 2026: entrate 0,00 € · uscite 0,00 € → saldo 82,21 €", text)
        self.assertIn("• Dicembre 2026: entrate 50,00 € · uscite 0,00 € → saldo 132,21 €", text)
        self.assertLess(text.index("06/12 Quote: +50,00 €"), text.index("03/10 Ricordi: -17,79 €"))
        self.assertIn("🏦 Cassa RS\n💰 Saldo: 97,75 €", text)
        self.assertIn("Nessuna operazione dall'inizio dell'anno.", text)
        self.assertNotIn("Saldo Iniziale Calcolato", text)

    def test_warns_when_portal_rows_are_partial(self) -> None:
        text = format_ledger_summary(
            [
                ledger(
                    "Cassa EG",
                    [entry(date(2026, 10, 1), "Saldo Iniziale Calcolato", income="1")],
                    "1",
                    complete=False,
                )
            ],
            2026,
        )[0]
        self.assertIn("mostra solo 1 righe su 40", text)

    def test_splits_long_summaries_into_telegram_sized_messages(self) -> None:
        entries = [entry(date(2026, 10, 1), "Saldo Iniziale Calcolato")]
        entries += [entry(date(2026 + (m > 12), (m - 1) % 12 + 1, 2), "x", expense="1") for m in range(10, 22)]
        messages = format_ledger_summary(
            [ledger(f"Cassa {index}", entries, "-12") for index in range(12)],
            2026,
            details=True,
        )
        self.assertGreater(len(messages), 1)
        self.assertTrue(all(len(message) <= 3800 for message in messages))
        self.assertEqual(sum(message.count("🏦") for message in messages), 12)


if __name__ == "__main__":
    unittest.main()

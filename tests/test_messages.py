import ast
from pathlib import Path
import unittest

from bot import messages
from bot.receipt_workflow import ReceiptWorkflow


class TelegramMessagesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.payload = {
            "date": "2026-10-29",
            "merchant": "Negozio esempio",
            "total_amount": "12.5",
            "currency": "EUR",
            "pending_path": r"C:\private\receipt.jpg",
        }
        self.selections = {
            "direction": "uscita",
            "account": {"label": "Cassa esempio"},
            "transaction_type": {"label": "Tipologia esempio"},
            "accounting_voice": "Voci Scout",
            "unit": None,
            "category": None,
            "portal_notes": None,
        }

    def test_details_show_only_populated_fields(self) -> None:
        self.assertEqual(
            messages.operation_details(self.payload, self.selections),
            "📅 29/10/2026\n🏪 Negozio esempio\n💶 12,50 € (uscita)\n"
            "🏦 Cassa esempio\n🏷️ Tipologia esempio",
        )
        self.assertEqual(messages.operation_details({}, {}), "")
        self.selections.update(
            unit={"label": "  "}, category={"label": ""}, portal_notes="  "
        )
        text = messages.operation_details(self.payload, self.selections)
        for absent in ("👥", "🗂️", "🗒️", "nessuna", "nessuno", "Voci", "NAS", "private"):
            self.assertNotIn(absent, text)

    def test_present_optional_fields_are_kept_and_empty_portal_unit_is_omitted(self) -> None:
        self.selections.update(
            unit={"label": "Unità esempio"},
            category={"label": "Categoria esempio"},
            portal_notes="Nota esempio",
        )
        text = messages.operation_details(self.payload, self.selections)
        self.assertIn("👥 Unità esempio", text)
        self.assertIn("🗂️ Categoria esempio", text)
        self.assertIn("🗒️ Nota esempio", text)
        self.assertNotIn(
            "👥", messages.operation_details(
                self.payload, self.selections, unit_left_empty=True,
            ),
        )

    def test_low_confidence_warning_has_exact_threshold_and_no_percentage(self) -> None:
        for confidence in (0.0, 0.699, 0.70, 0.92, 1.0):
            with self.subTest(confidence=confidence):
                text = messages.receipt_confirmation(self.payload, confidence)
                self.assertEqual(messages.LOW_CONFIDENCE in text, confidence < 0.7)
                self.assertNotIn("%", text)
                self.assertNotIn("Affidabilità", text)
                self.assertNotIn("NAS", text)

    def test_missing_and_intentional_edit_prompts_are_distinct(self) -> None:
        for field, missing, edit in (
            ("date", messages.MISSING_DATE, messages.EDIT_DATE),
            ("merchant", messages.MISSING_MERCHANT, messages.EDIT_MERCHANT),
            ("total_amount", messages.MISSING_AMOUNT, messages.EDIT_AMOUNT),
        ):
            self.assertNotEqual(missing, edit)
            self.assertEqual(ReceiptWorkflow._prompt_for(field), edit)

    def test_three_outcomes_keep_fixed_icons_and_explicit_meaning(self) -> None:
        self.assertTrue(messages.RECEIPT_SAVED.startswith("✅"))
        self.assertIn("salvata e verificata nel portale", messages.RECEIPT_SAVED)
        self.assertTrue(messages.TRIAL_SAVED.startswith("🧪"))
        self.assertIn("non è stato salvato nulla", messages.TRIAL_SAVED)
        self.assertTrue(messages.SUBMISSION_QUEUED.startswith("⚠️"))
        self.assertIn("Non sono sicuro", messages.SUBMISSION_QUEUED)
        for status, icon in (("submitted", "✅"), ("dry_run", "🧪"), ("failed", "⚠️")):
            row = messages.expense_row(self.payload, status)
            self.assertTrue(row.startswith(icon))
            self.assertIn("29/10", row)
            self.assertIn("12,50 €", row)

    def test_manual_help_explains_direct_save_and_explicit_test_mode(self) -> None:
        payload = {**self.payload, "manual_entry": True}
        details = ReceiptWorkflow._operation_details(payload, self.selections)
        self.assertIn("📝 Negozio esempio", details)
        self.assertNotIn("🏪", details)
        self.assertIn("senza altre conferme", messages.MENU_ADMIN_HELP)
        self.assertIn("modalità prova", messages.MENU_ADMIN_HELP)
        self.assertIn("Amministrazione", messages.MENU_ADMIN_HELP)
        form = messages.manual_form(
            {"date": "2026-10-29", "merchant": None, "total_amount": None},
            income_only=False, prompt=messages.MANUAL_MERCHANT, mode=messages.MODE_REAL,
        )
        self.assertIn("📅 Data: 29/10/2026", form)
        self.assertIn("✏️ Descrizione: da inserire", form)
        self.assertIn("👉 " + messages.MANUAL_MERCHANT, form)
        self.assertIn("giorno-mese-anno", messages.MANUAL_DATE)
        self.assertNotIn("/", messages.UNSUPPORTED_MESSAGE)

    def test_user_messages_do_not_include_internal_paths_or_technical_terms(self) -> None:
        rendered = (
            messages.HOME_TEXT, messages.MENU_HELP, messages.ANALYSIS_FAILED,
            messages.ANALYZER_UNAVAILABLE, messages.SUBMISSION_QUEUED,
            messages.SUBMISSION_FAILED, messages.TRIAL_UNCONFIRMED,
            messages.operation_details(self.payload, self.selections),
            messages.receipt_confirmation(self.payload, 0.5),
            messages.MANUAL_FAILED.format(
                details=messages.operation_details(self.payload, self.selections),
            ),
        )
        for text in rendered:
            for forbidden in ("NAS", "diagnostica", "modello locale", "C:\\", "/data", "receipt.jpg"):
                self.assertNotIn(forbidden, text)

    def test_workflow_modules_reference_only_existing_message_names(self) -> None:
        root = Path(__file__).resolve().parents[1] / "bot"
        for name in ("application.py", "receipt_workflow.py", "retry_service.py", "summary.py"):
            tree = ast.parse((root / name).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                    if node.value.id == "texts":
                        self.assertTrue(hasattr(messages, node.attr), f"{name}: {node.attr}")
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                    continue
                if node.func.attr not in {"reply_text", "edit_message_text", "answer"}:
                    continue
                for value in node.args:
                    self.assertFalse(isinstance(value, (ast.Constant, ast.JoinedStr)), name)

    def test_receipt_confirmation_keyboard_preserves_callbacks(self) -> None:
        buttons = [
            (button.text, button.callback_data)
            for row in ReceiptWorkflow._confirmation_keyboard().inline_keyboard
            for button in row
        ]
        self.assertEqual(buttons, [
            ("✅ Sì, è giusto", "receipt:confirm"),
            ("📅 Data", "receipt:edit:date"),
            ("💶 Importo", "receipt:edit:total_amount"),
            ("🏪 Negozio", "receipt:edit:merchant"),
            ("🏷️ Categoria", "receipt:edit:suggested_category"),
            ("Annulla", "receipt:cancel"),
        ])


if __name__ == "__main__":
    unittest.main()

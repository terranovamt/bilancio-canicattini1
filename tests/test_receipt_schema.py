import unittest
from datetime import date
from decimal import Decimal

from vision.receipt_schema import (
    ReceiptParseError,
    missing_required_fields,
    parse_receipt_json,
    requires_user_clarification,
)


class ReceiptSchemaTests(unittest.TestCase):
    def test_parses_valid_receipt_json(self) -> None:
        receipt = parse_receipt_json(
            """
            {
              "date": "2026-10-26",
              "merchant": "Test Shop",
              "total_amount": "12.50",
              "currency": "eur",
              "vat": [{"rate": "22", "amount": "2.25"}],
              "payment_method": "card",
              "line_items": [
                {"description": "Notebook", "quantity": "1", "price": "12.50"}
              ],
              "suggested_category": "materials",
              "confidence": 0.93,
              "notes": null
            }
            """
        )

        self.assertEqual(receipt.date, date(2026, 10, 26))
        self.assertEqual(receipt.total_amount, Decimal("12.50"))
        self.assertEqual(receipt.currency, "EUR")
        self.assertEqual(receipt.vat[0].amount, Decimal("2.25"))
        self.assertEqual(receipt.line_items[0].quantity, Decimal("1"))
        self.assertFalse(requires_user_clarification(receipt))

    def test_allows_missing_values_for_user_clarification(self) -> None:
        receipt = parse_receipt_json('{"confidence": 0.9}')

        self.assertEqual(
            missing_required_fields(receipt),
            ("date", "merchant", "total_amount"),
        )
        self.assertTrue(requires_user_clarification(receipt))

    def test_flags_low_confidence(self) -> None:
        receipt = parse_receipt_json(
            '{"date":"2026-10-26","merchant":"Shop","total_amount":"1.00",'
            '"confidence":0.4}'
        )

        self.assertTrue(requires_user_clarification(receipt))

    def test_rejects_non_json_output(self) -> None:
        with self.assertRaisesRegex(ReceiptParseError, "failed validation"):
            parse_receipt_json("```json\n{\"confidence\": 0.9}\n```")

    def test_rejects_out_of_range_confidence(self) -> None:
        with self.assertRaises(ReceiptParseError):
            parse_receipt_json('{"confidence": 1.2}')

    def test_rejects_non_iso_date(self) -> None:
        with self.assertRaises(ReceiptParseError):
            parse_receipt_json('{"date":"26/10/2026","confidence":0.9}')

    def test_rejects_unexpected_fields(self) -> None:
        with self.assertRaises(ReceiptParseError):
            parse_receipt_json('{"confidence":0.9,"extra":"unexpected"}')

    def test_rejects_invalid_clarification_threshold(self) -> None:
        receipt = parse_receipt_json('{"confidence":0.9}')

        with self.assertRaisesRegex(ValueError, "threshold"):
            requires_user_clarification(receipt, threshold=1.1)


if __name__ == "__main__":
    unittest.main()

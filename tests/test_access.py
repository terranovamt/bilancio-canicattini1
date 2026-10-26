import unittest

from bot.access import is_admin_user, is_authorized_user


class AccessPolicyTests(unittest.TestCase):
    def test_authorizes_configured_user(self) -> None:
        self.assertTrue(is_authorized_user(12345, frozenset({12345, 67890})))

    def test_rejects_unknown_or_missing_user(self) -> None:
        allowed = frozenset({12345, 67890})

        self.assertFalse(is_authorized_user(99999, allowed))
        self.assertFalse(is_authorized_user(None, allowed))

    def test_identifies_only_the_configured_admin(self) -> None:
        self.assertTrue(is_admin_user(12345, 12345))
        self.assertFalse(is_admin_user(67890, 12345))
        self.assertFalse(is_admin_user(None, 12345))


if __name__ == "__main__":
    unittest.main()

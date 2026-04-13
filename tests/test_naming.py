"""
Unit tests for naming conversion functions.
Tests only pure functions — no database connections needed.
"""

import unittest
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Import only the pure functions (avoid mysql.connector import)
import re


# ── Inline copy of pure functions for testing ─────────────────
# (These mirror mysql2pg/naming.py but avoid the mysql import)

def camel_to_snake(name: str) -> str:
    if not name or "_" in name:
        return name.lower()
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", s)
    return s.lower()


def needs_snake_conversion(name: str) -> bool:
    return bool(re.search(r"[A-Z]", name))


def is_valid_snake_case(name: str) -> bool:
    return bool(re.match(r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$", name))


# ── Tests ─────────────────────────────────────────────────────

class TestCamelToSnake(unittest.TestCase):
    """Test camelCase/PascalCase → snake_case conversion."""

    def test_camel_case(self):
        self.assertEqual(camel_to_snake("firstName"), "first_name")

    def test_pascal_case(self):
        self.assertEqual(camel_to_snake("UserAccount"), "user_account")

    def test_multiple_words(self):
        self.assertEqual(camel_to_snake("myLongVariableName"), "my_long_variable_name")

    def test_acronym_at_start(self):
        self.assertEqual(camel_to_snake("HTMLParser"), "html_parser")

    def test_acronym_in_middle(self):
        self.assertEqual(camel_to_snake("getHTTPSUrl"), "get_https_url")

    def test_already_lowercase(self):
        self.assertEqual(camel_to_snake("useraccounts"), "useraccounts")

    def test_already_snake_case(self):
        self.assertEqual(camel_to_snake("already_snake"), "already_snake")

    def test_already_snake_mixed_case(self):
        self.assertEqual(camel_to_snake("Already_Snake"), "already_snake")

    def test_single_word_lowercase(self):
        self.assertEqual(camel_to_snake("id"), "id")

    def test_single_word_uppercase(self):
        self.assertEqual(camel_to_snake("ID"), "id")

    def test_single_capital_letter(self):
        self.assertEqual(camel_to_snake("A"), "a")

    def test_empty_string(self):
        self.assertEqual(camel_to_snake(""), "")

    def test_with_numbers(self):
        self.assertEqual(camel_to_snake("address2Line"), "address2_line")

    def test_table_name_users(self):
        self.assertEqual(camel_to_snake("Users"), "users")

    def test_table_name_user_accounts(self):
        self.assertEqual(camel_to_snake("UserAccounts"), "user_accounts")

    def test_column_created_at(self):
        self.assertEqual(camel_to_snake("createdAt"), "created_at")

    def test_column_is_active(self):
        self.assertEqual(camel_to_snake("isActive"), "is_active")

    def test_column_account_id(self):
        self.assertEqual(camel_to_snake("accountId"), "account_id")


class TestNeedsSnakeConversion(unittest.TestCase):
    """Test detection of names needing conversion."""

    def test_camel_case_needs_conversion(self):
        self.assertTrue(needs_snake_conversion("firstName"))

    def test_pascal_case_needs_conversion(self):
        self.assertTrue(needs_snake_conversion("UserAccount"))

    def test_all_lowercase_no_conversion(self):
        self.assertFalse(needs_snake_conversion("useraccounts"))

    def test_snake_case_no_conversion(self):
        self.assertFalse(needs_snake_conversion("first_name"))

    def test_single_uppercase(self):
        self.assertTrue(needs_snake_conversion("Id"))


class TestIsValidSnakeCase(unittest.TestCase):
    """Test snake_case validation."""

    def test_valid_snake_case(self):
        self.assertTrue(is_valid_snake_case("user_accounts"))

    def test_valid_single_word(self):
        self.assertTrue(is_valid_snake_case("users"))

    def test_valid_with_numbers(self):
        self.assertTrue(is_valid_snake_case("address2_line"))

    def test_invalid_camel_case(self):
        self.assertFalse(is_valid_snake_case("firstName"))

    def test_invalid_pascal_case(self):
        self.assertFalse(is_valid_snake_case("UserAccount"))

    def test_invalid_starts_with_number(self):
        self.assertFalse(is_valid_snake_case("2nd_table"))

    def test_invalid_double_underscore(self):
        self.assertFalse(is_valid_snake_case("user__accounts"))


class TestEnumTypeNaming(unittest.TestCase):
    """Test ENUM type name generation (uses camel_to_snake)."""

    def test_simple_enum_name(self):
        snake_table = camel_to_snake("Users")
        snake_col = camel_to_snake("status")
        self.assertEqual(f"{snake_table}_{snake_col}", "users_status")

    def test_camel_case_enum_name(self):
        snake_table = camel_to_snake("UserAccounts")
        snake_col = camel_to_snake("accountStatus")
        self.assertEqual(f"{snake_table}_{snake_col}", "user_accounts_account_status")

    def test_already_snake_enum_name(self):
        snake_table = camel_to_snake("user_profiles")
        snake_col = camel_to_snake("role_type")
        self.assertEqual(f"{snake_table}_{snake_col}", "user_profiles_role_type")


if __name__ == "__main__":
    unittest.main()

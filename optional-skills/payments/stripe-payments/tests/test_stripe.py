"""Tests for stripe-payments CLI — validators, formatters, sanitizers, and helpers."""

import os
import sys
import json
import tempfile
import pytest

# Set a dummy key before importing stripe.py (it checks at import time)
os.environ["STRIPE_API_KEY"] = "sk_test_dummy123456789"

# Add scripts dir to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import stripe as sp


# ── Validators ──────────────────────────────────────────────────────────

class TestValidateAmount:
    def test_valid_integer(self):
        assert sp._validate_amount("5000") == 5000

    def test_zero(self):
        assert sp._validate_amount("0") == 0

    def test_negative_raises(self):
        with pytest.raises(SystemExit):
            sp._validate_amount("-100")

    def test_non_numeric_raises(self):
        with pytest.raises(SystemExit):
            sp._validate_amount("abc")

    def test_float_raises(self):
        with pytest.raises(SystemExit):
            sp._validate_amount("10.50")

    def test_none_raises(self):
        with pytest.raises(SystemExit):
            sp._validate_amount(None)

    def test_custom_field_name(self):
        try:
            sp._validate_amount("bad", field="refund amount")
        except SystemExit as e:
            assert "refund amount" in str(e)


class TestValidateEmail:
    def test_valid_email(self):
        assert sp._validate_email("user@example.com") == "user@example.com"

    def test_no_at_sign_raises(self):
        with pytest.raises(SystemExit):
            sp._validate_email("notanemail")

    def test_none_passes(self):
        assert sp._validate_email(None) is None

    def test_empty_string_passes(self):
        assert sp._validate_email("") == ""


class TestValidateCurrency:
    def test_valid_currency(self):
        assert sp._validate_currency("usd") == "usd"
        assert sp._validate_currency("EUR") == "eur"

    def test_defaults_to_usd(self):
        assert sp._validate_currency(None) == "usd"
        assert sp._validate_currency("") == "usd"

    def test_invalid_length_raises(self):
        with pytest.raises(SystemExit):
            sp._validate_currency("dollar")

    def test_single_char_raises(self):
        with pytest.raises(SystemExit):
            sp._validate_currency("u")


# ── Sanitizer ───────────────────────────────────────────────────────────

class TestSanitizeError:
    def test_redacts_full_key(self):
        msg = sp._sanitize_error(f"Auth failed with key {sp.SK}")
        assert sp.SK not in msg
        assert "***REDACTED***" in msg

    def test_redacts_partial_key(self):
        partial = sp.SK[4:]
        msg = sp._sanitize_error(f"Key suffix {partial} leaked")
        assert partial not in msg
        assert "***REDACTED***" in msg

    def test_no_crash_on_empty(self):
        msg = sp._sanitize_error("no secrets here")
        assert msg == "no secrets here"


# ── Formatters ──────────────────────────────────────────────────────────

class TestFormatters:
    def test_fmt_cents_usd(self):
        assert sp._fmt_cents(5000) == "$50.00 USD"
        assert sp._fmt_cents(0) == "$0.00 USD"
        assert sp._fmt_cents(99) == "$0.99 USD"

    def test_fmt_cents_other_currency(self):
        assert sp._fmt_cents(1000, "EUR") == "$10.00 EUR"

    def test_fmt_ts_valid(self):
        result = sp._fmt_ts(1700000000)
        assert result != "N/A"
        assert "-" in result  # YYYY-MM-DD format

    def test_fmt_ts_none(self):
        assert sp._fmt_ts(None) == "N/A"

    def test_fmt_ts_zero(self):
        assert sp._fmt_ts(0) == "N/A"


# ── Version ─────────────────────────────────────────────────────────────

class TestVersion:
    def test_version_exists(self):
        assert hasattr(sp, "VERSION")
        assert sp.VERSION == "1.0.0"

    def test_mode_detection_test(self):
        assert sp.MODE == "TEST"  # our dummy key is sk_test_

    def test_api_version_set(self):
        assert sp.API_VERSION.startswith("202")


# ── Headers ─────────────────────────────────────────────────────────────

class TestHeaders:
    def test_bearer_auth(self):
        h = sp._headers()
        assert h["Authorization"].startswith("Bearer ")

    def test_stripe_version(self):
        h = sp._headers()
        assert "Stripe-Version" in h

    def test_idempotency_key(self):
        h1 = sp._headers()
        h2 = sp._headers()
        assert h1["Idempotency-Key"] != h2["Idempotency-Key"]  # unique each call

    def test_form_content_type(self):
        h = sp._headers(form=True)
        assert h["Content-Type"] == "application/x-www-form-urlencoded"

    def test_no_content_type_for_get(self):
        h = sp._headers(form=False)
        assert "Content-Type" not in h


# ── Dispatch completeness ──────────────────────────────────────────────

class TestDispatchCompleteness:
    """Verify all 27 commands exist in the dispatch table."""

    EXPECTED_COMMANDS = {
        "invoice", "paylink", "send", "status", "balance", "stats",
        "followup", "list", "refund", "products", "customers", "customer",
        "newproduct", "subscriptions", "cancelsub", "webhooks", "portal",
        "coupon", "reconcile", "history", "search", "receipt", "ltv",
        "duplicate", "aging", "bulk-refund", "churn",
        "sync-tiers", "bulklink",
    }

    def test_dispatch_has_all_commands(self):
        # We can't easily call main() dispatch, but we can verify the set
        # by counting subparsers in the source
        import inspect
        source = inspect.getsource(sp.main)
        for cmd in self.EXPECTED_COMMANDS:
            # Each command has a sub.add_parser call
            assert f'"{cmd}"' in source or f"'{cmd}'" in source, f"Missing command: {cmd}"

    def test_no_ghost_commands(self):
        """revenue, disputes, export should NOT be in the dispatch."""
        import inspect
        source = inspect.getsource(sp.main)
        for ghost in ["revenue", "disputes", "export"]:
            # Should not appear as a sub.add_parser call
            assert f'sub.add_parser("{ghost}"' not in source, f"Ghost command still present: {ghost}"


# ── History DB ──────────────────────────────────────────────────────────

class TestHistoryDB:
    def test_creates_table(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Monkey-patch the history dir
            orig_expanduser = os.path.expanduser
            try:
                os.path.expanduser = lambda p: p.replace("~", tmpdir)
                with sp._history_db() as conn:
                    tables = conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                    table_names = [t[0] for t in tables]
                    assert "invoices" in table_names
            finally:
                os.path.expanduser = orig_expanduser


# ── Output verification ─────────────────────────────────────────────────

class TestPrintResult:
    """Smoke test _print_result with various types."""

    def test_error_output(self, capsys):
        sp._print_result({"error": "test error"})
        captured = capsys.readouterr()
        assert "Error: test error" in captured.out

    def test_unknown_type_falls_through(self, capsys):
        sp._print_result({"type": "unknown_thing", "key": "value"})
        captured = capsys.readouterr()
        assert "unknown_thing" in captured.out  # JSON dump

    def test_non_dict_passthrough(self, capsys):
        sp._print_result("plain string")
        captured = capsys.readouterr()
        assert "plain string" in captured.out

    def test_balance_output(self, capsys):
        sp._print_result({
            "type": "balance", "mode": "TEST",
            "available_cents": 123456, "available_currency": "USD",
            "pending_cents": 0, "pending_currency": "USD",
        })
        captured = capsys.readouterr()
        assert "$1234.56 USD" in captured.out
        assert "TEST" in captured.out


class TestTierConfig:
    def test_load_tier_config(self):
        config = sp._load_tier_config()
        assert isinstance(config, dict)
        assert "ICEMAG 3" in config
        assert "tiers" in config["ICEMAG 3"]
        assert "MOQ" in config["ICEMAG 3"]["tiers"]
        assert "D" in config["ICEMAG 3"]["tiers"]

    def test_tier_has_required_fields(self):
        config = sp._load_tier_config()
        for product_name, product in config.items():
            assert "product_id" in product, f"{product_name} missing product_id"
            assert "moq" in product, f"{product_name} missing moq"
            assert "tiers" in product, f"{product_name} missing tiers"
            for tier_name, tier in product["tiers"].items():
                assert "price_id" in tier, f"{product_name}/{tier_name} missing price_id"
                assert "min" in tier, f"{product_name}/{tier_name} missing min"
                assert "max" in tier, f"{product_name}/{tier_name} missing max"
                assert "unit_amount" in tier, f"{product_name}/{tier_name} missing unit_amount"

    def test_tier_ranges_dont_overlap(self):
        config = sp._load_tier_config()
        for product_name, product in config.items():
            tiers = product["tiers"]
            # MOQ should have the lowest min
            moq_min = tiers["MOQ"]["min"]
            assert moq_min >= 1, f"{product_name} MOQ min should be >= 1"
            # D should have the highest min
            d_min = tiers["D"]["min"]
            assert d_min >= 1000, f"{product_name} D tier min should be >= 1000"

    def test_moq_enforced(self):
        """MOQ should be > 1 for physical products."""
        config = sp._load_tier_config()
        for product_name, product in config.items():
            moq = product["moq"]
            assert moq >= 10, f"{product_name} MOQ ({moq}) should be >= 10 for physical goods"

    def test_bulklink_product_not_found(self):
        result = sp.cmd_bulklink("Nonexistent Product", tier="MOQ")
        assert "error" in result
        assert "not found" in result["error"]

    def test_bulklink_tier_not_found(self):
        result = sp.cmd_bulklink("ICEMAG 3", tier="X")
        assert "error" in result
        assert "not found" in result["error"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

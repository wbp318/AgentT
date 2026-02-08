"""Tests for expense categorizer."""

import pytest
from unittest.mock import patch, MagicMock

from modules.quickbooks.categorizer import ExpenseCategorizer
from core.events import EventBus


@pytest.fixture
def categorizer():
    """Create an ExpenseCategorizer with a mock event bus."""
    cat = ExpenseCategorizer()
    cat.setup(EventBus())
    return cat


class TestVendorLookup:
    """Test categorization via vendor mapping table."""

    def test_known_vendor_returns_vendor_lookup(self, categorizer):
        """A known vendor should return source='vendor_lookup' with confidence 1.0."""
        with patch("modules.quickbooks.categorizer.get_category_for_vendor") as mock_get:
            mock_get.return_value = "chemicals"
            result = categorizer.categorize(vendor_name="Helena Chemical")

        assert result["category"] == "chemicals"
        assert result["source"] == "vendor_lookup"
        assert result["confidence"] == 1.0
        assert result["qb_account"] == "Chemicals"

    def test_unknown_vendor_falls_back_to_claude(self, categorizer):
        """An unknown vendor should fall back to Claude API."""
        with patch("modules.quickbooks.categorizer.get_category_for_vendor") as mock_get:
            mock_get.return_value = None
            with patch.object(categorizer, "_classify_with_claude") as mock_claude:
                mock_claude.return_value = {
                    "category": "seeds_plants",
                    "qb_account": "Seeds & Plants",
                    "confidence": 0.85,
                    "source": "claude_api",
                }
                result = categorizer.categorize(vendor_name="Unknown Seed Co")

        assert result["source"] == "claude_api"
        assert result["category"] == "seeds_plants"
        mock_claude.assert_called_once()

    def test_empty_vendor_falls_back(self, categorizer):
        """Empty vendor name should go to Claude API."""
        with patch.object(categorizer, "_classify_with_claude") as mock_claude:
            mock_claude.return_value = {
                "category": "other_expenses",
                "qb_account": "Other Farm Expenses",
                "confidence": 0.3,
                "source": "claude_api",
            }
            result = categorizer.categorize(vendor_name="")

        mock_claude.assert_called_once()


class TestClaudeAPIFallback:
    """Test Claude API classification behavior."""

    def test_claude_api_success(self, categorizer):
        """Successful Claude API call returns correct structure."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock()]
        mock_response.content[0].text = '{"category": "gasoline_fuel_oil", "confidence": 0.9, "reasoning": "Shell is a gas station"}'

        with patch("modules.quickbooks.categorizer.get_category_for_vendor", return_value=None):
            with patch("modules.quickbooks.categorizer.anthropic") as mock_anthropic:
                mock_client = MagicMock()
                mock_anthropic.Anthropic.return_value = mock_client
                mock_client.messages.create.return_value = mock_response

                result = categorizer.categorize(vendor_name="Shell Gas Station")

        assert result["category"] == "gasoline_fuel_oil"
        assert result["source"] == "claude_api"
        assert result["confidence"] == 0.9

    def test_claude_api_error_returns_fallback(self, categorizer):
        """API errors should return fallback category."""
        with patch("modules.quickbooks.categorizer.get_category_for_vendor", return_value=None):
            with patch("modules.quickbooks.categorizer.anthropic") as mock_anthropic:
                mock_anthropic.APIError = Exception
                mock_client = MagicMock()
                mock_anthropic.Anthropic.return_value = mock_client
                mock_client.messages.create.side_effect = Exception("API down")

                result = categorizer.categorize(vendor_name="Mystery Vendor")

        assert result["category"] == "other_expenses"
        assert result["source"] == "fallback"
        assert result["confidence"] == 0.0

    def test_income_type_uses_income_categories(self, categorizer):
        """Income transactions should use income categories."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock()]
        mock_response.content[0].text = '{"category": "grain_sales", "confidence": 0.95, "reasoning": "Grain sale"}'

        with patch("modules.quickbooks.categorizer.get_category_for_vendor", return_value=None):
            with patch("modules.quickbooks.categorizer.anthropic") as mock_anthropic:
                mock_client = MagicMock()
                mock_anthropic.Anthropic.return_value = mock_client
                mock_client.messages.create.return_value = mock_response

                result = categorizer.categorize(
                    vendor_name="ADM",
                    transaction_type="income",
                )

        assert result["category"] == "grain_sales"
        assert result["qb_account"] == "Grain Sales"


class TestLearnVendor:
    """Test vendor learning functionality."""

    def test_learn_vendor_calls_save(self, categorizer):
        """learn_vendor should save to the database."""
        with patch("modules.quickbooks.categorizer.save_vendor_mapping") as mock_save:
            categorizer.learn_vendor("New Chemical Co", "chemicals")
            mock_save.assert_called_once_with("New Chemical Co", "chemicals", source="manual")

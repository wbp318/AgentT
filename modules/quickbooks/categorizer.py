"""
Expense categorizer for AgentT.
Maps vendor names and document context to Schedule F categories and QB accounts.
Uses vendor mapping table first, falls back to Claude API for unknowns.
"""

import json
import logging

import anthropic

from config.settings import ANTHROPIC_API_KEY, CATEGORIZATION_MODEL
from config.entities import FARM_EXPENSE_CATEGORIES, FARM_INCOME_CATEGORIES
from config.qb_accounts import (
    get_qb_account,
    get_category_for_vendor,
    save_vendor_mapping,
    EXPENSE_CATEGORY_TO_QB_ACCOUNT,
    INCOME_CATEGORY_TO_QB_ACCOUNT,
)

logger = logging.getLogger(__name__)

CATEGORIZATION_PROMPT = """You are a farm accountant categorizing transactions for Schedule F tax reporting.

Given the following transaction details, determine the most appropriate Schedule F category.

Transaction details:
- Vendor: {vendor_name}
- Description: {description}
- Amount: ${amount:.2f}
- Transaction type: {transaction_type}

{doc_text_section}

Available {transaction_type} categories:
{categories}

Respond with ONLY valid JSON:
{{
    "category": "category_slug_from_list_above",
    "confidence": 0.0 to 1.0,
    "reasoning": "brief explanation"
}}
"""


class ExpenseCategorizer:
    """
    Categorizes expenses/income using vendor lookup + Claude API fallback.
    NOT an event handler — called by web routes as a service.
    """

    def setup(self, event_bus):
        self._event_bus = event_bus

    def categorize(self, vendor_name, description="", amount=0.0,
                   document_text="", transaction_type="expense"):
        """Categorize a transaction.

        Args:
            vendor_name: Vendor or customer name
            description: Transaction description
            amount: Dollar amount
            document_text: OCR text from source document (optional)
            transaction_type: "expense" or "income"

        Returns:
            dict with keys: category, qb_account, confidence, source
        """
        # 1. Check vendor mapping table
        if vendor_name:
            category = get_category_for_vendor(vendor_name)
            if category:
                qb_account = get_qb_account(category, transaction_type)
                return {
                    "category": category,
                    "qb_account": qb_account,
                    "confidence": 1.0,
                    "source": "vendor_lookup",
                }

        # 2. Fall back to Claude API
        return self._classify_with_claude(
            vendor_name, description, amount, document_text, transaction_type
        )

    def _classify_with_claude(self, vendor_name, description, amount,
                              document_text, transaction_type):
        """Use Claude API to classify a transaction."""
        if transaction_type == "income":
            categories = FARM_INCOME_CATEGORIES
            category_map = INCOME_CATEGORY_TO_QB_ACCOUNT
        else:
            categories = FARM_EXPENSE_CATEGORIES
            category_map = EXPENSE_CATEGORY_TO_QB_ACCOUNT

        categories_list = "\n".join(f"- {cat}" for cat in categories)

        doc_text_section = ""
        if document_text:
            doc_text_section = f"Document text (first 4000 chars):\n{document_text[:4000]}"

        prompt = CATEGORIZATION_PROMPT.format(
            vendor_name=vendor_name or "Unknown",
            description=description or "No description",
            amount=amount,
            transaction_type=transaction_type,
            doc_text_section=doc_text_section,
            categories=categories_list,
        )

        try:
            client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
            response = client.messages.create(
                model=CATEGORIZATION_MODEL,
                max_tokens=300,
                messages=[{"role": "user", "content": prompt}],
            )

            result_text = response.content[0].text.strip()
            if result_text.startswith("```"):
                result_text = result_text.split("```")[1]
                if result_text.startswith("json"):
                    result_text = result_text[4:]
            result = json.loads(result_text)

            category = result.get("category", "other_expenses")
            # Validate category is in our list
            if category not in categories:
                category = "other_expenses" if transaction_type == "expense" else "other_farm_income"

            confidence = float(result.get("confidence", 0.5))
            qb_account = get_qb_account(category, transaction_type)

            logger.info(
                f"Claude categorized '{vendor_name}' as '{category}' "
                f"(confidence: {confidence:.0%}): {result.get('reasoning', '')}"
            )

            return {
                "category": category,
                "qb_account": qb_account,
                "confidence": confidence,
                "source": "claude_api",
                "reasoning": result.get("reasoning", ""),
            }

        except (json.JSONDecodeError, IndexError, KeyError) as e:
            logger.error(f"Failed to parse categorization response: {e}")
        except anthropic.APIError as e:
            logger.error(f"Claude API error during categorization: {e}")
        except Exception as e:
            logger.error(f"Unexpected error during categorization: {e}")

        # Default fallback
        default_cat = "other_expenses" if transaction_type == "expense" else "other_farm_income"
        return {
            "category": default_cat,
            "qb_account": get_qb_account(default_cat, transaction_type),
            "confidence": 0.0,
            "source": "fallback",
        }

    def learn_vendor(self, vendor_name, category_slug):
        """Save a vendor-to-category mapping. Called explicitly by user."""
        save_vendor_mapping(vendor_name, category_slug, source="manual")
        logger.info(f"Learned vendor mapping: {vendor_name} -> {category_slug}")

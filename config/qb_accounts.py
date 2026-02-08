"""
QuickBooks account mappings for AgentT.
Maps Schedule F categories to QB account names, and common vendors to categories.
"""

import logging
from database.db import get_session
from config.entities import FARM_EXPENSE_CATEGORIES, FARM_INCOME_CATEGORIES

logger = logging.getLogger(__name__)

# Schedule F expense category -> QB account name
EXPENSE_CATEGORY_TO_QB_ACCOUNT = {
    "car_truck_expenses": "Car & Truck Expenses",
    "chemicals": "Chemicals",
    "conservation_expenses": "Conservation Expenses",
    "custom_hire": "Custom Hire",
    "depreciation": "Depreciation",
    "employee_benefit_programs": "Employee Benefit Programs",
    "feed": "Feed",
    "fertilizers_lime": "Fertilizers & Lime",
    "freight_trucking": "Freight & Trucking",
    "gasoline_fuel_oil": "Gasoline, Fuel & Oil",
    "insurance": "Insurance",
    "interest_mortgage": "Interest - Mortgage",
    "interest_other": "Interest - Other",
    "labor_hired": "Labor Hired",
    "pension_profit_sharing": "Pension & Profit-Sharing",
    "rent_machinery_equipment": "Rent - Machinery & Equipment",
    "rent_land_animals": "Rent - Land & Animals",
    "repairs_maintenance": "Repairs & Maintenance",
    "seeds_plants": "Seeds & Plants",
    "storage_warehousing": "Storage & Warehousing",
    "supplies": "Supplies",
    "taxes": "Taxes",
    "utilities": "Utilities",
    "veterinary_breeding_medicine": "Veterinary, Breeding & Medicine",
    "other_expenses": "Other Farm Expenses",
}

# Schedule F income category -> QB account name
INCOME_CATEGORY_TO_QB_ACCOUNT = {
    "grain_sales": "Grain Sales",
    "livestock_sales_purchased": "Livestock Sales - Purchased",
    "livestock_sales_raised": "Livestock Sales - Raised",
    "cooperative_distributions": "Cooperative Distributions",
    "agricultural_program_payments": "Agricultural Program Payments",
    "ccc_loans_reported": "CCC Loans Reported",
    "ccc_loans_forfeited": "CCC Loans Forfeited",
    "crop_insurance_proceeds": "Crop Insurance Proceeds",
    "custom_hire_income": "Custom Hire Income",
    "other_farm_income": "Other Farm Income",
}

# Default balance sheet accounts
DEFAULT_ACCOUNTS = {
    "accounts_payable": "Accounts Payable",
    "checking": "Checking",
}

# Seed data: common farm vendors -> expense/income category slugs
VENDOR_CATEGORY_DEFAULTS = {
    "helena chemical": "chemicals",
    "helena agri-enterprises": "chemicals",
    "corteva agriscience": "chemicals",
    "basf": "chemicals",
    "syngenta": "chemicals",
    "bayer cropscience": "chemicals",
    "fmc corporation": "chemicals",
    "pioneer": "seeds_plants",
    "dekalb": "seeds_plants",
    "asgrow": "seeds_plants",
    "channel seeds": "seeds_plants",
    "shell": "gasoline_fuel_oil",
    "exxon": "gasoline_fuel_oil",
    "chevron": "gasoline_fuel_oil",
    "valero": "gasoline_fuel_oil",
    "marathon": "gasoline_fuel_oil",
    "entergy": "utilities",
    "cleco": "utilities",
    "swepco": "utilities",
    "at&t": "utilities",
    "john deere financial": "rent_machinery_equipment",
    "cnh industrial": "rent_machinery_equipment",
    "agco finance": "rent_machinery_equipment",
    "farm plan": "repairs_maintenance",
    "napa auto parts": "repairs_maintenance",
    "tractor supply": "supplies",
    "fastenal": "supplies",
    "progressive insurance": "insurance",
    "rain and hail": "insurance",
    "crop risk services": "insurance",
    "fedex": "freight_trucking",
    "ups": "freight_trucking",
    "farm bureau insurance": "insurance",
    "nutrien ag solutions": "fertilizers_lime",
    "mosaic": "fertilizers_lime",
    "cf industries": "fertilizers_lime",
}


def get_qb_account(category_slug, transaction_type="expense"):
    """Get the QB account name for a category slug.

    Args:
        category_slug: Schedule F category slug (e.g. "chemicals")
        transaction_type: "expense" or "income"

    Returns:
        QB account name string, or None if not found
    """
    if transaction_type == "income":
        return INCOME_CATEGORY_TO_QB_ACCOUNT.get(category_slug)
    return EXPENSE_CATEGORY_TO_QB_ACCOUNT.get(category_slug)


def get_category_for_vendor(vendor_name):
    """Look up the category for a vendor name.

    Checks the VendorMapping DB table first, falls back to VENDOR_CATEGORY_DEFAULTS.

    Args:
        vendor_name: Vendor name string

    Returns:
        Category slug string, or None if no mapping exists
    """
    from database.models import VendorMapping

    vendor_lower = vendor_name.strip().lower()

    # Check DB first
    try:
        with get_session() as session:
            mapping = (
                session.query(VendorMapping)
                .filter(VendorMapping.vendor_name == vendor_lower)
                .first()
            )
            if mapping:
                return mapping.category_slug
    except Exception as e:
        logger.warning(f"Error querying vendor mapping: {e}")

    # Fall back to defaults
    return VENDOR_CATEGORY_DEFAULTS.get(vendor_lower)


def save_vendor_mapping(vendor_name, category_slug, source="manual"):
    """Save or update a vendor-to-category mapping in the database.

    Args:
        vendor_name: Vendor name (will be lowercased for storage)
        category_slug: Schedule F category slug
        source: How this mapping was created ("manual", "claude_api", "csv_import", "seed")
    """
    from database.models import VendorMapping
    from datetime import datetime

    vendor_lower = vendor_name.strip().lower()
    vendor_display = vendor_name.strip()

    with get_session() as session:
        existing = (
            session.query(VendorMapping)
            .filter(VendorMapping.vendor_name == vendor_lower)
            .first()
        )
        if existing:
            existing.category_slug = category_slug
            existing.source = source
            existing.updated_at = datetime.utcnow()
        else:
            mapping = VendorMapping(
                vendor_name=vendor_lower,
                vendor_display_name=vendor_display,
                category_slug=category_slug,
                source=source,
            )
            session.add(mapping)

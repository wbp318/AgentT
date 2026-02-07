"""
Entity definitions for the farm operations.
Customize these with your actual entity names and details.
"""

ENTITIES = {
    "farm_1": {
        "name": "Farm Entity 1",
        "entity_type": "row_crop_farm",
        "state": "LA",
        "accounting_method": "cash",
        "crops": ["corn", "soybeans", "cotton"],
        "filing_keywords": [],  # Keywords in documents that identify this entity
    },
    "farm_2": {
        "name": "Farm Entity 2",
        "entity_type": "row_crop_farm",
        "state": "LA",
        "accounting_method": "cash",
        "crops": ["corn", "soybeans"],
        "filing_keywords": [],
    },
    "ga_real_estate": {
        "name": "GA Real Estate",
        "entity_type": "real_estate",
        "state": "GA",
        "accounting_method": "accrual",
        "crops": [],
        "filing_keywords": ["georgia", "GA"],
    },
}

# Document types the system recognizes
DOCUMENT_TYPES = [
    "invoice",
    "receipt",
    "bank_statement",
    "lease",
    "contract",
    "fsa_form",
    "tax_document",
    "insurance",
    "utility_bill",
    "correspondence",
    "unknown",
]

# Schedule F expense categories (from tax_assistant)
FARM_EXPENSE_CATEGORIES = [
    "car_truck_expenses",
    "chemicals",
    "conservation_expenses",
    "custom_hire",
    "depreciation",
    "employee_benefit_programs",
    "feed",
    "fertilizers_lime",
    "freight_trucking",
    "gasoline_fuel_oil",
    "insurance",
    "interest_mortgage",
    "interest_other",
    "labor_hired",
    "pension_profit_sharing",
    "rent_machinery_equipment",
    "rent_land_animals",
    "repairs_maintenance",
    "seeds_plants",
    "storage_warehousing",
    "supplies",
    "taxes",
    "utilities",
    "veterinary_breeding_medicine",
    "other_expenses",
]

# Schedule F income categories
FARM_INCOME_CATEGORIES = [
    "grain_sales",
    "livestock_sales_purchased",
    "livestock_sales_raised",
    "cooperative_distributions",
    "agricultural_program_payments",
    "ccc_loans_reported",
    "ccc_loans_forfeited",
    "crop_insurance_proceeds",
    "custom_hire_income",
    "other_farm_income",
]

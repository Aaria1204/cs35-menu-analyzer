"""
allergens.py
------------
Tiered allergen dictionary for the Menu Analyzer.

Structure rationale:
    - DIRECT: obvious mentions. A keyword match here is enough to flag UNSAFE.
    - DERIVATIVES: hidden but deterministic — if it's present, the dish contains the allergen.
    - AMBIGUOUS: depends on preparation or restaurant. Flag as UNCERTAIN, not UNSAFE,
      and surface the reason so the user can ask the restaurant.
    - CONTEXT_CLUES: phrases that hint at allergen presence without naming it directly.
      These are soft signals — weight them lower than direct matches.

Why this structure matters:
    Your keyword matcher should return (verdict, reason, tier_hit) so you can later
    measure where it fails. "Easy" cases are the DIRECT tier. The ML/LLM upgrade
    earns its keep on the DERIVATIVES and AMBIGUOUS tiers.
"""

from typing import Optional

DAIRY = {
    "direct": [
        # Obvious — most users would catch these themselves
        "milk",
        "cream",
        "heavy cream",
        "half and half",
        "cheese",
        "butter",
        "yogurt",
        "ice cream",
        "sour cream",
        "cream cheese",
        "condensed milk",
        "evaporated milk",
        "buttermilk",
    ],
    "derivatives": [
        # Hidden but deterministic — if present, dairy is present
        "ghee",                # clarified butter (common in Indian cuisine)
        "casein",              # milk protein — appears in processed foods
        "caseinate",
        "whey",                # milk byproduct — common in baked goods, sauces
        "lactose",
        "lactalbumin",
        "lactoglobulin",
        "curd",                # paneer, cottage cheese base
        "paneer",              # Indian cheese
        "ricotta",
        "mascarpone",
        "mozzarella",
        "parmesan",
        "parmigiano",
        "pecorino",
        "gouda",
        "cheddar",
        "feta",
        "brie",
        "camembert",
        "gruyere",
        "halloumi",
        "queso",
        "queso fresco",
        "cotija",
        "crema",
        "crème fraîche",
        "creme fraiche",
        "clotted cream",
        "gelato",
        "custard",
        "béchamel",
        "bechamel",
        "alfredo",             # sauce is cream + butter + cheese
        "carbonara",           # traditionally has cheese (pecorino/parmesan)
        "au gratin",           # implies cheese
        "au poivre",           # often finished with cream
        "quiche",              # egg + cream + cheese
        "tzatziki",            # yogurt-based
        "labneh",              # strained yogurt
        "kefir",
        "ranch",               # ranch dressing contains buttermilk
        "caesar",              # caesar dressing typically has parmesan (and anchovies)
    ],
    "ambiguous": [
        # Depends on preparation — flag UNCERTAIN, ask the restaurant
        "mashed potatoes",     # often has butter/milk but can be made without
        "scrambled eggs",      # sometimes cooked in butter, sometimes has milk
        "omelet",              # same
        "pancakes",            # almost always milk/butter but not guaranteed
        "waffles",
        "french toast",
        "bread",               # some breads contain milk/butter
        "biscuits",            # southern biscuits use buttermilk
        "croissant",           # contains butter but variants exist
        "risotto",             # traditionally finished with butter/parmesan
        "polenta",             # often finished with butter/cheese
        "pesto",                # traditional pesto has parmesan/pecorino
        "soup",                # cream soups are common; broths usually not
        "bisque",              # usually cream-based
        "chowder",             # usually cream-based
        "sauce",                # generic — many sauces are cream-based
        "gravy",                # some gravies use milk/butter
        "sautéed",              # often sautéed in butter
        "sauteed",
        "pan-fried",
        "glazed",               # sometimes a butter glaze
        "mashed",
        "creamy",               # could be dairy or a plant-based cream
    ],
    "context_clues": [
        # Phrases that strongly suggest dairy without naming it
        "house-made butter",
        "finished with butter",
        "topped with cheese",
        "cheesy",
        "milky",
        "buttery",
        "with a dollop of",     # usually sour cream or cream
        "drizzled with",         # often cream/yogurt based
        "cream-based",
        "dairy",                # explicit mention
        "three-cheese",
        "four-cheese",
        "cheese blend",
    ],
}

# Convenience: flat lookup of all dairy terms → tier
# Useful when you want a fast "is this term anywhere in our dictionary" check.
DAIRY_FLAT = {}
for tier, terms in DAIRY.items():
    for term in terms:
        DAIRY_FLAT[term.lower()] = tier


# Placeholder for future restrictions. Keep the same structure so the matcher
# code doesn't need to change when you add gluten / nuts / halal later.
ALLERGENS = {
    "dairy": DAIRY,
    # "gluten": GLUTEN,
    # "nuts": NUTS,
    # "halal": HALAL,
}


def get_tier(term: str, restriction: str = "dairy") -> Optional[str]:
    """Return which tier a term belongs to, or None if not in the dictionary."""
    term = term.lower().strip()
    restriction_dict = ALLERGENS.get(restriction, {})
    for tier, terms in restriction_dict.items():
        if term in [t.lower() for t in terms]:
            return tier
    return None


if __name__ == "__main__":
    # Quick sanity check
    print(f"Dairy direct terms: {len(DAIRY['direct'])}")
    print(f"Dairy derivatives: {len(DAIRY['derivatives'])}")
    print(f"Dairy ambiguous: {len(DAIRY['ambiguous'])}")
    print(f"Dairy context clues: {len(DAIRY['context_clues'])}")
    print(f"Total dairy terms: {len(DAIRY_FLAT)}")
    print()
    print(f"'ghee' is in tier: {get_tier('ghee')}")
    print(f"'mashed potatoes' is in tier: {get_tier('mashed potatoes')}")
    print(f"'grilled chicken' is in tier: {get_tier('grilled chicken')}")


    
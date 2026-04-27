"""
spoonacular_test.py — one-off exploration script (NOT integrated with the notebook).

Samples 30 dishes (stratified across all menus in ./menus/) and queries
Spoonacular's /recipes/complexSearch?addRecipeInformation=true endpoint to see
whether it returns useful dietary booleans (vegetarian/vegan/dairyFree/glutenFree)
for restaurant dish names + descriptions.

This is a feasibility test only. It does NOT compare against the keyword matcher,
does NOT cache to disk, and does NOT modify menu_analyzer.ipynb.

Usage:
    export SPOONACULAR_API_KEY='your_key_here'
    python3 spoonacular_test.py
"""

import json
import os
import random
import sys
import time
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
MENUS_DIR = Path("/Users/aariachandwani/Downloads/CS35_Final_Project/menus")
TOTAL_SAMPLE_SIZE = 30
RANDOM_SEED = 42
ENDPOINT = "https://api.spoonacular.com/recipes/complexSearch"
TIMEOUT_SECS = 15


# ---------------------------------------------------------------------------
# 1. Read API key
# ---------------------------------------------------------------------------
API_KEY = os.environ.get("SPOONACULAR_API_KEY")
if not API_KEY:
    print("ERROR: environment variable SPOONACULAR_API_KEY is not set.")
    print("       Set it before running, e.g.:")
    print("           export SPOONACULAR_API_KEY='your_key_here'")
    sys.exit(1)


# ---------------------------------------------------------------------------
# 2. Load menus, flatten to (restaurant, dish, description) tuples
# ---------------------------------------------------------------------------
def load_menu_dishes(menus_dir: Path):
    by_restaurant: dict[str, list[tuple[str, str]]] = {}
    for fp in sorted(menus_dir.glob("*.json")):
        with open(fp) as f:
            menu = json.load(f)
        restaurant = menu.get("restaurant_name", fp.stem)
        for section in menu.get("sections", []):
            for item in section.get("items", []):
                name = (item.get("name") or "").strip()
                desc = (item.get("description") or "").strip()
                if not name:
                    continue
                by_restaurant.setdefault(restaurant, []).append((name, desc))
    return by_restaurant


# ---------------------------------------------------------------------------
# 3 & 4. Stratified deterministic sampling
# ---------------------------------------------------------------------------
def stratified_sample(by_restaurant, total_target, seed):
    rng = random.Random(seed)
    n_restaurants = len(by_restaurant) or 1
    per_restaurant = max(1, total_target // n_restaurants)
    sampled = []
    for restaurant in sorted(by_restaurant.keys()):
        dishes = by_restaurant[restaurant]
        picks = dishes if len(dishes) <= per_restaurant else rng.sample(dishes, per_restaurant)
        sampled.extend((restaurant, name, desc) for (name, desc) in picks)
    # Hard cap at TOTAL_SAMPLE_SIZE in case rounding pushed us over
    return sampled[:total_target]


# ---------------------------------------------------------------------------
# 5. Query Spoonacular
# ---------------------------------------------------------------------------
def query_spoonacular(title: str):
    """Return (parsed_json_or_None, status_code_or_None, headers_dict, elapsed_secs, error_str_or_None)."""
    params = {
        "query": title,
        "number": 1,
        "addRecipeInformation": "true",
        "apiKey": API_KEY,
    }
    t0 = time.monotonic()
    try:
        resp = requests.get(ENDPOINT, params=params, timeout=TIMEOUT_SECS)
    except requests.exceptions.RequestException as exc:
        return None, None, {}, time.monotonic() - t0, f"NETWORK: {exc}"
    elapsed = time.monotonic() - t0
    headers = dict(resp.headers)
    if resp.status_code != 200:
        return None, resp.status_code, headers, elapsed, f"HTTP {resp.status_code}"
    try:
        return resp.json(), 200, headers, elapsed, None
    except ValueError as exc:
        return None, 200, headers, elapsed, f"JSON_DECODE: {exc}"


def extract_classification(data):
    """Pull dietary booleans + any allergen-ish field from a complexSearch response."""
    if not data:
        return None
    results = data.get("results") or []
    if not results:
        return None
    r = results[0]
    return {
        "matched_title": r.get("title"),
        "vegetarian": r.get("vegetarian"),
        "vegan": r.get("vegan"),
        "dairyFree": r.get("dairyFree"),
        "glutenFree": r.get("glutenFree"),
        # complexSearch doesn't surface a clean allergens array;
        # record any closest fields if present.
        "intolerances": r.get("intolerances"),
        "diets": r.get("diets"),
    }


# ---------------------------------------------------------------------------
# 6 + 7 + 8. Main: query, table, summary
# ---------------------------------------------------------------------------
def fmt_bool(v):
    if v is True:
        return "T"
    if v is False:
        return "F"
    return "?"


def fmt_allergens(cl):
    if not cl:
        return "-"
    bits = []
    if cl.get("diets"):
        bits.append(f"diets={cl['diets']}")
    if cl.get("intolerances"):
        bits.append(f"intol={cl['intolerances']}")
    return ", ".join(bits) if bits else "—"


def main():
    print(f"Loading menus from {MENUS_DIR}…")
    by_restaurant = load_menu_dishes(MENUS_DIR)
    total_dishes = sum(len(v) for v in by_restaurant.values())
    n_restaurants = len(by_restaurant)
    print(f"Loaded {total_dishes} dishes across {n_restaurants} restaurants:")
    for r, items in sorted(by_restaurant.items()):
        print(f"  - {r}: {len(items)} dishes")
    print()

    sample = stratified_sample(by_restaurant, TOTAL_SAMPLE_SIZE, RANDOM_SEED)
    per_r = TOTAL_SAMPLE_SIZE // max(1, n_restaurants)
    print(f"Sampled {len(sample)} dishes "
          f"(target={TOTAL_SAMPLE_SIZE}, ~{per_r} per restaurant, seed={RANDOM_SEED}).\n")

    # Table header
    print(f"{'Restaurant':<18} {'Dish':<42} {'veg':<3} {'vgn':<3} {'dF':<3} {'gF':<3} "
          f"{'Allergens/diets':<28} {'HTTP':<5}")
    print("-" * 116)

    results = []
    rate_limited = False
    last_quota_used = None
    last_quota_left = None
    last_quota_request = None

    for restaurant, dish, desc in sample:
        if rate_limited:
            break
        title = (dish + " " + desc).strip()
        data, status, headers, elapsed, err = query_spoonacular(title)

        # Update rate-limit tracking from any response we got
        last_quota_used = headers.get("X-API-Quota-Used", last_quota_used)
        last_quota_left = headers.get("X-API-Quota-Left", last_quota_left)
        last_quota_request = headers.get("X-API-Quota-Request", last_quota_request)

        cl = extract_classification(data)
        results.append({
            "restaurant": restaurant,
            "dish": dish,
            "status": status,
            "elapsed": elapsed,
            "error": err,
            "classification": cl,
        })

        dish_short = (dish[:39] + "…") if len(dish) > 40 else dish
        rest_short = (restaurant[:17] + "…") if len(restaurant) > 18 else restaurant
        if cl:
            row = (
                f"{rest_short:<18} {dish_short:<42} "
                f"{fmt_bool(cl['vegetarian']):<3} {fmt_bool(cl['vegan']):<3} "
                f"{fmt_bool(cl['dairyFree']):<3} {fmt_bool(cl['glutenFree']):<3} "
                f"{fmt_allergens(cl)[:28]:<28} {str(status):<5}"
            )
        else:
            row = (
                f"{rest_short:<18} {dish_short:<42} "
                f"{'-':<3} {'-':<3} {'-':<3} {'-':<3} "
                f"{('FAILED: ' + (err or '?'))[:28]:<28} {str(status if status is not None else '-'):<5}"
            )
        print(row)

        # 9. Stop on rate limit
        if status == 429:
            print(f"\n!! 429 RATE LIMIT hit on dish: {dish!r} (restaurant: {restaurant})")
            print("   Stopping further API calls. Partial results are above.")
            rate_limited = True

    # 8. Summary
    total_calls = len(results)
    successes = [r for r in results if r["classification"] is not None]
    failures = [r for r in results if r["classification"] is None]
    avg_elapsed = (sum(r["elapsed"] for r in results) / total_calls) if total_calls else 0.0

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Total API calls            : {total_calls}")
    print(f"Successful classifications : {len(successes)}")
    print(f"Failures                   : {len(failures)}")
    print(f"Avg response time          : {avg_elapsed:.2f}s")
    print(f"X-API-Quota-Used  (last)   : {last_quota_used or '— not in response headers'}")
    print(f"X-API-Quota-Left  (last)   : {last_quota_left or '— not in response headers'}")
    print(f"X-API-Quota-Request (last) : {last_quota_request or '— not in response headers'}")


if __name__ == "__main__":
    main()

"""
parse_ingredients_test.py — second exploratory probe.

Tests Spoonacular's POST /recipes/parseIngredients on 15 dishes, stratified
across description styles (slash-list / prose / empty) so we can see which
formats the API can extract structured ingredients from.

Like spoonacular_test.py: one-off, not integrated, no caching.
"""

import json
import os
import random
import re
import sys
import time
from pathlib import Path

import requests

MENUS_DIR = Path("/Users/aariachandwani/Downloads/CS35_Final_Project/menus")
ENDPOINT = "https://api.spoonacular.com/recipes/parseIngredients"
TIMEOUT_SECS = 20
PER_BUCKET = 5
RANDOM_SEED = 42

API_KEY = os.environ.get("SPOONACULAR_API_KEY")
if not API_KEY:
    print("ERROR: SPOONACULAR_API_KEY not set.", file=sys.stderr)
    sys.exit(1)


def classify_style(desc: str) -> str:
    if not desc.strip():
        return "empty"
    if desc.count("/") >= 2:
        return "ingredient_list"
    return "prose_or_mixed"


def load_buckets():
    buckets = {"ingredient_list": [], "prose_or_mixed": [], "empty": []}
    for fp in sorted(MENUS_DIR.glob("*.json")):
        with open(fp) as f:
            menu = json.load(f)
        rest = menu["restaurant_name"]
        for section in menu.get("sections", []):
            for item in section.get("items", []):
                name = (item.get("name") or "").strip()
                desc = (item.get("description") or "").strip()
                if not name:
                    continue
                buckets[classify_style(desc)].append((rest, name, desc))
    return buckets


def normalize_to_ingredient_lines(text: str) -> str:
    """Turn a description into one ingredient per line — what parseIngredients expects."""
    if not text:
        return ""
    # If it's slash-separated, split on /
    if text.count("/") >= 2:
        parts = [p.strip() for p in text.split("/")]
    # If it's comma-separated and not a sentence, split on commas
    elif text.count(",") >= 2 and not re.search(r"\.\s+\w", text):
        parts = [p.strip() for p in text.split(",")]
    else:
        # Prose: leave as one block — the API does NLP on it
        parts = [text.strip()]
    parts = [p for p in parts if p]
    return "\n".join(parts)


def parse_ingredients(text: str):
    body = {"ingredientList": text, "servings": 1, "includeNutrition": "false"}
    headers_out = {"Content-Type": "application/x-www-form-urlencoded"}
    t0 = time.monotonic()
    try:
        resp = requests.post(
            f"{ENDPOINT}?apiKey={API_KEY}",
            data=body,
            headers=headers_out,
            timeout=TIMEOUT_SECS,
        )
    except requests.exceptions.RequestException as exc:
        return None, None, {}, time.monotonic() - t0, f"NETWORK: {exc}"
    elapsed = time.monotonic() - t0
    if resp.status_code != 200:
        return None, resp.status_code, dict(resp.headers), elapsed, f"HTTP {resp.status_code}: {resp.text[:120]}"
    try:
        return resp.json(), 200, dict(resp.headers), elapsed, None
    except ValueError as exc:
        return None, 200, dict(resp.headers), elapsed, f"JSON: {exc}"


def main():
    buckets = load_buckets()
    print("Bucket sizes (universe):")
    for k, v in buckets.items():
        print(f"  {k:<16} {len(v)}")
    print()

    rng = random.Random(RANDOM_SEED)
    sample = []
    for bucket in ("ingredient_list", "prose_or_mixed", "empty"):
        pool = buckets[bucket]
        picks = pool if len(pool) <= PER_BUCKET else rng.sample(pool, PER_BUCKET)
        for rest, name, desc in picks:
            sample.append((bucket, rest, name, desc))

    print(f"Probing {len(sample)} dishes ({PER_BUCKET} per bucket, seed={RANDOM_SEED})\n")

    last_left = None
    for bucket, rest, name, desc in sample:
        # For empty descriptions, use the dish name as the ingredient input
        text_in = desc if desc else name
        normalized = normalize_to_ingredient_lines(text_in)
        data, status, headers, elapsed, err = parse_ingredients(normalized)
        last_left = headers.get("X-API-Quota-Left", last_left)

        print(f"--- [{bucket:<16}] {rest} :: {name}")
        print(f"    INPUT      : {text_in[:90]}{'…' if len(text_in)>90 else ''}")
        print(f"    NORMALIZED : {normalized.replace(chr(10), ' | ')[:90]}{'…' if len(normalized)>90 else ''}")
        if err:
            print(f"    ERROR      : {err}")
            print(f"    HTTP {status} in {elapsed:.2f}s\n")
            if status == 429:
                print("!! 429 — stopping further calls.")
                break
            continue

        if not data:
            print(f"    no ingredients returned (HTTP 200, empty array)\n")
            continue

        print(f"    HTTP 200 in {elapsed:.2f}s — {len(data)} ingredient(s) parsed:")
        for ing in data:
            ing_name = ing.get("name") or ing.get("originalName") or "?"
            aisle = ing.get("aisle") or "?"
            meta = ing.get("meta") or []
            print(f"      • name={ing_name!r:<30}  aisle={aisle!r}  meta={meta}")
        print()

    print("=" * 70)
    print(f"X-API-Quota-Left (last seen): {last_left or '— not in headers'}")


if __name__ == "__main__":
    main()

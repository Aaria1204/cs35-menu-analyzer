"""
llm_classifier.py — Claude-powered dietary classifier for restaurant menu items.

This is the primary classifier for the project. The keyword matcher is dropped.

Design notes:
- System prompt carries the rules + few-shot examples (stable across all calls).
  It is marked for prompt caching via `cache_control: ephemeral`. On Sonnet 4.5
  the minimum cacheable prefix is 1024 tokens — short prompts silently won't
  cache (no error). The marker is harmless when below threshold.
- User message carries the per-call inputs (dish name + description + restrictions).
- A local disk cache (llm_cache.json) keyed by sha256(name + description +
  sorted(restrictions)) means re-running the same menu costs $0 in API calls.
- combined_verdict is computed locally — never asked of the LLM.
- The LLM is allowed to return "uncertain". Forcing it to commit when it
  doesn't know is the failure mode this whole design tries to prevent.
"""

import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import anthropic

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
ALLERGENS = ["dairy", "gluten", "nuts", "eggs", "soy", "shellfish", "fish", "sesame"]
DIETS = ["pescatarian", "vegetarian", "vegan"]

MODEL = "claude-sonnet-4-5"
MAX_TOKENS = 1024
CACHE_PATH = Path(__file__).resolve().parent / "llm_cache.json"
DELAY_BETWEEN_CALLS_SECS = 0.5


# ---------------------------------------------------------------------------
# API key check
# ---------------------------------------------------------------------------
if not os.environ.get("ANTHROPIC_API_KEY"):
    print("ERROR: environment variable ANTHROPIC_API_KEY is not set.", file=sys.stderr)
    print("       Set it before running, e.g.:", file=sys.stderr)
    print("           export ANTHROPIC_API_KEY='your_key_here'", file=sys.stderr)
    sys.exit(1)

_client = anthropic.Anthropic()


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are a dietary classifier for restaurant menu items. Given a dish name, description, and list of dietary restrictions, you return a JSON object classifying the dish against each restriction.

THREE VERDICT TIERS:
- "safe": You are confident the dish does NOT contain or derive from the restricted item.
- "unsafe": You are confident the dish DOES contain or derive from it. This includes hidden ingredients you know about from culinary tradition (e.g. anchovies in classic caesar dressing, parmesan/pecorino in pesto, fish sauce in pad thai, butter in bearnaise).
- "uncertain": The description doesn't resolve it. The right answer when:
  - A dish traditionally contains the restricted item but the description doesn't mention it (risotto traditionally finished with butter and parmesan; bolognese traditionally has milk).
  - The description is empty and the name alone isn't definitive ("Mashed potatoes" with no description — could be made with butter, could be made with olive oil).
  - Preparation varies between restaurants and you cannot tell from this menu.

CRITICAL RULE: uncertain stays uncertain if the description doesn't resolve it. Do NOT default to "safe" out of helpfulness. If you genuinely don't know, "uncertain" is the correct answer — it tells the user to ask the restaurant. Resolving an ambiguous case to "safe" because it sounds plausible is the worst possible failure mode of this system.

OUTPUT FORMAT: Raw JSON only. No preamble. No markdown code fences. No prose outside the JSON object.

Schema:
{
  "per_restriction": {
    "<restriction_name>": {
      "verdict": "safe" | "unsafe" | "uncertain",
      "reason": "1-2 sentence explanation grounded in the dish",
      "evidence": ["phrases or ingredients from the dish that support the verdict"]
    }
  }
}

The "evidence" array should cite specific phrases from the dish name or description, OR named cultural defaults (e.g. "traditional caesar dressing contains anchovies"). If you marked a verdict as "unsafe" or "uncertain", evidence must explain why.

EXAMPLE 1 — uncertain stays uncertain (this is the most important case):
Input:
  Dish name: Mashed potatoes
  Description: (empty)
  Restrictions: dairy, vegan

Output:
{"per_restriction": {"dairy": {"verdict": "uncertain", "reason": "Mashed potatoes are typically made with butter and milk, but olive-oil and dairy-free versions exist. The empty description doesn't resolve which version this restaurant serves.", "evidence": ["mashed potatoes (name)", "no description provided"]}, "vegan": {"verdict": "uncertain", "reason": "Vegan status depends on whether butter or milk is used; with no description this can't be determined.", "evidence": ["mashed potatoes (name)", "no description provided"]}}}

EXAMPLE 2 — hidden ingredient, unsafe:
Input:
  Dish name: Caesar salad
  Description: romaine, croutons, parmesan, house dressing
  Restrictions: vegetarian, dairy

Output:
{"per_restriction": {"vegetarian": {"verdict": "unsafe", "reason": "Traditional caesar dressing contains anchovies (a fish), making the salad non-vegetarian. The 'house dressing' on a caesar follows this convention unless explicitly marked otherwise.", "evidence": ["caesar salad (name)", "house dressing"]}, "dairy": {"verdict": "unsafe", "reason": "Parmesan is a cow's-milk cheese, and caesar dressing typically also contains dairy.", "evidence": ["parmesan", "house dressing"]}}}

Now classify the dish in the user message. Output JSON only."""


def _build_user_message(name: str, description: str, restrictions: List[str]) -> str:
    desc = description.strip() if description else ""
    if not desc:
        desc = "(empty)"
    return (
        f"Dish name: {name}\n"
        f"Description: {desc}\n"
        f"Restrictions: {', '.join(restrictions)}"
    )


# ---------------------------------------------------------------------------
# Disk cache
# ---------------------------------------------------------------------------
def _cache_key(name: str, description: str, restrictions: List[str]) -> str:
    payload = json.dumps(
        {"name": name, "description": description, "restrictions": sorted(restrictions)},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _load_cache() -> Dict[str, Any]:
    if not CACHE_PATH.exists():
        return {}
    try:
        with open(CACHE_PATH) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_cache(cache: Dict[str, Any]) -> None:
    tmp = CACHE_PATH.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)
    tmp.replace(CACHE_PATH)


# ---------------------------------------------------------------------------
# Local combined-verdict logic (NOT asked of the LLM)
# ---------------------------------------------------------------------------
def _combined_verdict(per_restriction: Dict[str, Dict[str, Any]]) -> str:
    verdicts = {pr.get("verdict", "uncertain") for pr in per_restriction.values()}
    if "unsafe" in verdicts:
        return "unsafe"
    if "uncertain" in verdicts:
        return "uncertain"
    return "safe"


# ---------------------------------------------------------------------------
# Single API call
# ---------------------------------------------------------------------------
def _call_claude(name: str, description: str, restrictions: List[str], stricter: bool = False) -> str:
    """One Anthropic API call. Returns raw assistant text."""
    user_msg = _build_user_message(name, description, restrictions)
    if stricter:
        user_msg += "\n\nReturn raw JSON only. No prose. No markdown code fences. No explanation outside the JSON."

    system = [
        {
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }
    ]

    response = _client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=system,
        messages=[{"role": "user", "content": user_msg}],
    )
    return next((b.text for b in response.content if b.type == "text"), "")


def _parse_json(text: str) -> Dict[str, Any]:
    """Tolerantly extract a JSON object. Strips markdown fences if present."""
    text = text.strip()
    if text.startswith("```"):
        # Strip leading ```json or ```
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    return json.loads(text)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def classify_dish(name: str, description: str, restrictions: List[str]) -> Dict[str, Any]:
    """Classify a single dish against a list of dietary restrictions."""
    cache = _load_cache()
    key = _cache_key(name, description, restrictions)
    if key in cache:
        return cache[key]

    raw = ""
    parsed: Dict[str, Any] = {}
    err: Optional[str] = None

    try:
        raw = _call_claude(name, description, restrictions)
        parsed = _parse_json(raw)
    except json.JSONDecodeError:
        # Retry once with a stricter "JSON ONLY" instruction
        try:
            raw = _call_claude(name, description, restrictions, stricter=True)
            parsed = _parse_json(raw)
        except json.JSONDecodeError as exc:
            err = f"LLM response unparseable: {exc}"
        except anthropic.APIError as exc:
            err = f"API error on retry: {exc}"
    except anthropic.APIError as exc:
        err = f"API error: {exc}"
    except Exception as exc:  # network, etc.
        err = f"unexpected error: {exc}"

    if err is not None:
        per_restriction = {
            r: {"verdict": "uncertain", "reason": err, "evidence": []}
            for r in restrictions
        }
    else:
        per_restriction = parsed.get("per_restriction", {}) or {}
        # Guarantee an entry per requested restriction even if the LLM dropped one
        for r in restrictions:
            if r not in per_restriction:
                per_restriction[r] = {
                    "verdict": "uncertain",
                    "reason": "LLM did not return a verdict for this restriction.",
                    "evidence": [],
                }

    result = {
        "name": name,
        "description": description,
        "per_restriction": per_restriction,
        "combined_verdict": _combined_verdict(per_restriction),
        "raw_response": raw,
    }

    # Cache successful results only — don't pin transient errors to disk
    if err is None:
        cache[key] = result
        _save_cache(cache)

    return result


def classify_menu(menu: Dict[str, Any], restrictions: List[str]) -> Dict[str, Any]:
    """Classify every item in every section. Preserves menu structure."""
    out = {
        "restaurant_name": menu.get("restaurant_name"),
        "cuisine": menu.get("cuisine"),
        "location": menu.get("location"),
        "sections": [],
    }
    for section in menu.get("sections", []):
        out_items = []
        for item in section.get("items", []):
            result = classify_dish(
                name=item.get("name", ""),
                description=item.get("description", ""),
                restrictions=restrictions,
            )
            out_items.append(result)
            time.sleep(DELAY_BETWEEN_CALLS_SECS)
        out["sections"].append({"name": section["name"], "items": out_items})
    return out


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------
def _format_result(result: Dict[str, Any]) -> str:
    lines = [
        f"DISH       : {result['name']}",
        f"DESCRIPTION: {result['description']!r}",
        f"COMBINED   : {result['combined_verdict']}",
        "PER RESTRICTION:",
    ]
    for r, pr in result["per_restriction"].items():
        verdict = pr.get("verdict", "?")
        reason = pr.get("reason", "")
        evidence = pr.get("evidence", [])
        lines.append(f"  {r:<14} {verdict:<10} {reason}")
        if evidence:
            lines.append(f"  {'':<14} {'':<10} evidence={evidence}")
    return "\n".join(lines)


if __name__ == "__main__":
    cases = [
        ("Grilled branzino", "with olive oil and lemon", ["dairy", "vegetarian"]),
        ("Risotto al funghi", "wild mushroom risotto", ["dairy"]),
        ("Caesar salad", "romaine, croutons, parmesan, house dressing", ["vegetarian", "dairy", "gluten"]),
        ("Mashed potatoes", "", ["dairy", "vegan"]),
        ("Pollo arrosto", "roasted chicken with rosemary and olive oil over arugula", ["dairy", "vegetarian"]),
    ]

    for i, (name, desc, restrictions) in enumerate(cases):
        label = chr(ord("a") + i)
        print(f"\n========== Test ({label}) ==========")
        result = classify_dish(name, desc, restrictions)
        print(_format_result(result))

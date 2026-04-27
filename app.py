"""
app.py — Streamlit interface for the Menu Analyzer.

This is a UI prototype against a MOCK classifier. The single swap point for
the real classifier is `classify(menu, restrictions)` near the bottom of this
file — replace its body with `llm_classify_menu(...)` or `spoonacular_classify_menu(...)`
when those exist, and the rest of the UI is unchanged.

Run with:
    streamlit run app.py
"""

import hashlib
import json
from pathlib import Path
from typing import Dict, List

import pandas as pd
import streamlit as st

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
MENUS_DIR = Path(__file__).resolve().parent / "menus"

ALLERGENS = ["dairy", "gluten", "nuts", "eggs", "soy", "shellfish", "fish", "sesame"]

DIET_BUNDLES: Dict[str, List[str]] = {
    "None":         [],
    "Pescatarian":  ["meat"],
    "Vegetarian":   ["meat", "fish", "shellfish"],
    "Vegan":        ["meat", "fish", "shellfish", "dairy", "eggs"],
}

DOT = {"safe": "🟢", "uncertain": "🟡", "unsafe": "🔴"}


# ---------------------------------------------------------------------------
# Mock classifier — deterministic, hash-based, NOT real classification
# ---------------------------------------------------------------------------
SAFE_REASONS = {
    "dairy":     "no dairy ingredients listed in the description",
    "gluten":    "no wheat or breaded components mentioned",
    "nuts":      "no nuts mentioned in the description",
    "eggs":      "no eggs mentioned",
    "soy":       "no soy products mentioned",
    "shellfish": "no shellfish in description",
    "fish":      "no fish in description",
    "sesame":    "no sesame ingredients mentioned",
    "meat":      "appears vegetarian based on description",
}
UNCERTAIN_REASONS = {
    "dairy":     "may be cooked with butter or cream — ask the server",
    "gluten":    "may have flour or breading — confirm with kitchen",
    "nuts":      "kitchen may handle nuts — cross-contamination possible",
    "eggs":      "preparation may include egg wash or egg-based ingredients",
    "soy":       "soy sauce or tofu may be hidden in the preparation",
    "shellfish": "stock or sauce may contain shellfish",
    "fish":      "fish sauce may be in the marinade or stock",
    "sesame":    "may include sesame oil or seeds",
    "meat":      "stock or fat may be animal-based",
}
UNSAFE_REASONS = {
    "dairy":     "contains cheese, cream, or butter",
    "gluten":    "contains bread, pasta, or flour",
    "nuts":      "contains nuts in the dish or sauce",
    "eggs":      "contains eggs as a primary component",
    "soy":       "contains soy sauce or tofu",
    "shellfish": "contains shrimp, crab, or other shellfish",
    "fish":      "contains fish as a primary protein",
    "sesame":    "contains sesame seeds or oil",
    "meat":      "contains meat (poultry / beef / pork / lamb)",
}


def _hash_verdict(name: str, restriction: str) -> str:
    """Deterministic ~50/30/20 split into safe/uncertain/unsafe."""
    h = int(hashlib.md5(f"{name}|{restriction}".encode()).hexdigest(), 16) % 100
    if h < 50:
        return "safe"
    if h < 80:
        return "uncertain"
    return "unsafe"


def _combined(per_restriction: Dict[str, dict]) -> str:
    verdicts = {pr["verdict"] for pr in per_restriction.values()}
    if "unsafe" in verdicts:
        return "unsafe"
    if "uncertain" in verdicts:
        return "uncertain"
    return "safe"


def mock_classify_menu(menu: dict, restrictions: List[str]) -> dict:
    """Returns the menu structure with each item annotated with verdicts.
    Uses simple deterministic rules based on dish name keywords so the UI
    has realistic-looking variety. NOT real classification.
    """
    out = {
        "restaurant_name": menu.get("restaurant_name"),
        "cuisine": menu.get("cuisine"),
        "location": menu.get("location"),
        "sections": [],
    }
    for section in menu.get("sections", []):
        items_out = []
        for item in section.get("items", []):
            name = item.get("name", "")
            description = item.get("description", "")
            per_restriction = {}
            for r in restrictions:
                v = _hash_verdict(name, r)
                if v == "safe":
                    reason = SAFE_REASONS.get(r, f"no {r} detected")
                    evidence = []
                elif v == "uncertain":
                    reason = UNCERTAIN_REASONS.get(r, f"{r} status unclear")
                    evidence = [f"{name} (name)"]
                else:
                    reason = UNSAFE_REASONS.get(r, f"contains {r}")
                    evidence = [f"{name} (name)"]
                per_restriction[r] = {"verdict": v, "reason": reason, "evidence": evidence}
            items_out.append({
                "name": name,
                "description": description,
                "per_restriction": per_restriction,
                "combined_verdict": _combined(per_restriction) if per_restriction else "safe",
            })
        out["sections"].append({"name": section["name"], "items": items_out})
    return out


# ---------------------------------------------------------------------------
# Single swap point — change this one line to point at the real classifier
# ---------------------------------------------------------------------------
def classify(menu: dict, restrictions: List[str]) -> dict:
    """All classifier calls go through here. Swap mock_classify_menu for the
    real LLM or Spoonacular classifier when they're ready."""
    return mock_classify_menu(menu, restrictions)


# ---------------------------------------------------------------------------
# Friendliness score
# ---------------------------------------------------------------------------
def compute_friendliness(classified_menu: dict, strict_mode: bool) -> dict:
    safe = uncertain = unsafe = 0
    for section in classified_menu.get("sections", []):
        for item in section.get("items", []):
            v = item["combined_verdict"]
            if v == "safe":
                safe += 1
            elif v == "uncertain":
                if strict_mode:
                    unsafe += 1
                else:
                    uncertain += 1
            else:
                unsafe += 1
    total = safe + uncertain + unsafe
    score = (safe / total * 100) if total else 0.0
    return {
        "safe_count": safe,
        "uncertain_count": uncertain,
        "unsafe_count": unsafe,
        "score_pct": score,
    }


# ---------------------------------------------------------------------------
# Loading + cached classifier wrapper
# ---------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def load_menu(filename: str) -> dict:
    with open(MENUS_DIR / filename) as f:
        return json.load(f)


def list_menu_files() -> List[str]:
    return sorted(p.name for p in MENUS_DIR.glob("*.json"))


def get_classified(menu_filename: str, restrictions: List[str], strict_mode: bool) -> dict:
    """Cached classify — keyed by (file, frozenset(restrictions), strict_mode)."""
    if "cache" not in st.session_state:
        st.session_state.cache = {}
    key = (menu_filename, frozenset(restrictions), strict_mode)
    if key not in st.session_state.cache:
        menu = load_menu(menu_filename)
        st.session_state.cache[key] = classify(menu, sorted(restrictions))
    return st.session_state.cache[key]


def dot_for(verdict: str, strict_mode: bool) -> str:
    if strict_mode and verdict == "uncertain":
        return DOT["unsafe"]
    return DOT.get(verdict, "⚪")


# ---------------------------------------------------------------------------
# Streamlit app
# ---------------------------------------------------------------------------
st.set_page_config(page_title="Menu Analyzer", page_icon="🍽️", layout="wide")

st.title("🍽️ Menu Analyzer")
st.caption("Restaurant menu screening for dietary restrictions.")

# ---- Sidebar ----
with st.sidebar:
    st.header("Restrictions")
    selected_allergens = st.multiselect("Allergens", ALLERGENS, default=[])
    selected_diet = st.radio("Diet", list(DIET_BUNDLES.keys()), index=0)
    strict_mode = st.toggle(
        "Strict mode",
        value=False,
        help="Treat 'uncertain' as 'unsafe' — useful for severe allergies.",
    )
    st.markdown("---")
    st.caption("Demo using **mock classifier** — real LLM/API integration pending.")

# Final restriction set: explicit allergens + diet bundle
restrictions = sorted(set(selected_allergens) | set(DIET_BUNDLES[selected_diet]))

# ---- Load + classify all menus ----
menu_files = list_menu_files()
if not menu_files:
    st.error(f"No menu JSONs found in {MENUS_DIR}")
    st.stop()

rows = []
classified_by_file: Dict[str, dict] = {}
for fname in menu_files:
    classified = get_classified(fname, restrictions, strict_mode)
    classified_by_file[fname] = classified
    score = compute_friendliness(classified, strict_mode)
    rows.append({
        "Restaurant": classified.get("restaurant_name") or fname,
        "Safe": score["safe_count"],
        "Uncertain": score["uncertain_count"],
        "Unsafe": score["unsafe_count"],
        "Score (%)": round(score["score_pct"], 1),
        "_file": fname,
    })

ranking_df = (
    pd.DataFrame(rows)
    .sort_values("Score (%)", ascending=False)
    .reset_index(drop=True)
)

# ---- Restaurant picker (above the table per spec) ----
restaurant_choice = st.selectbox(
    "Select a restaurant to view its menu",
    options=ranking_df["_file"].tolist(),
    format_func=lambda f: next(r for r in rows if r["_file"] == f)["Restaurant"],
)

# ---- Ranking table ----
st.subheader("Friendliness ranking")
if not restrictions:
    st.info("No restrictions selected — every dish defaults to safe. Pick allergens or a diet to see differentiation.")
st.dataframe(ranking_df.drop(columns=["_file"]), use_container_width=True, hide_index=True)

# ---- Selected menu ----
selected = classified_by_file[restaurant_choice]
st.subheader(f"Menu — {selected['restaurant_name']}")
score = compute_friendliness(selected, strict_mode)
col_a, col_b, col_c, col_d = st.columns(4)
col_a.metric("Safe",      score["safe_count"])
col_b.metric("Uncertain", score["uncertain_count"])
col_c.metric("Unsafe",    score["unsafe_count"])
col_d.metric("Score",     f"{score['score_pct']:.0f}%")

for section in selected["sections"]:
    st.markdown(f"### {section['name']}")
    for item in section["items"]:
        verdict = item["combined_verdict"]
        d = dot_for(verdict, strict_mode)
        st.markdown(f"{d} &nbsp; **{item['name']}**", unsafe_allow_html=True)
        if item["description"]:
            st.caption(item["description"])
        if item["per_restriction"]:
            with st.expander("why?", expanded=False):
                for r, pr in item["per_restriction"].items():
                    pv = pr["verdict"]
                    pd_dot = dot_for(pv, strict_mode)
                    st.markdown(f"- {pd_dot} **{r}** ({pv}): {pr.get('reason', '')}")
                    ev = pr.get("evidence") or []
                    if ev:
                        st.caption(f"evidence: {', '.join(ev)}")

# ---- About ----
with st.expander("About this demo"):
    st.markdown(
        """
This is the user-facing prototype for a dietary restriction menu analyzer
(CS35 Final Project). Goal: pick your restrictions, get a per-item verdict
on a real restaurant menu.

**Right now, classification is mocked.** Verdicts are deterministic (seeded
by a hash of the dish name + restriction) so the UI looks varied and
reproducible — but they are NOT real classifications. A dish flagged
"contains cheese" here may not actually contain cheese.

**Coming next:**
- LLM-backed classifier (Claude) — primary classifier
- Spoonacular API for ingredient-level cross-checks
- Real friendliness scores

The classifier swap point is **one line** in `app.py`: replace the body of
`classify(menu, restrictions)` with the real classifier and the rest of the
UI is unchanged.
        """
    )

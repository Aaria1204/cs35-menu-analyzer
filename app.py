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
import pydeck as pdk
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

# Hex palette — also used by the .dot-* CSS rules below.
COLOR_SAFE = "#2E7D32"        # forest green
COLOR_UNCERTAIN = "#F9A825"   # amber
COLOR_UNSAFE = "#C62828"      # deep red


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


def verdict_class(verdict: str, strict_mode: bool) -> str:
    """CSS class suffix for the colored dot. Strict mode collapses uncertain to unsafe."""
    if strict_mode and verdict == "uncertain":
        return "unsafe"
    return verdict


def score_to_color(score_pct: float) -> str:
    """Bucket score into the same 3 colors used elsewhere in the UI."""
    if score_pct >= 70:
        return COLOR_SAFE
    if score_pct >= 40:
        return COLOR_UNCERTAIN
    return COLOR_UNSAFE


def score_to_radius_px(score_pct: float) -> float:
    """Pin radius in pixels. Higher score = larger pin. Range 8–20px."""
    s = max(0.0, min(100.0, score_pct))
    return 8.0 + (s / 100.0) * 12.0


def hex_to_rgb(hex_str: str) -> List[int]:
    """Convert '#2E7D32' -> [46, 125, 50]."""
    h = hex_str.lstrip("#")
    return [int(h[i:i + 2], 16) for i in (0, 2, 4)]


# ---------------------------------------------------------------------------
# Streamlit app
# ---------------------------------------------------------------------------
st.set_page_config(page_title="Menu Analyzer", page_icon="🍽️", layout="wide")

# ---- Custom CSS ----
st.markdown("""
<style>
/* Hide Streamlit chrome */
#MainMenu, [data-testid="stMainMenu"] {visibility: hidden;}
footer {visibility: hidden;}
[data-testid="stStatusWidget"] {visibility: hidden;}

/* Tighter container padding (≈30% reduction) */
.block-container {
    padding-top: 2.2rem;
    padding-bottom: 2rem;
    padding-left: 2rem;
    padding-right: 2rem;
    max-width: 1200px;
}

/* Color dots */
.dot {
    display: inline-block;
    width: 14px;
    height: 14px;
    border-radius: 50%;
    box-shadow: 0 1px 2px rgba(0, 0, 0, 0.18);
    margin-right: 10px;
    vertical-align: middle;
}
.dot-safe      { background: #2E7D32; }
.dot-uncertain { background: #F9A825; }
.dot-unsafe    { background: #C62828; }

/* Dish row layout */
.dish-row { margin: 0 0 6px 0; line-height: 1.3; }
.dish-name {
    font-weight: 600;
    font-size: 1.05rem;
    color: #1A1A1A;
    vertical-align: middle;
}
.dish-desc {
    color: #5A6470;
    font-size: 0.9rem;
    margin: 2px 0 8px 24px;
}

/* Tighter section headers in the menu */
h3 { margin-top: 1.4rem !important; margin-bottom: 0.6rem !important; }

/* Card-style ranking table */
[data-testid="stDataFrame"] {
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06);
    border-radius: 10px;
    overflow: hidden;
}

/* Why? rows inside the expander */
.reason-row { margin: 4px 0; line-height: 1.35; }
.reason-row .dot { width: 10px; height: 10px; margin-right: 6px; }
.reason-evidence {
    color: #5A6470;
    font-size: 0.82rem;
    margin: 0 0 4px 22px;
}
</style>
""", unsafe_allow_html=True)

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

# ---- Map: where they are ----
st.subheader("Where they are")
st.caption("Pin size and color reflect friendliness score for your selected restrictions.")

map_rows = []
for fname in menu_files:
    raw = load_menu(fname)
    lat = raw.get("latitude")
    lng = raw.get("longitude")
    if lat is None or lng is None:
        continue
    s = compute_friendliness(classified_by_file[fname], strict_mode)
    rgb = hex_to_rgb(score_to_color(s["score_pct"]))
    map_rows.append({
        "lat": float(lat),
        "lon": float(lng),
        "name": raw.get("restaurant_name") or fname,
        "score": round(s["score_pct"], 1),
        "safe": s["safe_count"],
        "uncertain": s["uncertain_count"],
        "unsafe": s["unsafe_count"],
        "color": rgb + [220],  # RGBA, alpha 220
        "radius": score_to_radius_px(s["score_pct"]),
    })

if map_rows:
    map_df = pd.DataFrame(map_rows)
    layer = pdk.Layer(
        "ScatterplotLayer",
        data=map_df,
        get_position=["lon", "lat"],
        get_fill_color="color",
        get_radius="radius",
        radius_units="pixels",
        radius_min_pixels=8,
        radius_max_pixels=20,
        pickable=True,
        stroked=True,
        get_line_color=[255, 255, 255, 230],
        line_width_min_pixels=1,
    )
    center_lat = sum(r["lat"] for r in map_rows) / len(map_rows)
    center_lng = sum(r["lon"] for r in map_rows) / len(map_rows)
    deck = pdk.Deck(
        layers=[layer],
        initial_view_state=pdk.ViewState(latitude=center_lat, longitude=center_lng, zoom=13),
        tooltip={
            "html": (
                "<b>{name}</b><br/>"
                "Score: {score}%<br/>"
                "{safe} safe · {uncertain} uncertain · {unsafe} unsafe"
            ),
            "style": {
                "backgroundColor": "white",
                "color": "#1A1A1A",
                "fontSize": "12px",
                "padding": "8px 10px",
                "borderRadius": "6px",
                "boxShadow": "0 2px 6px rgba(0,0,0,0.18)",
            },
        },
        map_provider="carto",
        map_style="light",
    )
    st.pydeck_chart(deck)
else:
    st.info("No restaurants have latitude/longitude set yet — add them to the menu JSONs to see the map.")

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
st.dataframe(ranking_df.drop(columns=["_file"]), width="stretch", hide_index=True)

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
        cls = verdict_class(verdict, strict_mode)
        # Escape the dish name so quotes / angle brackets don't break the markup.
        name_html = (item["name"] or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        desc_html = (item.get("description") or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        body = (
            f'<div class="dish-row">'
            f'<span class="dot dot-{cls}"></span>'
            f'<span class="dish-name">{name_html}</span>'
            f'</div>'
        )
        if desc_html:
            body += f'<div class="dish-desc">{desc_html}</div>'
        st.markdown(body, unsafe_allow_html=True)

        if item["per_restriction"]:
            with st.expander("why?", expanded=False):
                for r, pr in item["per_restriction"].items():
                    pv = pr["verdict"]
                    pcls = verdict_class(pv, strict_mode)
                    reason = (pr.get("reason") or "").replace("<", "&lt;").replace(">", "&gt;")
                    st.markdown(
                        f'<div class="reason-row">'
                        f'<span class="dot dot-{pcls}"></span>'
                        f'<b>{r}</b> ({pv}): {reason}'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
                    ev = pr.get("evidence") or []
                    if ev:
                        ev_html = ", ".join(e.replace("<", "&lt;").replace(">", "&gt;") for e in ev)
                        st.markdown(
                            f'<div class="reason-evidence">evidence: {ev_html}</div>',
                            unsafe_allow_html=True,
                        )

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

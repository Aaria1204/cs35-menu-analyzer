# CS35 Final Project: Menu Dietary Analyzer

A Streamlit app that screens restaurant menus against user-selected dietary restrictions (allergens like dairy, nuts, gluten, plus diet bundles like vegetarian and vegan). It loads menu JSONs scraped from real restaurants, classifies each dish as `safe`, `uncertain`, or `unsafe` per restriction, and ranks restaurants by friendliness score so users can pick where to eat.

## Local setup

```
pip install -r requirements.txt
streamlit run app.py
```

Open http://localhost:8501 in your browser.

## Status

This is a class project. The Streamlit interface is real, but the classifier is currently a **mock** — verdicts are deterministic (hash-seeded by dish name) and look varied, but they are not real classifications. The real LLM classifier (Claude) and Spoonacular ingredient API integration are in progress; both will plug in via a single function swap (`classify(menu, restrictions)` in `app.py`).

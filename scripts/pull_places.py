"""
One-time (occasional, manual) data pipeline step per CLAUDE(CARA-BACKEND).md's
"Data Pipeline Notes": pulls real places for Nagpur from the Google Places API
across all 8 categories (+ QSR sub-category), merges the results against the
existing draft dataset (datasetCARA.ods) by fuzzy name + location match, and
flags anything Google didn't confirm as needing manual verification.

Only photo *metadata* (photo_reference, html_attributions, the Google
place_id needed to re-fetch a fresh reference later) is captured here -
Google's Places API ToS prohibits caching/storing photo content itself.
The actual image bytes are fetched live at display time by the backend
(see app/routers/places.py's GET /places/{id}/photo), never persisted.

This does NOT touch the database or the `places` table — importing the
merged CSV output is a separate, later step.

Run: python -m scripts.pull_places
Output: places_merged.csv
"""

import json
import time
import urllib.parse
import urllib.request
from difflib import SequenceMatcher

import pandas as pd

from app.config import settings
from ml.features import haversine_km

DRAFT_PATH = "datasetCARA.ods"
OUTPUT_PATH = "places_merged.csv"

TEXT_SEARCH_URL = "https://maps.googleapis.com/maps/api/place/textsearch/json"

# category -> Text Search query. QSR is handled as a second, separate query
# tagged onto the `restaurant` category, per CLAUDE(CARA-BACKEND).md ("QSR
# chains ... category = restaurant, sub_category = qsr").
CATEGORY_QUERIES = {
    "restaurant": "restaurants in Nagpur",
    "cafe": "cafes in Nagpur",
    "park": "parks in Nagpur",
    "mall": "malls in Nagpur",
    "library": "libraries in Nagpur",
    "gym": "gyms in Nagpur",
    "hospital": "hospitals in Nagpur",
    "tourist_attraction": "tourist attractions in Nagpur",
}
QSR_QUERY = "fast food restaurants in Nagpur"

# The Places API doesn't return an indoor/outdoor flag; this is a starting
# default per category, same spirit as the crowd table in ml/features.py —
# hand-tuned, refine later during manual verification.
DEFAULT_IS_INDOOR = {
    "restaurant": True,
    "cafe": True,
    "park": False,
    "mall": True,
    "library": True,
    "gym": True,
    "hospital": True,
    "tourist_attraction": False,
}

# Google's 0-4 ordinal price_level -> this project's price_range labels.
PRICE_LEVEL_TO_RANGE = {0: "budget", 1: "budget", 2: "moderate", 3: "premium", 4: "premium"}

NAME_MATCH_THRESHOLD = 0.6
DISTANCE_MATCH_KM = 0.3  # 300m - generous since draft coordinates are marked estimated


def _http_get(url: str, params: dict) -> dict:
    query = urllib.parse.urlencode(params)
    with urllib.request.urlopen(f"{url}?{query}", timeout=15) as resp:
        return json.loads(resp.read())


def text_search(query: str) -> list[dict]:
    """Fetches up to 60 results (3 pages) for a Text Search query.

    Despite most docs saying a pagetoken request only needs `pagetoken` +
    `key`, this API key returns INVALID_REQUEST unless the original params
    (query, region) are also kept on the continuation request - confirmed
    empirically, so keep them.
    """
    results = []
    base_params = {"query": query, "key": settings.google_places_api_key, "region": "in"}
    params = dict(base_params)
    page_token = None

    for _ in range(3):
        if page_token:
            time.sleep(2)  # next_page_token isn't valid immediately
            params = {**base_params, "pagetoken": page_token}
        data = _http_get(TEXT_SEARCH_URL, params)
        status = data.get("status")
        if status not in ("OK", "ZERO_RESULTS"):
            print(f"  ! Places API returned {status} for query '{query}': {data.get('error_message', '')}")
            break
        results.extend(data.get("results", []))
        page_token = data.get("next_page_token")
        if not page_token:
            break

    return results


def fetch_google_places() -> pd.DataFrame:
    rows = []
    seen_google_ids: set[str] = set()

    def add_results(results: list[dict], category: str, sub_category: str | None) -> None:
        for r in results:
            gid = r.get("place_id")
            if not gid or gid in seen_google_ids:
                continue
            if r.get("business_status") == "CLOSED_PERMANENTLY":
                continue
            seen_google_ids.add(gid)
            loc = r.get("geometry", {}).get("location", {})
            photos = r.get("photos") or []
            photo = photos[0] if photos else {}
            attributions = photo.get("html_attributions") or []
            rows.append(
                {
                    "name": r.get("name"),
                    "category": category,
                    "sub_category": sub_category,
                    # Text Search doesn't return a clean locality field (only
                    # Nearby Search's `vicinity` does) - leave for manual fill
                    # during the needs_verification review rather than guess.
                    "area": None,
                    "latitude": loc.get("lat"),
                    "longitude": loc.get("lng"),
                    "approx_rating": r.get("rating"),
                    "price_range": PRICE_LEVEL_TO_RANGE.get(r.get("price_level")),
                    "avg_price_inr": None,
                    "is_indoor": DEFAULT_IS_INDOOR[category],
                    "popular_time_slot": None,
                    # Needed later to re-query Place Details for a fresh
                    # photo_reference if the stored one expires.
                    "google_place_id": gid,
                    # A pointer used to fetch the actual image live at
                    # display time - not the image itself (ToS).
                    "photo_reference": photo.get("photo_reference"),
                    "photo_attribution": "; ".join(attributions) if attributions else None,
                }
            )

    for category, query in CATEGORY_QUERIES.items():
        print(f"Fetching: {query}")
        add_results(text_search(query), category, None)

    print(f"Fetching: {QSR_QUERY}")
    add_results(text_search(QSR_QUERY), "restaurant", "qsr")

    return pd.DataFrame(rows)


def load_draft() -> pd.DataFrame:
    df = pd.read_excel(DRAFT_PATH, engine="odf", sheet_name="Sheet1")
    df["is_indoor"] = df["is_indoor"].astype(bool)
    return df


def _name_similarity(a: str, b: str) -> float:
    norm = lambda s: str(s).strip().lower()
    return SequenceMatcher(None, norm(a), norm(b)).ratio()


def merge(draft: pd.DataFrame, google: pd.DataFrame) -> pd.DataFrame:
    draft = draft.copy()
    for col in ("google_place_id", "photo_reference", "photo_attribution"):
        if col not in draft.columns:
            draft[col] = None
    matched_draft_idx: set[int] = set()
    new_rows = []

    for _, g in google.iterrows():
        candidates = draft[
            (draft["category"] == g["category"]) & (~draft.index.isin(matched_draft_idx))
        ]
        best_idx, best_score = None, 0.0
        for idx, d in candidates.iterrows():
            dist_km = haversine_km(g["latitude"], g["longitude"], d["latitude"], d["longitude"])
            if dist_km > DISTANCE_MATCH_KM:
                continue
            score = _name_similarity(g["name"], d["name"])
            if score > best_score:
                best_idx, best_score = idx, score

        if best_idx is not None and best_score >= NAME_MATCH_THRESHOLD:
            # Confirmed match: prefer Google's ground-truth values for
            # anything it supplies; keep the draft's own place_id and any
            # fields (price, area, sub_category, ...) Google doesn't give us.
            matched_draft_idx.add(best_idx)
            draft.loc[best_idx, ["name", "latitude", "longitude", "approx_rating"]] = [
                g["name"], g["latitude"], g["longitude"], g["approx_rating"]
            ]
            if pd.isna(draft.loc[best_idx, "price_range"]) and g["price_range"]:
                draft.loc[best_idx, "price_range"] = g["price_range"]
            draft.loc[best_idx, "google_place_id"] = g["google_place_id"]
            if g["photo_reference"]:
                draft.loc[best_idx, "photo_reference"] = g["photo_reference"]
                draft.loc[best_idx, "photo_attribution"] = g["photo_attribution"]
            draft.loc[best_idx, "coordinates_estimated"] = False
            draft.loc[best_idx, "needs_verification"] = False
            draft.loc[best_idx, "source"] = str(draft.loc[best_idx, "source"]) + " + google_places_api"
        else:
            new_rows.append(g)

    # Draft rows Google never confirmed: flag for manual verification.
    unmatched = ~draft.index.isin(matched_draft_idx)
    draft.loc[unmatched, "needs_verification"] = True

    if new_rows:
        next_id = int(draft["place_id"].str.extract(r"P(\d+)")[0].astype(int).max()) + 1
        new_df = pd.DataFrame(new_rows)
        new_df["place_id"] = [f"P{i:04d}" for i in range(next_id, next_id + len(new_df))]
        new_df["coordinates_estimated"] = False
        new_df["needs_verification"] = False
        new_df["source"] = "google_places_api"
        draft = pd.concat([draft, new_df], ignore_index=True)

    return draft


def main() -> None:
    if not settings.google_places_api_key:
        raise SystemExit("GOOGLE_PLACES_API_KEY is not set in .env")

    print("Loading existing draft dataset...")
    draft = load_draft()
    print(f"  {len(draft)} existing rows")

    print("\nFetching from Google Places API...")
    google = fetch_google_places()
    print(f"  {len(google)} unique places fetched from Google")

    print("\nMerging...")
    merged = merge(draft, google)

    merged.to_csv(OUTPUT_PATH, index=False)
    print(f"\nWrote {len(merged)} rows to {OUTPUT_PATH}")
    print(f"  {(~merged['needs_verification']).sum()} confirmed by Google Places API")
    print(f"  {merged['needs_verification'].sum()} still need manual verification")


if __name__ == "__main__":
    main()

"""
Final stage of the Nagpur places data pipeline (run after
scripts/pull_places.py). Loads the merged CSV into the `places` table on
Supabase. Only photo *metadata* (photo_reference, photo_attribution,
google_place_id) is imported - the actual image is fetched live at display
time by GET /places/{id}/photo, never stored, per Google's Places API ToS.

Requires the Alembic migration to already be applied (`alembic upgrade
head`) against a real DATABASE_URL.

WARNING: this replaces the *entire* contents of `places` each run (delete +
bulk insert), not an upsert - the simplest correct option while `places` has
no stable external key to upsert on (place_id is an autoincrement int, not
the CSV's "P0001" scheme). Fine before any real `interactions` /
`preferences` rows reference a place_id (nothing does yet), but once real
users exist, re-running this would orphan any interaction/preference rows
pointing at the old integer place_ids. Revisit before then - e.g. upsert on
google_place_id instead.

Run: python -m scripts.import_places [--input places_merged.csv]
"""

import argparse
import math

import pandas as pd

from app.database import SessionLocal
from app.models.place import Place, PlaceCategory

DEFAULT_INPUT = "places_merged.csv"


def _clean(value):
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=DEFAULT_INPUT)
    args = parser.parse_args()

    df = pd.read_csv(args.input)
    print(f"Loaded {len(df)} rows from {args.input}")

    db = SessionLocal()
    try:
        deleted = db.query(Place).delete()
        print(f"Cleared {deleted} existing rows from places")

        places = [
            Place(
                name=row["name"],
                category=PlaceCategory(row["category"]),
                sub_category=_clean(row.get("sub_category")),
                area=_clean(row.get("area")),
                latitude=row["latitude"],
                longitude=row["longitude"],
                approx_rating=_clean(row.get("approx_rating")),
                price_range=_clean(row.get("price_range")),
                avg_price_inr=_clean(row.get("avg_price_inr")),
                is_indoor=_clean(row.get("is_indoor")),
                popular_time_slot=_clean(row.get("popular_time_slot")),
                eco_friendly=bool(_clean(row.get("eco_friendly")) or False),
                sustainability_score=_clean(row.get("sustainability_score")),
                source=_clean(row.get("source")),
                coordinates_estimated=bool(row.get("coordinates_estimated", False)),
                needs_verification=bool(row.get("needs_verification", False)),
                google_place_id=_clean(row.get("google_place_id")),
                photo_reference=_clean(row.get("photo_reference")),
                photo_attribution=_clean(row.get("photo_attribution")),
            )
            for _, row in df.iterrows()
        ]
        db.add_all(places)
        db.commit()
        print(f"Inserted {len(places)} rows into places")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()

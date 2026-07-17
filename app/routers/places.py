"""
Live photo proxy. Per Google's Places API ToS, photo content itself must
never be cached/stored - only photo_reference (metadata) is kept in the
`places` table (see scripts/pull_places.py). This endpoint fetches the
actual image from Google on every request, using the server-held API key,
so the Android client never sees that key directly (per
CLAUDE(CARA-BACKEND).md's Secrets rule).
"""

import json
import urllib.error
import urllib.parse
import urllib.request

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.place import Place

router = APIRouter(prefix="/places", tags=["places"])

PLACE_PHOTO_URL = "https://maps.googleapis.com/maps/api/place/photo"
PLACE_DETAILS_URL = "https://maps.googleapis.com/maps/api/place/details/json"
MAX_WIDTH = 800
# This is a live proxy behind an interactive scrolling UI - fail fast rather
# than hang. Google's Photo API is normally well under 2s; anything slower
# than this is unlikely to succeed anyway.
REQUEST_TIMEOUT_SECONDS = 6

# Failures that mean "the reference itself is probably bad" - worth the cost
# of falling through to a Place Details refresh + retry.
_REFERENCE_ERRORS = (urllib.error.HTTPError,)
# Failures that are just "the network/API had a bad moment" - retrying via
# the slower refresh path would likely hit the same issue, so fail fast
# instead of stacking multiple timeouts on top of each other.
_TRANSIENT_ERRORS = (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, KeyError, IndexError)


def _fetch_photo(photo_reference: str) -> tuple[bytes, str]:
    params = {
        "maxwidth": MAX_WIDTH,
        "photo_reference": photo_reference,
        "key": settings.google_places_api_key,
    }
    url = f"{PLACE_PHOTO_URL}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
        content_type = resp.headers.get("Content-Type", "image/jpeg")
        return resp.read(), content_type


def _refresh_photo_reference(google_place_id: str) -> str | None:
    """Re-queries Place Details for a fresh photo_reference - used when the
    reference stored in the DB has expired."""
    params = {
        "place_id": google_place_id,
        "fields": "photo",
        "key": settings.google_places_api_key,
    }
    url = f"{PLACE_DETAILS_URL}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
        data = json.loads(resp.read())
    photos = data.get("result", {}).get("photos") or []
    return photos[0]["photo_reference"] if photos else None


@router.get("/{place_id}/photo")
def get_place_photo(place_id: int, db: Session = Depends(get_db)) -> Response:
    place = db.get(Place, place_id)
    if place is None:
        raise HTTPException(status_code=404, detail="Place not found")

    if place.photo_reference:
        try:
            image_bytes, content_type = _fetch_photo(place.photo_reference)
            return Response(content=image_bytes, media_type=content_type)
        except _REFERENCE_ERRORS:
            pass  # likely expired - fall through and try to refresh it
        except _TRANSIENT_ERRORS:
            raise HTTPException(status_code=404, detail="No photo available")

    if not place.google_place_id:
        raise HTTPException(status_code=404, detail="No photo available")

    try:
        fresh_reference = _refresh_photo_reference(place.google_place_id)
    except _TRANSIENT_ERRORS:
        raise HTTPException(status_code=404, detail="No photo available")

    if not fresh_reference:
        raise HTTPException(status_code=404, detail="No photo available")

    # Opportunistically heal the stored reference so future requests for
    # this place don't have to hit Place Details again.
    place.photo_reference = fresh_reference
    db.commit()

    try:
        image_bytes, content_type = _fetch_photo(fresh_reference)
        return Response(content=image_bytes, media_type=content_type)
    except (*_REFERENCE_ERRORS, *_TRANSIENT_ERRORS):
        raise HTTPException(status_code=404, detail="No photo available")

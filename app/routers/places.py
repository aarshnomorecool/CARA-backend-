"""
Live photo proxy. Per Google's Places API ToS, photo content itself must
never be cached/stored - only photo_reference (metadata) is kept in the
`places` table (see scripts/pull_places.py). This endpoint fetches the
actual image from Google on every request, using the server-held API key,
so the Android client never sees that key directly (per
CLAUDE(CARA-BACKEND).md's Secrets rule).
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.place import Place
from app.schemas.place import PlaceRead

router = APIRouter(prefix="/places", tags=["places"])

PLACE_PHOTO_URL = "https://maps.googleapis.com/maps/api/place/photo"
PLACE_DETAILS_URL = "https://maps.googleapis.com/maps/api/place/details/json"
MAX_WIDTH = 800
# Clients ask for the size they actually draw (e.g. ~400px for a card
# thumbnail vs ~1000px for a full-bleed hero) - a thumbnail at 800px was
# ~155KB each and the main cause of slow-loading card rows.
MIN_REQUESTED_WIDTH = 200
MAX_REQUESTED_WIDTH = 1200

# Lets the phone keep each photo in its own image cache (Coil) for a day
# instead of re-downloading it through Google on every screen visit. This is
# client-side only - the server still never stores image bytes (ToS note in
# the module docstring).
PHOTO_CACHE_HEADERS = {"Cache-Control": "private, max-age=86400"}
NO_PHOTO_CACHE_HEADERS = {"Cache-Control": "private, max-age=3600"}

# place_id -> monotonic time we learned it has no usable photo. ~1 in 3
# places has none; without this every visit re-queried Google (Photo +
# Place Details) just to fail again. Metadata only, never image content.
NO_PHOTO_TTL_SECONDS = 6 * 60 * 60
_no_photo: dict[int, float] = {}
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


def _fetch_photo(photo_reference: str, max_width: int = MAX_WIDTH) -> tuple[bytes, str]:
    params = {
        "maxwidth": max_width,
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


@router.get("/{place_id}", response_model=PlaceRead)
def get_place(place_id: int, db: Session = Depends(get_db)) -> Place:
    """Standalone place lookup by ID - independent of any /recommendations
    response. Place Details needs this regardless of navigation source (Home,
    Saved, or a future deep link): the RecommendationCache it also reads from
    is only ever populated by /recommendations, so anything opened from
    Saved (which only has place_id + a smaller field set from
    /users/{id}/saved-places) previously had nowhere to fetch full place data
    from and showed "Place not found" even for places that exist fine."""
    place = db.get(Place, place_id)
    if place is None:
        raise HTTPException(status_code=404, detail="Place not found")
    return place


def _no_photo_response(place_id: int) -> HTTPException:
    _no_photo[place_id] = time.monotonic()
    return HTTPException(status_code=404, detail="No photo available", headers=NO_PHOTO_CACHE_HEADERS)


@router.get("/{place_id}/photo")
def get_place_photo(
    place_id: int,
    w: int = Query(default=MAX_WIDTH, description="Desired image width in px"),
    db: Session = Depends(get_db),
) -> Response:
    width = max(MIN_REQUESTED_WIDTH, min(MAX_REQUESTED_WIDTH, w))

    known_missing_at = _no_photo.get(place_id)
    if known_missing_at is not None and time.monotonic() - known_missing_at < NO_PHOTO_TTL_SECONDS:
        raise HTTPException(status_code=404, detail="No photo available", headers=NO_PHOTO_CACHE_HEADERS)

    place = db.get(Place, place_id)
    if place is None:
        raise HTTPException(status_code=404, detail="Place not found")

    if place.photo_reference:
        try:
            image_bytes, content_type = _fetch_photo(place.photo_reference, width)
            return Response(content=image_bytes, media_type=content_type, headers=PHOTO_CACHE_HEADERS)
        except _REFERENCE_ERRORS:
            pass  # likely expired - fall through and try to refresh it
        except _TRANSIENT_ERRORS:
            # Network blip, not a missing photo - don't remember it as missing.
            raise HTTPException(status_code=404, detail="No photo available")

    if not place.google_place_id:
        raise _no_photo_response(place_id)

    try:
        fresh_reference = _refresh_photo_reference(place.google_place_id)
    except _TRANSIENT_ERRORS:
        raise HTTPException(status_code=404, detail="No photo available")
    except _REFERENCE_ERRORS:
        raise _no_photo_response(place_id)

    if not fresh_reference:
        raise _no_photo_response(place_id)

    # Opportunistically heal the stored reference so future requests for
    # this place don't have to hit Place Details again.
    place.photo_reference = fresh_reference
    db.commit()

    try:
        image_bytes, content_type = _fetch_photo(fresh_reference, width)
        return Response(content=image_bytes, media_type=content_type, headers=PHOTO_CACHE_HEADERS)
    except _REFERENCE_ERRORS:
        raise _no_photo_response(place_id)
    except _TRANSIENT_ERRORS:
        raise HTTPException(status_code=404, detail="No photo available")

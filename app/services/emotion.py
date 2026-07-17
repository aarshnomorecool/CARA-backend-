"""
Converts free-text user input into one of ml/features.py's EMOTIONS labels
via Gemini. Per CLAUDE(CARA-BACKEND).md: Gemini does NOT rank places or
generate explanations, its output is just one more feature fed into
XGBoost - so any failure here degrades to "neutral" rather than failing the
whole /recommendations request.

Cached per exact input text (same TTL pattern as weather.py) - the Android
app resends the last-submitted mood text on every live-location auto-refetch
(see HomeScreen.kt's REFETCH_DISTANCE_METERS/MIN_REFETCH_INTERVAL_MS), so
without this, unchanged mood text pays Gemini's ~7-20s latency again on every
single background refresh instead of just once.

**2026-07-16 root-cause note** (this bit us as "emotion always shows
neutral"): `gemini-flash-latest` currently resolves to `gemini-3.5-flash`,
which (a) has "thinking" enabled by default - burning 15-30s generating an
internal reasoning trace before the actual one-word answer, routinely
exceeding this module's timeout - and (b) has a free-tier quota of just 20
requests/DAY for that specific model, trivially exhausted. Both failures are
`HTTPError`/`TimeoutError`, subclasses/members of the except-tuple below, so
they silently degraded to "neutral" - exactly matching the bug report, and
easy to miss without inspecting the raw HTTP error body. Fixed by switching
to `gemini-flash-lite-latest` (a cheap/high-quota tier model appropriate for
one-word classification - confirmed ~1s responses, correct labels, no quota
errors across repeated tests) and explicitly disabling thinking via
`thinkingConfig.thinkingBudget: 0`.
"""

import json
import time
import urllib.error
import urllib.request

from app.config import settings
from ml.features import EMOTIONS

# "latest" alias rather than a pinned version - avoids hardcoding a model
# name that Google later deprecates for this key. Deliberately the "lite"
# tier (not gemini-flash-latest) - see the root-cause note above.
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-lite-latest:generateContent"
CACHE_TTL_SECONDS = 10 * 60

_PROMPT = (
    "Classify the message below into exactly one of these labels, covering "
    "both emotional states and physical/situational needs: {labels}. "
    "Use want_to_relax for messages about wanting to unwind/relax, and "
    "want_to_exercise for messages about wanting to work out/be active. "
    "Reply with ONLY the single label exactly as written above (keep "
    "underscores as-is), nothing else, no punctuation.\n\nMessage: {text}"
)

# normalized text -> (classified_at_monotonic, label)
_cache: dict[str, tuple[float, str]] = {}


def detect_emotion(text_input: str | None) -> str:
    if not text_input or not text_input.strip():
        return "neutral"

    cache_key = text_input.strip().lower()
    now = time.monotonic()
    cached = _cache.get(cache_key)
    if cached and now - cached[0] < CACHE_TTL_SECONDS:
        return cached[1]

    prompt = _PROMPT.format(labels=", ".join(EMOTIONS), text=text_input.strip())
    body = json.dumps(
        {
            "contents": [{"parts": [{"text": prompt}]}],
            # Without this, gemini-flash-lite-latest still burns several
            # seconds on an internal reasoning trace for a task this simple -
            # see the root-cause note at the top of this file.
            "generationConfig": {"thinkingConfig": {"thinkingBudget": 0}},
        }
    ).encode()
    req = urllib.request.Request(
        f"{GEMINI_URL}?key={settings.gemini_api_key}",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read())
        label = data["candidates"][0]["content"]["parts"][0]["text"].strip().lower()
        result = label if label in EMOTIONS else "neutral"
    except (urllib.error.URLError, TimeoutError, OSError, KeyError, IndexError, ValueError):
        result = "neutral"

    _cache[cache_key] = (now, result)
    return result

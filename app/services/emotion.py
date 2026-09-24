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

# Keeps the lite model from spending seconds on an internal reasoning trace
# for a one-word classification. Gemini 3.x uses thinkingLevel; the older
# thinkingBudget: 0 form is rejected there (see detect_emotion's 400 retry).
_GENERATION_CONFIG = {"thinkingConfig": {"thinkingLevel": "minimal"}}


def _generate(prompt: str, generation_config: dict | None) -> dict:
    payload: dict = {"contents": [{"parts": [{"text": prompt}]}]}
    if generation_config:
        payload["generationConfig"] = generation_config
    req = urllib.request.Request(
        f"{GEMINI_URL}?key={settings.gemini_api_key}",
        data=json.dumps(payload).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())


def detect_emotion(text_input: str | None) -> str:
    if not text_input or not text_input.strip():
        return "neutral"

    cache_key = text_input.strip().lower()
    now = time.monotonic()
    cached = _cache.get(cache_key)
    if cached and now - cached[0] < CACHE_TTL_SECONDS:
        return cached[1]

    prompt = _PROMPT.format(labels=", ".join(EMOTIONS), text=text_input.strip())
    try:
        data = _generate(prompt, _GENERATION_CONFIG)
    except urllib.error.HTTPError as e:
        # The "-latest" alias moves to new model generations without notice,
        # and thinking-config parameters are model-specific. Real incident
        # (2026-09-24): the alias moved to gemini-3.5-flash-lite, which
        # rejects the old `thinkingBudget: 0` with 400 INVALID_ARGUMENT -
        # and because that 400 was swallowed below, every mood silently
        # became "neutral". Retry once with no generationConfig so an alias
        # change degrades to "slightly slower", never to "always neutral".
        if e.code != 400:
            data = None
        else:
            try:
                data = _generate(prompt, None)
            except (urllib.error.URLError, TimeoutError, OSError, ValueError):
                data = None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        data = None

    try:
        label = data["candidates"][0]["content"]["parts"][0]["text"].strip().lower() if data else "neutral"
        result = label if label in EMOTIONS else "neutral"
    except (KeyError, IndexError, TypeError):
        result = "neutral"

    _cache[cache_key] = (now, result)
    return result

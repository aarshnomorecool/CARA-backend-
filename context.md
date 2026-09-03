# CARA — Project Context

_Written 2026-09-03. This is a snapshot, not a live document — re-verify against actual code/state before trusting specifics that could have drifted._

## What CARA is

CARA (Context-Aware Recommendation Agent) is a final-year B.Tech FYP: a real-time, explainable
place-recommendation system for Nagpur. Given a user's live location, the time of day, current
weather, and their stated mood/need (free text), it ranks a catalog of ~600 real places (Google
Places-sourced) and explains *why* each one was recommended (real SHAP values, not canned text).
It also learns per-user category preferences over time from bookmarks/clicks/dismissals.

Two separate repos:

- **`CARA`** (this repo) — FastAPI backend, Python, SQLAlchemy 2.0 + Pydantic v2, XGBoost ranking
  model. `github.com/aarshnomorecool/CARA-backend-`.
- **`CARA-android`** — Kotlin + Jetpack Compose Android client, MVVM, Retrofit/OkHttp/Gson, Coil,
  Room (offline cache), Navigation Compose. `github.com/aarshnomorecool/CARA-kotlin`.

Each repo has its own spec file (`CLAUDE(CARA-BACKEND).md` in this repo, `CLAUDE_android.md` —
which, confusingly but confirmed intentionally, also physically lives in this backend repo, not
under `CARA-android/`) with tech-stack/design-system/non-goal rules. Check those before proposing
architecture changes.

---

## Pipeline / architecture

### Data pipeline (offline, one-time-ish)
1. `scripts/pull_places.py` — Google Places Text Search across 8 categories + QSR, merged against
   the original 140-row `datasetCARA.ods` by fuzzy name+location match → `places_merged.csv`.
   Captures `google_place_id`, `photo_reference`, `photo_attribution` alongside place data.
   Already run: 605 merged rows, 469 confirmed by Google, 467 with a photo reference.
2. `ml/synthesize_data.py` + `ml/train.py` — synthesizes training contexts, trains the XGBoost
   ranking model (R²=0.958). Model artifact loaded once at import time by `app/services/ranking.py`.
3. `scripts/import_places.py` — loads `places_merged.csv` into the live `places` table
   (delete-and-reinsert, not a true upsert — safe only because nothing external references
   `place_id` yet by a stable key; revisit if that ever changes, e.g. upsert on `google_place_id`).

### Request pipeline (`GET /recommendations`, the core endpoint)
1. `app/services/weather.py` — OpenWeather, ~20min cache, graceful fallback on failure.
2. `app/services/emotion.py` — Gemini (`gemini-flash-lite-latest`, thinking disabled) classifies
   free-text mood/need into one of 9 labels (`neutral, tired, stressed, happy, excited, hungry,
   bored, want_to_relax, want_to_exercise`). Result cached 10min per exact input text.
3. `app/services/ranking.py` — scores every candidate place with the trained XGBoost model using
   continuous/personalized features (proximity, budget_fit, preference_weight, rating, crowd_score).
4. **Deterministic post-hoc category boost** (`app/routers/recommendations.py`) — if a mood/need
   was detected, `EMOTION_CATEGORY_BONUS[emotion]` adds a flat score bonus per matching category
   (e.g. "hungry" boosts restaurant/cafe). This is deliberately NOT baked into the model — two
   rounds of trying to make XGBoost learn the association directly proved unreliable (a frequent
   category like restaurant drowns out a correctly-weighted but rarer signal like gym). See
   `reference_cara_emotion_ranking_architecture.md` for the full failed-approaches writeup.
5. **Neutral-query diversity cap** — when NO mood/need is active, `_cap_per_category()` caps any
   single category to `MAX_PER_CATEGORY_NEUTRAL = 6` of the top 20 (greedy, score-order preserving,
   backfills if the cap can't be filled). Exempt when a real emotion bonus is active, since a real
   mood *should* skew toward its matching category.
6. `app/services/explain.py` — real SHAP (`shap.TreeExplainer`) on the top-N only, not every
   candidate. `_feature_phrase` guards against a known SHAP gotcha: a one-hot dummy feature can get
   positive attribution even when its actual value is 0 for that row — only phrased if the row's
   value is actually 1.0.
7. If a category boost fired, `recommendations.py` force-prepends the matching `EMOTION_PHRASES`
   tag to that candidate's reason text, so the stated "why" stays honest even when the real reason
   is the deterministic layer, not something SHAP found.

### Preference learning
- `POST /interactions` (click/bookmark/dismiss/order_intent) → `app/services/preferences.py`'s
  `recompute_preference()` — **not** an incremental mutation. Every write replays ALL of that
  user's currently-active interactions for that category, in timestamp order, through an EMA
  (`weight = 0.7*old + 0.3*signal`, signal per action: click=0.4, bookmark=0.9, dismiss=0.05,
  order_intent=1.0) from the cold-start default. This makes it self-healing: deleting an
  interaction (unbookmarking) correctly un-does its effect, because the whole history is replayed
  from scratch rather than incrementally bumped and never reversed.
- Unbookmarking (`DELETE /users/{id}/saved-places/{place_id}`) deletes the interaction row and
  calls the same recompute — this is the fix for the hospital-domination production bug (see below).

### Auth
- Tokenless by design (never planned to be more) — `POST /auth/register` / `POST /auth/login`,
  bcrypt password hashing, every other endpoint takes a plain `user_id` param. No JWT/session layer
  exists on the backend; Android holds the session client-side (`UserSession`, SharedPreferences-backed).

### Images / Maps (hard policy constraints, not preferences — don't revisit without a new reason)
- **Photos are never stored** — Google's Places API ToS prohibits caching photo bytes (only
  `place_id` is exempt for indefinite storage). `places` stores only metadata
  (`google_place_id`, `photo_reference`, `photo_attribution`). `GET /places/{id}/photo` fetches the
  actual image from Google live on every request using the server-held key, so the key never
  reaches the Android client. On a stale `photo_reference`, it refreshes via Place Details and
  retries once before 404ing.
- **Maps**: Google Maps SDK (decided over Mapbox, 2026-08-15-ish). `com.google.maps.android:maps-compose`.

### Deployment
- **Google Cloud Run**, service `cara-backend`, region `asia-south1`, project
  `project-cb5a4d6b-3c27-405f-80a` (GCP account `caracloud8@gmail.com`, deliberately separate from
  the GitHub account). Live: `https://cara-backend-783370662490.asia-south1.run.app`.
- **No Dockerfile** — Google Cloud Buildpacks, driven by `Procfile`
  (`web: uvicorn app.main:app --host 0.0.0.0 --port $PORT`) and `.python-version` (pinned `3.13`).
- Env vars (`DATABASE_URL`, `GEMINI_API_KEY`, `GOOGLE_PLACES_API_KEY`, `OPENWEATHER_API_KEY`)
  passed via `--env-vars-file=env.yaml` (gitignored; `env.yaml.example` committed).
- **CI**: Cloud Build trigger on push to `main` — now genuinely working (see "Problems fixed" below).
- **Database**: Supabase Postgres, free tier, project ref `jxfndfsexconwoizvtin`, region
  `ap-south-1`. Connected via the Session pooler host (IPv4-safe; the direct host is IPv6-only and
  doesn't work on this network). **Auto-pauses after ~7 days idle** (confirmed happening in
  practice at 13 days) — needs a manual dashboard resume, no CLI/API path exists for that on the
  free tier. No automated backups; `places` is regenerable from CSV, `users`/`interactions`/
  `preferences` are not.
- Scale-to-zero (no `--min-instances` set) — first request after idle pays a real cold-start cost
  because `app/services/ranking.py` loads xgboost/shap/pandas/the trained model at process import.

---

## Progress achieved so far

**Backend — feature-complete against the original spec**, plus extras added along the way:
- All endpoints implemented and verified against live data: `GET /health`, `GET /places/{id}`,
  `GET /places/{id}/photo`, `POST /auth/register`, `POST /auth/login`, `GET /recommendations`,
  `POST /interactions`, `GET /users/{id}`, `GET /users/{id}/preferences`,
  `GET /users/{id}/saved-places`, `DELETE /users/{id}/saved-places/{place_id}`,
  `PUT /users/{id}/home-location`, `PUT /users/{id}/college-location`.
- Deployed live on Cloud Run, reachable over the public internet, CI auto-deploys on push.
- Real SHAP explainability, weather + Gemini-based emotion context, expanded 9-label mood/need
  taxonomy, deterministic category-boost + diversity-cap ranking layer, self-healing preference
  recompute.

**Android — all 5 core screens built and (mostly) on-device confirmed:**
- Home (sectioned layout: spotlight carousel, category tiles, 4 curated rows, live GPS tracking
  with a move/time-gated refetch), ContextInput (mood text + budget slider bottom sheet),
  PlaceDetails (photo, reason + tags, price/rating, bookmark, now also a map pin), Saved
  (bookmarked places list), Profile (identity, home/college location + map pins, preference bars,
  light/dark theme toggle, logout).
- Real authentication: Login/Register screen, persisted session, nav gated so unauthenticated
  users land on Login, bottom bar hidden there. **Confirmed working end-to-end on-device.**
- Both repos are now under git and pushed to GitHub, Android has a README tracking progress/roadmap.

**Two rounds of real production bugs found and fixed** (each root-caused via direct DB/log
inspection, not guessed):
1. "Place not found" opening a place from Saved — missing standalone lookup endpoint; added
   `GET /places/{id}`, rewired `PlaceDetailsScreen.kt` to a proper Loading/Success/NotFound state.
2. Neutral-mood Home suggesting almost only hospitals, with zero hospitals ever saved — root cause
   was the irreversible EMA mutation on unbookmark described above. Fixed with the recompute
   architecture + the diversity cap. Verified live with exact before/after weight numbers.

---

## Problems encountered and fixed (worth knowing if they resurface)

- **Cloud Build `$REPO_NAME` substitution bug**: the browser-generated CI trigger templated image
  paths using the built-in `$REPO_NAME` substitution, which resolves to the GitHub repo's literal
  name (`cara-backend-`, trailing hyphen). Docker's strict path parser rejects that in Pull/Push
  steps even though the Buildpack step tolerates it. Fixed by exporting the trigger YAML, dropping
  `$REPO_NAME/` from every image path, and re-importing via `gcloud builds triggers import`.
  Verified via a real triggered build reaching `SUCCESS`.
- **IAM**: fresh GCP projects' default compute service account lacks `roles/run.builder`, needed
  for `gcloud run deploy --source` to read its own uploaded source — one-time grant applied.
- **Gemini model gotchas**: `gemini-flash-latest` resolved to a "thinking"-enabled model with a
  20-requests/day free quota, causing 15-30s calls that routinely timed out and silently degraded
  to "neutral" (caught by an intentionally-broad exception handler). Fixed by switching to
  `gemini-flash-lite-latest` with `thinkingConfig.thinkingBudget: 0`.
- **urllib timeout gotcha**: `urlopen`'s read-phase timeout raises a raw `TimeoutError`, not
  wrapped in `URLError` — an except clause only catching `URLError` misses it, defeating the
  "graceful fallback" every outbound integration (weather/emotion/places) claims. Fixed across all
  three.
- **Secrets briefly exposed in a tool output** (`gcloud run services describe ... env.list()`
  printed the live `DATABASE_URL` and all 3 API keys in full). User was told to consider rotating
  all 4 — **unconfirmed whether that rotation actually happened**, worth checking.
- **maps-compose dependency version mismatch**: picked the latest release (8.4.0) without checking
  it against this project's already-pinned stack (compileSdk 34, AGP 8.5.0, compose-bom
  2024.06.00), causing 36 AAR metadata sync errors. Fixed by downgrading to the era-matched `4.4.2`.

---

## Open problems (not yet fixed)

1. **Photo loading is slow for most of the catalog.** Root cause confirmed empirically: Google's
   `photo_reference` values have no published fixed expiry, and most of this catalog's stored
   references (pulled weeks ago) no longer work directly — every such request pays a live
   "refresh via Place Details" round-trip (~1.0-1.3s vs ~0.27-0.29s for a working reference), and
   this does not appear to self-heal across requests even though the refreshed reference IS
   persisted. **Proposed, not yet approved/run**: a one-time bulk refresh — re-fetch and persist a
   current `photo_reference` for every place with a `google_place_id` (~450+ live Google Place
   Details calls, a few minutes to run).
2. **Cloud Run cold starts** are slower than typical because the ranking model (xgboost/shap/
   pandas) loads at process import time, and the service scales to zero by default. Not mitigated;
   `--min-instances=1` would fix it but costs money to keep an instance warm continuously.
3. **Credential rotation status unknown** — see "secrets briefly exposed" above.

---

## Yet to be achieved / blocked

1. **Map pins are functionally blocked.** All the code is written and pushed
   (`PlaceMapView.kt`, wired into Place Details and Profile), but `local.properties` has no real
   `MAPS_API_KEY` yet, so pins won't render correctly on-device. Needs the app's **debug SHA-1**
   (Android Studio → Gradle panel → app → Tasks → android → `signingReport`) to generate a
   properly package+SHA1-restricted key.
2. **Full on-device regression pass unconfirmed** since the auth rework landed: bookmark → Saved →
   unbookmark (does the preference bar actually move both ways now?), opening a place from Saved
   (does Place Details load without a reason section as expected?), Profile/logout round-trip.
   Login itself is confirmed; these adjacent flows are "should work," not "confirmed."
3. **Bulk photo-reference refresh** — see open problem #1, awaiting go-ahead.

## Planned / discussed but deliberately deferred

- Real crowd/occupancy data: Google's Places API doesn't officially expose live busyness; the only
  legitimate route is a paid third-party service (BestTime.app). Explicitly decided to keep the
  existing hand-tuned `crowd_score` heuristic instead — don't revisit unprompted.
- `--min-instances=1` on Cloud Run to eliminate cold starts — mentioned as an option, never committed to.
- Renaming the backend GitHub repo to drop its trailing hyphen (would fully obsolete the
  `$REPO_NAME` workaround) — never done since the trigger fix made it unnecessary; low priority.

## Related spec files in this repo
- `CLAUDE(CARA-BACKEND).md` — backend tech-stack/design rules.
- `CLAUDE_android.md` — Android tech-stack/design-system/writing-guidance rules (physically lives here).

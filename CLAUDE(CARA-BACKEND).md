# CARA Backend — Claude Code Instructions

## Project Overview

CARA (Context-Aware Recommendation Agent) is a final-year B.Tech FYP: a real-time,
explainable recommendation system for places in Nagpur (restaurants, cafés, parks,
malls, libraries, gyms, hospitals, tourist attractions). It recommends places by
fusing live location, time, weather, crowd prediction, budget, and user-typed emotion
— then explains *why* each recommendation was made.

This repo is the **backend only**. The client is a separate Android app (Kotlin +
Jetpack Compose) that talks to this backend over REST. The backend never assumes
anything about the frontend beyond "it sends JSON, it expects JSON back."

**Not this project's job:** the backend does not render UI, does not manage Android
permissions/GPS sensors directly (the app sends coordinates to us), and does not
call Uber/Zomato/etc. (deep-linking to those is a frontend concern, out of scope here).

---

## Tech Stack (do not deviate without asking)

- **Framework:** FastAPI (Python)
- **Database:** PostgreSQL, hosted on **Supabase** (free tier) — single
  project, single connection via the Transaction pooler (port 6543, not the
  direct 5432 connection). Do NOT split tables across multiple databases.
  Supabase holds ALL tables, not a subset. Free-tier projects auto-pause
  after 7 days of inactivity — data is preserved, but the project needs a
  manual resume in the Supabase dashboard before the backend can connect
  again, so check this before any demo. No automated backups on free tier,
  so treat `places` as regenerable (via the data pipeline below) but be
  mindful that real `users`/`interactions`/`preferences` data isn't
  automatically backed up.
- **ORM:** SQLAlchemy + Pydantic models for request/response validation
- **ML model:** XGBoost — trained ONCE, offline, on the maintainer's machine.
  Loaded at FastAPI startup from a bundled `.pkl` / native XGBoost file.
  **Do not build a retraining pipeline, cron job, or "online learning" system
  for the model itself** — that's explicitly out of scope for this project.
- **Emotion detection:** Gemini API — used ONLY to convert free-text user input
  (e.g. "I'm exhausted") into a structured emotion label (e.g. `tired`, `stressed`,
  `happy`, `neutral`, `excited`). Gemini does NOT rank places, does NOT generate
  recommendations, and does NOT write explanations. Its output is just one more
  feature fed into XGBoost.
- **Explainability (XAI):** SHAP applied to the XGBoost model's output, converted
  into human-readable sentences via templates (not another LLM call). See
  "Explainability" section below for the exact contract.
- **Crowd prediction:** Self-built heuristic (see "Crowd Prediction" section).
  **Do NOT scrape Google Popular Times or use any third-party scraping service**
  (Apify, Outscraper, ScrapingBee, etc.) — this violates Google's ToS and is
  explicitly ruled out for this project.
- **Deployment:** FastAPI app on Render or Railway free tier — **compute
  only**. **No Docker** — deploy directly (native buildpack / `uvicorn` start
  command). Keep this simple. The database does NOT live here: Render/Railway
  free-tier Postgres add-ons hard-delete the database after ~30 days, which
  is unacceptable for a project that needs to persist for a semester.
  Supabase is the sole, persistent database (see above); Render/Railway are
  stateless compute that can restart or redeploy freely without losing data.
- **Secrets:** All third-party API keys (Google Places, OpenWeatherMap, Gemini)
  and the Supabase `DATABASE_URL` (pooler connection string, includes the DB
  password) live in environment variables on the hosting platform. NEVER
  hardcode a key/connection string in source, NEVER commit one to git, and
  NEVER expose these to the Android client. The client only ever talks to
  this backend, never directly to Google/OpenWeatherMap/Gemini/Supabase.

---

## Database Schema (four core tables)

### `users`
Static account/profile info.
```
user_id (PK), name, email, password_hash (if not using Supabase Auth),
budget_default, home_lat, home_lon, college_lat, college_lon, created_at
```

### `places`
The Nagpur catalog. Built once via Google Places API pull + merge with any
manually/Cowork-sourced data. Static after initial build (re-run the pull
script manually to refresh, not automated).
```
place_id (PK), name, category, sub_category, area, latitude, longitude,
approx_rating, price_range, avg_price_inr, is_indoor, popular_time_slot,
eco_friendly (boolean), sustainability_score (1-5, nullable),
source, coordinates_estimated (boolean), needs_verification (boolean)
```
`category` values: `restaurant, cafe, park, mall, library, gym, hospital,
tourist_attraction`. QSR chains (Burger King, McDonald's, etc.) are
`category = restaurant`, `sub_category = qsr`.

### `preferences`
Live, per-user taste weights. Updated on every interaction (this is the
"feels real-time" layer — NOT model retraining).
```
user_id (FK), category, weight (float, exponential moving average),
last_updated
```

### `interactions`
Log of every click/bookmark/dismiss. Feeds preference updates and future
manual retraining if ever done.
```
interaction_id (PK), user_id (FK), place_id (FK), action
(click/bookmark/dismiss/order_intent), timestamp, context_snapshot (JSON:
lat, lon, time_slot, weather, emotion at time of interaction)
```

---

## Core Request Flow

```
1. Android app sends: user_id, lat, lon, budget, optional text_input
2. Backend resolves context:
   - time_slot from server clock
   - weather from OpenWeatherMap (cache 15-30 min per area, don't call per-request)
   - emotion from Gemini API (if text_input provided; else "neutral")
   - crowd_score per candidate place from internal heuristic (see below)
   - semantic location (HOME / COLLEGE / OUTSIDE) via geofence check against
     user's stored home_lat/lon, college_lat/lon (radius ~100m)
3. Backend queries `places` for candidates within reasonable radius of (lat, lon)
4. Backend builds feature vector per candidate:
   [distance, time_slot, weather, crowd_score, budget_fit, preference_weight,
    emotion, eco_friendly, rating]
5. XGBoost scores + ranks candidates
6. SHAP extracts top contributing features per top-N results
7. Template layer converts SHAP output → explanation string
8. Response: ranked list of places, each with a `reason` string
```

---

## Crowd Prediction (self-built, not fetched)

Do NOT integrate any external crowd/Popular-Times API or scraper. Build a
lookup-based heuristic instead:

```
crowd_score = f(category, day_type[weekday/weekend], time_slot)
```
Start with a hand-tuned table (e.g. gyms peak morning/evening, cafés peak
afternoon, restaurants peak lunch/dinner, malls peak weekend afternoon).
Optionally blend in self-reported crowding from `interactions` over time
(e.g. users tagging "it's crowded" — treat as a future enhancement, not V1
blocker).

---

## Explainability Contract

Every recommendation returned to the client must include a `reason` field:
a short, human-readable string built from the top 2-3 SHAP-attributed
features for that place, e.g.:

> "Recommended because: 450m away · matches your café preference ·
> currently low crowd"

Do not return raw SHAP values to the client — only the rendered string
(and optionally a structured `reason_tags: []` array if the frontend wants
to render chips instead of a sentence).

---

## API Endpoints (initial scope)

```
POST /auth/register
POST /auth/login
GET  /recommendations?user_id=&lat=&lon=&budget=&text_input=
POST /interactions              # log click/bookmark/dismiss
GET  /users/{user_id}/preferences
PUT  /users/{user_id}/home-location
PUT  /users/{user_id}/college-location
```

---

## Explicit Non-Goals (do not build unless asked)

- No Docker / containerization
- No scheduled/automated model retraining (train once, manually, offline)
- No scraping of Google Popular Times or any Google Maps data outside the
  official Places API
- No Uber/Zomato/Swiggy booking or ordering integration on the backend
  (deep-linking, if implemented, is a pure frontend concern)
- No storing of raw face images or audio (if emotion detection ever expands
  beyond text, store only the derived label, never the raw media)
- No API keys, secrets, or credentials in code or committed files — env
  vars only

---

## Data Pipeline Notes (for one-time dataset build)

1. Google Places API pull (Text/Nearby Search) across all 8 categories +
   QSR sub-category, city = Nagpur.
2. Merge against any existing CSV (e.g. earlier Cowork-generated draft) by
   fuzzy name + location match; prefer Google API values on conflict
   (they're ground-truth verified).
3. Flag `coordinates_estimated` / `needs_verification` for any row not
   confirmed by the API.
4. Import final merged CSV into `places` table.
5. This is a manual, occasional script — not a live/runtime process.

---

## When Unsure

If a request would violate one of the "Explicit Non-Goals" above, or adds
infrastructure complexity not covered in this file (e.g. new external APIs,
new scheduled jobs, containerization), stop and ask before implementing —
don't assume it's wanted just because it would technically improve the system.

# CARA — backend

FastAPI + SQLAlchemy + XGBoost/SHAP backend for **CARA** (Context-Aware
Recommendation Agent). See `CLAUDE(CARA-BACKEND).md` for the spec and
`context.md` for the full pipeline/progress write-up.

## Running locally (current dev setup, 2026-09-24)

Both the API and Postgres run on the dev laptop; the Android debug build talks
to the laptop over the LAN / phone hotspot.

**One-time setup** (already done on the main dev machine):

1. Portable PostgreSQL 17 lives in `%USERPROFILE%\cara-postgres`
   (`pgsql\bin` = EDB binaries, `data` = cluster). Localhost-only, trust auth,
   database `cara`. To recreate from scratch:
   ```powershell
   $b = "$env:USERPROFILE\cara-postgres\pgsql\bin"
   & "$b\initdb.exe" -D "$env:USERPROFILE\cara-postgres\data" -U postgres -A trust -E UTF8 --no-locale
   & "$b\pg_ctl.exe" -D "$env:USERPROFILE\cara-postgres\data" -l "$env:USERPROFILE\cara-postgres\server.log" -w start
   & "$b\createdb.exe" -U postgres -h localhost cara
   ```
2. `.env`: `DATABASE_URL=postgresql+psycopg://postgres@localhost:5432/cara`
   (the previous Supabase URL is kept commented out underneath).
3. Schema + places catalog:
   ```powershell
   .\.venv\Scripts\python.exe -m alembic upgrade head
   .\.venv\Scripts\python.exe -m scripts.import_places
   ```
   Loads all 605 places from `places_merged.csv`. User accounts, bookmarks and
   learned preferences are not in the CSV; they start empty locally.

**Every day:**

```powershell
.\run_local.ps1
```

This starts Postgres if needed, prints the laptop's LAN IP, and serves the API
on `0.0.0.0:8000`. Put that IP in `CARA-android/local.properties` as
`CARA_BASE_URL=http://<ip>:8000/` and rebuild the app. The phone and laptop
must be on the same network.

**Copying data back from Supabase (optional):** Supabase's pooler ports
(5432/6543) are blocked on some networks, and free projects auto-pause after
about a week idle. From a network that allows it, with the project resumed:
`pg_dump -n public --no-owner --no-privileges -Fc` against the Supabase URL,
then `pg_restore --data-only` into the local `cara` database.

## Cloud deployment

Still live on Cloud Run (`asia-south1`), auto-deploying from `main`, backed
by Supabase. The Android **release** build points there; the **debug** build
points at the laptop.

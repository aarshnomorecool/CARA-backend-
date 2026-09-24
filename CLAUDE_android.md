# CARA Android — Claude Code Instructions

## Project Overview

CARA (Context-Aware Recommendation Agent) is a final-year B.Tech FYP: a real-time,
explainable recommendation system for places in Nagpur. This repo is the **Android
frontend only** — native Kotlin + Jetpack Compose. It talks to a separate FastAPI
backend over REST (see that repo's own `CLAUDE.md` for the API contract/schema —
do not duplicate backend logic here).

**This app's job:** collect context (GPS, optional text mood, budget), send it to
the backend, and present ranked recommendations with their explanations in a UI
that feels fast, warm, and alive — not a generic list of cards. The bar is: this
should feel as considered and habitual to open as a food-delivery app, because
that's the category of app people actually open daily out of habit. Get there with
an original visual identity, not by copying any specific existing app's branding.

---

## Design System

Before writing any UI code, internalize this token system. Do not deviate from it
without a clear reason tied back to the brief below — consistency across ~15+
screens/components matters more than any single component looking clever in
isolation.

### Grounding concept

Nagpur is nicknamed the "Orange City" — historically famous for its oranges. That's
the authentic anchor for this app's palette, rather than reaching for a generic
food-app orange. The palette below uses a deeper, more burnt citrus tone (closer to
orange peel/rind) against a warm dark ink background, not a bright saturated
delivery-app orange.

### Color tokens

```
--ink-base:      #14120F   // primary background, warm near-black, not pure #000
--ink-surface:   #1E1A16   // card/surface background
--ink-raised:    #28221D   // elevated surface (sheets, dialogs)
--citrus:        #E8641C   // primary accent — burnt orange, CTAs, active states
--citrus-dim:    #B84F17   // pressed/dim state of primary accent
--sage:          #7FA650   // secondary accent — freshness, eco-friendly tag, low-crowd indicator
--brick:         #C4443A   // alert accent — high crowd, budget-exceeded warning
--warm-white:    #F5EFE6   // primary text on dark surfaces
--warm-grey:     #9C948A   // secondary/muted text, captions, timestamps
```

Do not introduce generic acid-green or bright saturated Material default colors
(e.g. stock Material `#6750A4`). Every color used should trace back to this token
list, a documented theme palette below, or a tint/shade of one.

### Themes (added 2026-09-24, user-requested)

The tokens above are the default **Orange City** theme. The user can pick a theme
on Profile → Appearance; the choice is persisted (SharedPreferences) and restored
before the first frame. Every theme is a complete palette in
`ui/theme/CaraThemes.kt` supplying the same token names (base / surface / raised /
text / muted / accent / accent-dim / on-accent) plus a 3-stop accent gradient and a
subtle background wash. Screens only ever read the token names (`InkBase`,
`Citrus`, `AccentBrush`, …), so adding a theme touches no screen code.

| Theme | Mode | Accent | Gradient |
|---|---|---|---|
| Orange City (default) | dark | `#E8641C` | `#FFA24C → #E8641C → #B8391A` |
| Orange Cream | light | `#E8641C` | `#FF9A4A → #E8641C → #C94A14` |
| Blossom (pink) | light | `#E2457F` | `#FF9EC4 → #F0548E → #C2185B` |
| Amethyst (purple) | dark | `#A974F7` | `#D6B4FF → #A974F7 → #6A2FD1` |
| Onyx (black) | dark, true-black OLED | `#EDEDED` | `#FFFFFF → #C9C9CE → #7C7C82` |

`--sage`, `--brick` and the star-rating gold stay constant across all themes —
they carry meaning (low crowd / eco, warnings, ratings), not branding. The purple
theme is a deliberate exception to the original "no purple" rule, at the user's
request; it is opt-in and never the default. Text drawn on the accent/gradient
uses `OnAccent` (not `InkBase`), so light themes keep real contrast on it.

### Typography

- **Display face:** Fraunces (variable, use the softer/lower-contrast optical
  sizes) — for screen titles, place names on detail screens, the empty-state
  headline. Used with restraint: large text only, never body copy.
- **Body face:** Inter — for all body text, buttons, form fields, navigation
  labels. This carries almost all the reading weight of the app.
- **Utility/data face:** JetBrains Mono — for distances, prices, timestamps, and
  crowd/rating numbers specifically. This is a deliberate choice: rendering
  numeric context data in monospace signals "this is a live measurement" and
  visually separates system-generated context from human-written content
  (place names, reasons).

### Layout concept

Home screen is a **sectioned layout** (as of 2026-07-16 — this replaced an
earlier single flat vertical feed), built entirely from one `/recommendations`
response with no extra API calls per section:

- **Context Strip** (see Signature Element below) pinned at the top, always
  visible, unaffected by anything below it
- **Category quick-access tiles**: a horizontal row of 4 tappable tiles below
  the Context Strip — "Eat & Drink" (restaurant/cafe), "Outdoors"
  (park/tourist_attraction), "Wellness" (gym/hospital), "Culture & Work"
  (library/mall). Tapping one client-side filters the curated rows below by
  category; tapping the same tile again clears the filter. Active tile uses
  the citrus accent fill; inactive tiles use `--ink-surface`.
- **"Recommended right now" spotlight carousel**: a full-bleed, horizontally
  swipeable carousel (`HorizontalPager`) showing the top 3 places from the
  ranked response — always unfiltered, regardless of the tile selection above
  (the tiles filter the curated rows further down, not this strip). Each page
  is a full-bleed photo with a bottom scrim, name + category overlaid at the
  bottom, and the single strongest XAI reason as a citrus badge in the top
  corner. Sized similarly to the old top-pick card, with a peek of the next
  card visible at the screen edge.
- **Curated horizontal rows** (`LazyRow`, compact card format, same card
  component as before): four rows below the carousel, each a *re-sort* of the
  same already-fetched list by a different existing score component, not a
  new query — "Perfect for right now" (overall model score, the response's
  default order), "Low crowd nearby" (crowd_score ascending), "Matches your
  taste" (preference_weight descending), "Fits your budget" (budget_fit
  descending). A row hides itself entirely if fewer than 3 places would
  appear in it after the category filter is applied — no half-empty rows.

ASCII sketch of the compact card (used in the curated rows):
```
┌─────────────────────────────────────┐
│ [photo]  Café Cocoon           ★4.3 │
│ [photo]  Cafe · 450m · ₹200         │
│ [photo]  ⌗ Nearby ⌗ Low crowd ⌗ Fits │
└─────────────────────────────────────┘
```

### Signature element: the Context Strip

A thin, persistent horizontal strip pinned below the top app bar, always visible
on the Home screen. It renders the live context the recommendation was actually
computed from — not decoration, actual system state:

```
[☁ 31°C]  [🕐 Evening]  [📍 Near College]  [😌 Neutral]
```

Each segment is a small pill in `--ink-raised` with `--warm-grey` text, except
the currently-most-influential factor (e.g. if emotion was explicitly provided
by the user this session, highlight that pill in `--citrus`). This strip is the
one place in the app that makes "context-awareness" *visible* rather than just
claimed — treat it as the thing a demo/viva evaluator should notice first. Tapping
any pill in the strip opens a small explanation of that factor (e.g. tapping the
crowd/location pill explains the geofence detection).

### Explanation (XAI) chips

Render reason tags as small pill-shaped chips with a `⌗` or dot prefix, in
`--ink-raised` background with `--warm-white` text, sage-tinted when the reason
relates to crowd/eco-friendliness, citrus-tinted when it relates to preference
match. Never render the explanation as a plain sentence buried in grey caption
text — the chips are the whole point of the XAI feature and should be visually
prominent, not an afterthought.

### Motion

- Context Strip pill values animate with a subtle crossfade when context updates
  (e.g. time slot changes, weather refreshes) — this is the one place continuous
  motion is justified, since it reinforces "this is live."
- Card list: standard Compose item enter animation on initial load only, no
  scroll-triggered gimmicks.
- Avoid decorative animation elsewhere — respect reduced-motion system settings.

### Component patterns

- Bottom navigation: 3 tabs — Home, Saved, Profile. Icons + label, `--citrus` for
  active tab, `--warm-grey` for inactive.
- Buttons: primary action = filled `--citrus` background, `--ink-base` text.
  Secondary = outlined, `--warm-grey` border, `--warm-white` text.
- Empty states: follow the writing guidance below — an empty "Saved" screen should
  invite action ("Nothing saved yet — tap the bookmark on a recommendation to
  keep it here"), not just say "No items."

---

## Tech Stack

- **Language:** Kotlin
- **UI:** Jetpack Compose, Material 3 as a base but overridden with the token
  system above — do not ship default Material 3 colors/typography
- **Networking:** Retrofit + OkHttp, single `ApiService` interface, base URL as a
  build config field (not hardcoded) so it can point to localhost during dev and
  the deployed backend in release builds
- **Local cache:** Room — cache the last successful recommendation response for
  offline fallback (show cached results with a "showing saved results — offline"
  banner if a live fetch fails)
- **State management:** ViewModel + StateFlow, one ViewModel per screen
- **Navigation:** Navigation Compose
- **Maps:** Google Maps SDK for Android — uses its own restricted key (see
  Secrets section), separate from any backend-held API keys
- **Image loading:** Coil
- **Architecture:** MVVM with a repository layer between ViewModel and
  Retrofit/Room, so data sources are swappable and testable

---

## Screens

1. **Home** — Context Strip + recommendation feed (top-pick card + compact cards)
2. **Place Details** — full photo, map pin, full XAI explanation, budget/price,
   bookmark action
3. **Context Input** (sheet/modal from Home) — text mood input, budget slider
4. **Profile** — gradient hero (initials avatar, name, email, member-since, edit
   name), stats (saved / explored / interactions; tapping jumps to that tab),
   taste profile (top category + animated preference bars), default budget
   (feeds `/recommendations` and the mood sheet), Home/College locations settable
   from current GPS (map pins), theme picker, change password, location-permission
   status, clear offline cache, live server status, share, version, log out
   (confirmed), delete account (typed confirmation). Every control is wired to a
   real endpoint or real local state; no decorative settings.
5. **Saved** — bookmarked places, same compact card format as Home

---

## API Contract

This app is a pure client of the backend defined in the backend repo's
`CLAUDE.md`. Key endpoints it calls:

```
GET    /recommendations?user_id=&lat=&lon=&budget=&text_input=
POST   /interactions
GET    /users/{user_id}/preferences
PUT    /users/{user_id}/home-location
PUT    /users/{user_id}/college-location
PATCH  /users/{user_id}            (name, budget_default)
GET    /users/{user_id}/stats
PUT    /users/{user_id}/password
DELETE /users/{user_id}
GET    /health                     (Profile's server-status row)
```

Do not invent additional backend behavior client-side (e.g. do not compute
recommendation ranking or explanations locally — that logic lives entirely in
the backend; this app only renders what it receives).

---

## Secrets

- The Google Maps SDK key is the **only** key that lives in this app, restricted
  to this app's package name + SHA-1 signing certificate in Google Cloud Console.
  Store it in `local.properties` (gitignored), never committed.
- No other third-party API key (Places, Weather, Gemini) belongs in this repo —
  those are backend-only, per the backend `CLAUDE.md`.

---

## Explicit Non-Goals

- No backend/business logic in the app (ranking, XAI generation, crowd
  prediction all happen server-side)
- No Uber/Zomato/Swiggy booking integration for V1
- No stock/default Material 3 look — every screen should trace back to the
  design tokens above
- No hardcoded backend URLs — use build config so dev/prod can differ

---

## When Unsure

If a screen or component isn't covered by the Design System section above,
extend it *consistent with the existing tokens* (reuse the palette, don't
introduce new colors/fonts) rather than defaulting to generic Material styling.
If a genuinely new pattern is needed, propose it and explain how it derives from
the existing signature/palette before implementing.

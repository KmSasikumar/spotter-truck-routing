# Weather-Aware Truck Routing

A React + Django web app that helps a truck driver pick the **safest route** based on
weather, load weight and travel time. (Spotter Labs Full Stack Developer take-home.)

## Live demo
- Frontend: https://spotter-truck-routing.antideploy.app
- Backend API: https://spotter-truck-routing-api-b6ve.onrender.com
- Walkthrough video (captioned screen capture, no voice): `demo/spotter-walkthrough.mp4`
  — https://github.com/KmSasikumar/spotter-truck-routing/blob/main/demo/spotter-walkthrough.mp4

## Setup

**Backend (Django, Python 3.12):**
```bash
cd backend
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
./venv/bin/python manage.py runserver 127.0.0.1:8000
```

**Frontend (React + Vite + Leaflet):**
```bash
cd frontend
npm install
npm run dev        # dev server on http://localhost:5173 (proxies /api -> Django)
npm run build      # production build -> frontend/dist/
```

No accounts, no API keys — every external call is keyless and made server-side
by Django (avoids CORS and keeps third-party usage off the client).

## Architecture

```
frontend/  React (Vite) + Leaflet + leaflet.heat
           - trip form (geocode autocomplete, departure, load, spacing)
           - map: route polylines, risk-colored checkpoint markers w/ popups,
             weather heat layer + 0–48h forecast slider, route summary cards
backend/   Django (plain JSON views, no DB models needed)
  routes/
    views.py     /api/geocode  /api/plan  /api/heatmap
    services.py  risk classification, route summaries, recommendation
```

### API endpoints
- `GET /api/geocode/?q=...` → Nominatim (US), top 5 matches `{display_name, lat, lon}`.
- `POST /api/plan/` `{origin, destination, departure_iso, load_lb, spacing_miles}` →
  routes with geometry, distance/duration, checkpoints
  `{lat, lon, miles_along, eta_iso, wind_mph, rain_in_hr, snow_in_hr, risk, reasons}`,
  per-route summaries `{miles_per_level, avg_risk, worst_level}`, `recommended_index`
  + `recommendation_reason`, `notes`, `assumptions`.
- `POST /api/heatmap/` `{points, departure_iso, hour_offset (0–48), load_lb}` →
  `{hour_iso, points: [{lat, lon, intensity 0–4, level, wind_mph, rain_in_hr, snow_in_hr}]}`.

### Third-party APIs (all free & keyless)
- **Geocoding:** Nominatim (`nominatim.openstreetmap.org/search`), US-filtered, proper User-Agent.
- **Routing:** OSRM public server `router.project-osrm.org` with `alternatives=3`,
  `overview=full`, `geometries=geojson`. If it returns fewer than 3 routes we plan
  with what we got and say so in `notes` (verified: Dallas→Houston returns 1).
- **Weather (primary):** National Weather Service gridpoint API (`api.weather.gov`) —
  wind (km/h → mph), quantitative precipitation (mm → in/hr) and snowfall
  (mm → in/hr), expanded from interval values to hourly rates. US-only, which
  fits this US trucking app. One point lookup + one grid request per grid cell,
  with a 30-minute in-process forecast cache.
- **Weather (fallback):** Open-Meteo `api.open-meteo.com/v1/forecast`, hourly
  `wind_speed_10m` (mph), `precipitation` (inch), `snowfall` (cm → ÷2.54 = in/hr),
  used only if NWS fails for a point (e.g. a non-US location). One batched
  request per route for all its checkpoints (multi-coordinate support);
  heatmap batches all corridor points in one request too, with 429 retry + backoff.

## Risk & recommendation logic (`routes/services.py`)

Checkpoint risk = worst of wind / rain / snow bands:
- Wind mph: Low <25 · Moderate 25–34 · High 35–44 · Severe 45–54 · No Travel ≥55
- Rain in/hr: Low <0.10 · Moderate 0.10–0.25 · High 0.25–0.50 · Severe 0.50–1.00 · No Travel >1.00
- Snow in/hr: Low <0.5 · Moderate 0.5–1.0 · High 1.0–2.0 · Severe 2.0–3.0 · No Travel >3.0

Load rules (escalate only):
- Wind ≥55 mph → **No Travel** for any load
- Wind 45–54 mph + load >30,000 lb → **No Travel**
- Wind 35–44 mph + load >40,000 lb → **Severe**

Recommendation order: ① fewest **no-travel** miles (treated worse than severe),
② fewest severe miles, ③ fewest high miles, ④ lowest average risk,
⑤ shortest travel time. The response includes a one-line reason for the pick.

## Assumptions (also returned by `/api/plan/`)
- Checkpoint ETA = departure + (miles_along / route_miles) × route duration —
  i.e. uniform average speed along the route.
- Each segment between two checkpoints takes its **starting** checkpoint's risk level;
  miles-per-level are summed path miles between checkpoints (miles_along differences).
- The forecast hour nearest each checkpoint ETA is used; ETAs beyond the
  forecast window clamp to the nearest available hour.
- Weather is sampled at exact spacing intervals plus the destination point.

## Verification (2026-10-26, local backend + live OSRM / NWS / Nominatim)
- `manage.py check` — no issues. `npm run build` — passes.
- End-to-end `POST /api/plan` Dallas, TX → Houston, TX (departure next day
  08:00 CDT, 40,000 lb, 25-mi spacing) → HTTP 200, 1 route (OSRM returned 1; noted),
  238.3 mi / 4h 13m, 11 checkpoints each with ETA + weather + risk + reasons,
  recommended_index 0 with reason string, 4 `assumptions` + `notes` present.
- Los Angeles → San Francisco → HTTP 200, 2 routes (OSRM returned 2; noted);
  recommendation picks Route 1 on the risk tie (shorter time).
- `POST /api/heatmap` (60 corridor points, +0h/+24h/+48h) → HTTP 200, 60 points each
  with intensity/level/wind/rain/snow. `GET /api/geocode` → 200. Bad payloads → clean 400s.
- Risk-rule boundary checks (run against `routes/services.py`): 16 cases pass,
  incl. wind/rain/snow band edges and load rules (e.g. 40 mph + 45,000 lb → severe;
  50 mph + 31,000 lb → no_travel; rain 1.5 in/hr → no_travel).

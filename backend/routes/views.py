"""JSON API views. All external API calls are server-side (no keys needed)."""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

import requests
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .services import (
    SCORE,
    classify_checkpoint,
    format_duration,
    haversine_miles,
    pick_recommendation,
    summarize_route,
)

UA = {"User-Agent": "spotter-takehome/1.0 (student project; contact via employer)"}
REQ_TIMEOUT = 30

OSRM_URL = "https://router.project-osrm.org/route/v1/driving/{coords}"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OPENMETEO_URL = "https://api.open-meteo.com/v1/forecast"


def _err(message: str, status: int = 400):
    return JsonResponse({"error": message}, status=status)


@require_GET
def geocode(request):
    q = request.GET.get("q", "").strip()
    if len(q) < 2:
        return _err("Query too short.")
    try:
        resp = requests.get(
            NOMINATIM_URL,
            params={"q": q, "format": "jsonv2", "limit": 5, "countrycodes": "us"},
            headers=UA,
            timeout=REQ_TIMEOUT,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        return _err(f"Geocoding service unavailable: {exc}", 502)
    out = [
        {
            "display_name": r.get("display_name", ""),
            "lat": float(r["lat"]),
            "lon": float(r["lon"]),
        }
        for r in resp.json()
    ]
    return JsonResponse({"results": out})


def _osrm_routes(o_lat, o_lon, d_lat, d_lon):
    coords = f"{o_lon},{o_lat};{d_lon},{d_lat}"
    resp = requests.get(
        OSRM_URL.format(coords=coords),
        params={"alternatives": 3, "overview": "full", "geometries": "geojson"},
        timeout=REQ_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != "Ok":
        raise RuntimeError(f"OSRM error: {data.get('code')}")
    return data.get("routes", [])


# Small in-process cache: a repeated plan (same points + window) should
# not hit Open-Meteo again. Forecast data changes slowly, so 30 minutes
# is safe, and it stops a retry loop from re-triggering the rate limit.
_FORECAST_CACHE: dict = {}
_FORECAST_TTL_S = 1800


def _openmeteo_forecast(lats: list[float], lons: list[float], start_date=None, end_date=None):
    """Batch forecast for many points; returns list of hourly dicts aligned
    with the input points.

    Rate-limit fix (was: forecast_days=16 for every route, three times
    per plan, with no retry):
    - Request only the days actually needed around the departure/ETA
      window instead of 16 days of hourly data per point. That cuts
      each request to roughly an eighth of its old size.
    - Cache a successful batch for 30 minutes, keyed by points + window.
    - On HTTP 429, respect Retry-After, otherwise back off before
      retrying, and never surface the raw request URL in an error.
    """
    cache_key = (
        tuple(f"{v:.5f}" for v in lats),
        tuple(f"{v:.5f}" for v in lons),
        start_date,
        end_date,
    )
    cached = _FORECAST_CACHE.get(cache_key)
    if cached and time.monotonic() - cached[0] < _FORECAST_TTL_S:
        return cached[1]

    params = {
        "latitude": ",".join(f"{v:.5f}" for v in lats),
        "longitude": ",".join(f"{v:.5f}" for v in lons),
        "hourly": "wind_speed_10m,precipitation,snowfall",
        "wind_speed_unit": "mph",
        "precipitation_unit": "inch",
        "timezone": "auto",
    }
    if start_date and end_date:
        params["start_date"] = start_date
        params["end_date"] = end_date
    else:
        params["forecast_days"] = 3

    last_exc = None
    for attempt in range(4):
        try:
            resp = requests.get(OPENMETEO_URL, params=params, timeout=REQ_TIMEOUT)
            resp.raise_for_status()
            break
        except requests.HTTPError as exc:
            last_exc = exc
            status = exc.response.status_code if exc.response is not None else None
            if status != 429 or attempt == 3:
                break
            retry_after = None
            try:
                retry_after = float(exc.response.headers.get("Retry-After", ""))
            except (TypeError, ValueError):
                retry_after = None
            time.sleep(retry_after if retry_after else (2, 5, 10)[attempt])
    else:
        raise RuntimeError("Weather service is rate-limited. Please wait a minute and try again.")
    if last_exc is not None:
        status = last_exc.response.status_code if last_exc.response is not None else None
        if status == 429:
            raise RuntimeError("Weather service is rate-limited. Please wait a minute and try again.")
        raise RuntimeError(f"Weather service returned HTTP {status}." if status else "Weather service unavailable.")
    data = resp.json()
    # Single location -> {"hourly": {...}}; multiple locations -> top-level
    # list of per-location objects, each with its own "hourly" dict.
    if isinstance(data, list):
        result = [loc["hourly"] for loc in data]
    else:
        hourly = data.get("hourly")
        result = [hourly] if isinstance(hourly, dict) else hourly
    _FORECAST_CACHE[cache_key] = (time.monotonic(), result)
    return result


def _weather_at(hourly: dict, when: datetime):
    """Pick the forecast hour matching `when`; clamp to range ends."""
    times = hourly["time"]
    target = when.strftime("%Y-%m-%dT%H:00")
    if target in times:
        idx = times.index(target)
    else:
        # Nearest available hour (clamp).
        idx = 0
        best = None
        for i, t in enumerate(times):
            try:
                dt = datetime.fromisoformat(t)
            except ValueError:
                continue
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            delta = abs((dt - when).total_seconds())
            if best is None or delta < best:
                best, idx = delta, i
    return {
        "wind_mph": float(hourly["wind_speed_10m"][idx] or 0.0),
        "rain_in_hr": float(hourly["precipitation"][idx] or 0.0),
        # Open-Meteo snowfall is cm -> convert to in/hr.
        "snow_in_hr": float(hourly["snowfall"][idx] or 0.0) / 2.54,
        "hour_iso": times[idx],
    }


def _build_checkpoints(geometry, spacing_mi, departure, duration_s, forecasts, load_lb):
    """Walk route geometry; place a checkpoint every spacing_mi (plus the
    final destination); attach ETA, weather and risk."""
    coords = geometry["coordinates"]  # [lon, lat]
    # Cumulative distance at each vertex.
    cum = [0.0]
    for (lon1, lat1), (lon2, lat2) in zip(coords, coords[1:]):
        cum.append(cum[-1] + haversine_miles(lat1, lon1, lat2, lon2))
    total = cum[-1]

    targets = [0.0]
    d = spacing_mi
    while d < total:
        targets.append(d)
        d += spacing_mi
    if targets[-1] < total:
        targets.append(total)

    checkpoints = []
    for n, t in enumerate(targets):
        # Interpolate vertex position for distance t.
        i = next(k for k in range(len(cum) - 1, -1, -1) if cum[k] <= t)
        if i >= len(coords) - 1:
            lon, lat = coords[-1]
        else:
            span = cum[i + 1] - cum[i] or 1e-9
            f = (t - cum[i]) / span
            (lon1, lat1), (lon2, lat2) = coords[i], coords[i + 1]
            lon, lat = lon1 + f * (lon2 - lon1), lat1 + f * (lat2 - lat1)
        eta = departure + timedelta(seconds=(t / total) * duration_s) if total else departure
        wx = _weather_at(forecasts[n], eta)
        level, reasons = classify_checkpoint(
            wx["wind_mph"], wx["rain_in_hr"], wx["snow_in_hr"], load_lb
        )
        checkpoints.append(
            {
                "n": n,
                "lat": round(lat, 5),
                "lon": round(lon, 5),
                "miles_along": round(t, 1),
                "eta_iso": eta.isoformat(),
                "wind_mph": round(wx["wind_mph"], 1),
                "rain_in_hr": round(wx["rain_in_hr"], 3),
                "snow_in_hr": round(wx["snow_in_hr"], 3),
                "risk": level,
                "risk_score": SCORE[level],
                "reasons": reasons,
            }
        )
    return checkpoints, round(total, 1)


@csrf_exempt
@require_POST
def plan(request):
    try:
        body = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return _err("Invalid JSON body.")
    try:
        o = body["origin"]
        d = body["destination"]
        o_lat, o_lon = float(o["lat"]), float(o["lon"])
        d_lat, d_lon = float(d["lat"]), float(d["lon"])
        departure = datetime.fromisoformat(body["departure_iso"])
        load_lb = float(body.get("load_lb", 40000))
        spacing = float(body.get("spacing_miles", 25))
    except (KeyError, TypeError, ValueError) as exc:
        return _err(f"Missing/invalid fields: {exc}")
    if departure.tzinfo is None:
        departure = departure.replace(tzinfo=timezone.utc)
    if spacing not in (10, 25, 50):
        return _err("spacing_miles must be 10, 25 or 50.")
    if load_lb <= 0 or load_lb > 120_000:
        return _err("load_lb out of range.")

    try:
        osrm_routes = _osrm_routes(o_lat, o_lon, d_lat, d_lon)
    except Exception as exc:  # noqa: BLE001 - surface as 502
        return _err(f"Routing service unavailable: {exc}", 502)
    if not osrm_routes:
        return _err("No routes found.", 502)

    notes = []
    if len(osrm_routes) < 3:
        notes.append(
            f"OSRM returned {len(osrm_routes)} route(s) instead of 3; "
            "planning with what is available."
        )

    routes = []
    for idx, r in enumerate(osrm_routes):
        dist_mi = r["distance"] / 1609.344
        dur_s = r["duration"]
        geometry = r["geometry"]
        # Two passes: first find checkpoint positions, then fetch one batched
        # Open-Meteo forecast for all of them in a single request.
        positions = _checkpoint_positions(geometry, spacing)
        try:
            forecasts = _openmeteo_forecast(
                [p[0] for p in positions],
                [p[1] for p in positions],
                start_date=(departure - timedelta(days=1)).date().isoformat(),
                end_date=(departure + timedelta(days=2)).date().isoformat(),
            )
        except Exception as exc:  # noqa: BLE001
            return _err(f"Weather service unavailable: {exc}", 502)
        checkpoints, _ = _build_checkpoints(
            geometry, spacing, departure, dur_s, forecasts, load_lb
        )
        summary = summarize_route(checkpoints)
        routes.append(
            {
                "index": idx,
                "distance_mi": round(dist_mi, 1),
                "duration_s": int(dur_s),
                "duration_text": format_duration(dur_s),
                # Frontend-friendly [lat, lon] pairs.
                "geometry": [[round(c[1], 5), round(c[0], 5)] for c in geometry["coordinates"]],
                "checkpoints": checkpoints,
                "summary": summary,
            }
        )

    recommended_index, reason = pick_recommendation(routes)
    return JsonResponse(
        {
            "routes": routes,
            "recommended_index": recommended_index,
            "recommendation_reason": reason,
            "notes": notes,
            "assumptions": [
                "Checkpoint ETA = departure + (miles_along / route_miles) × route duration "
                "(uniform average speed along the route).",
                "Each segment between checkpoints takes its starting checkpoint's risk level.",
            ],
        }
    )


def _checkpoint_positions(geometry, spacing_mi):
    """(lat, lon) positions where checkpoints will fall (no weather)."""
    coords = geometry["coordinates"]
    cum = [0.0]
    for (lon1, lat1), (lon2, lat2) in zip(coords, coords[1:]):
        cum.append(cum[-1] + haversine_miles(lat1, lon1, lat2, lon2))
    total = cum[-1]
    targets = [0.0]
    d = spacing_mi
    while d < total:
        targets.append(d)
        d += spacing_mi
    if targets[-1] < total:
        targets.append(total)
    out = []
    for t in targets:
        i = next(k for k in range(len(cum) - 1, -1, -1) if cum[k] <= t)
        if i >= len(coords) - 1:
            lon, lat = coords[-1]
        else:
            span = cum[i + 1] - cum[i] or 1e-9
            f = (t - cum[i]) / span
            (lon1, lat1), (lon2, lat2) = coords[i], coords[i + 1]
            lon, lat = lon1 + f * (lon2 - lon1), lat1 + f * (lat2 - lat1)
        out.append((lat, lon))
    return out


@csrf_exempt
@require_POST
def heatmap(request):
    try:
        body = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return _err("Invalid JSON body.")
    try:
        points = body["points"]  # [[lat, lon], ...]
        departure = datetime.fromisoformat(body["departure_iso"])
        hour_offset = int(body.get("hour_offset", 0))
        load_lb = float(body.get("load_lb", 40000))
    except (KeyError, TypeError, ValueError) as exc:
        return _err(f"Missing/invalid fields: {exc}")
    if departure.tzinfo is None:
        departure = departure.replace(tzinfo=timezone.utc)
    if not (0 <= hour_offset <= 48):
        return _err("hour_offset must be 0–48.")
    if not points or len(points) > 400:
        return _err("points must be 1–400 entries.")

    when = departure + timedelta(hours=hour_offset)
    try:
        forecasts = _openmeteo_forecast(
            [float(p[0]) for p in points],
            [float(p[1]) for p in points],
            start_date=(when - timedelta(days=1)).date().isoformat(),
            end_date=(when + timedelta(days=1)).date().isoformat(),
        )
    except Exception as exc:  # noqa: BLE001
        return _err(f"Weather service unavailable: {exc}", 502)

    out = []
    for (lat, lon), hourly in zip(points, forecasts):
        wx = _weather_at(hourly, when)
        level, _ = classify_checkpoint(
            wx["wind_mph"], wx["rain_in_hr"], wx["snow_in_hr"], load_lb
        )
        out.append(
            {
                "lat": round(float(lat), 5),
                "lon": round(float(lon), 5),
                "intensity": SCORE[level],
                "level": level,
                "wind_mph": round(wx["wind_mph"], 1),
                "rain_in_hr": round(wx["rain_in_hr"], 3),
                "snow_in_hr": round(wx["snow_in_hr"], 3),
            }
        )
    return JsonResponse({"hour_iso": when.isoformat(), "points": out})

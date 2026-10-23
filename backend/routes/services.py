"""Weather-risk classification, route math, and recommendation logic."""
from __future__ import annotations

import math

# Canonical risk levels, ordered best -> worst.
LEVELS = ("low", "moderate", "high", "severe", "no_travel")
SCORE = {name: i for i, name in enumerate(LEVELS)}

# Wind (mph) thresholds -> score boundaries.
WIND_BANDS = ((25, 0), (35, 1), (45, 2), (55, 3), (float("inf"), 4))
# Rain (in/hr).
RAIN_BANDS = ((0.10, 0), (0.25, 1), (0.50, 2), (1.00, 3), (float("inf"), 4))
# Snow (in/hr).
SNOW_BANDS = ((0.5, 0), (1.0, 1), (2.0, 2), (3.0, 3), (float("inf"), 4))


def _band(value: float, bands) -> int:
    for edge, score in bands:
        if value < edge:
            return score
    return 4


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in statute miles."""
    r = 3958.7613  # earth radius in miles
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def classify_checkpoint(
    wind_mph: float, rain_in_hr: float, snow_in_hr: float, load_lb: float
) -> tuple[str, list[str]]:
    """Classify weather risk at one checkpoint, applying load rules.

    Returns (level_name, reasons). Base level is the worst of wind/rain/snow;
    load rules can only escalate.
    """
    reasons: list[str] = []
    scores: list[int] = []

    w = _band(wind_mph, WIND_BANDS)
    scores.append(w)
    if w >= 1:
        reasons.append(f"Wind {wind_mph:.0f} mph ({LEVELS[w]})")

    r = _band(rain_in_hr, RAIN_BANDS)
    scores.append(r)
    if r >= 1:
        reasons.append(f"Rain {rain_in_hr:.2f} in/hr ({LEVELS[r]})")

    s = _band(snow_in_hr, SNOW_BANDS)
    scores.append(s)
    if s >= 1:
        reasons.append(f"Snow {snow_in_hr:.2f} in/hr ({LEVELS[s]})")

    level = LEVELS[max(scores)] if scores else "low"

    # Load rules (only escalate).
    if wind_mph >= 55:
        if level != "no_travel":
            reasons.append(f"Wind {wind_mph:.0f} mph \u2265 55 \u2192 No Travel for any load")
        level = "no_travel"
    elif 45 <= wind_mph <= 54 and load_lb > 30_000:
        if level != "no_travel":
            reasons.append(
                f"Wind {wind_mph:.0f} mph (45\u201354) + load {load_lb:,.0f} lb > 30,000 lb \u2192 No Travel"
            )
        level = "no_travel"
    elif 35 <= wind_mph <= 44 and load_lb > 40_000:
        if SCORE[level] < SCORE["severe"]:
            reasons.append(
                f"Wind {wind_mph:.0f} mph (35\u201344) + load {load_lb:,.0f} lb > 40,000 lb \u2192 Severe"
            )
            level = "severe"

    if level == "low" and not reasons:
        reasons.append("All conditions low")
    return level, reasons


def summarize_route(checkpoints: list[dict]) -> dict:
    """Per-route summary from checkpoints.

    Segment between checkpoints i and i+1 takes checkpoint i's risk level.
    Returns miles per level, averages, worst level, and the sort keys used
    for recommendation.
    """
    miles_per_level = {name: 0.0 for name in LEVELS}
    weighted = 0.0
    total = 0.0
    worst = 0
    for a, b in zip(checkpoints, checkpoints[1:]):
        seg = haversine_miles(a["lat"], a["lon"], b["lat"], b["lon"])
        score = SCORE[a["risk"]]
        miles_per_level[a["risk"]] += seg
        weighted += score * seg
        total += seg
        worst = max(worst, score)
    avg = weighted / total if total else 0.0
    return {
        "miles_per_level": {k: round(v, 1) for k, v in miles_per_level.items()},
        "total_mi": round(total, 1),
        "avg_risk": round(avg, 2),
        "avg_risk_label": LEVELS[round(avg)] if total else "low",
        "worst_level": LEVELS[worst],
        "no_travel_mi": round(miles_per_level["no_travel"], 1),
        "severe_mi": round(miles_per_level["severe"], 1),
        "high_mi": round(miles_per_level["high"], 1),
    }


def pick_recommendation(routes: list[dict]) -> tuple[int, str]:
    """Order: fewest no-travel miles, fewest severe miles, fewest high miles,
    lowest average risk, shortest travel time. Returns (index, reason)."""
    def key(r: dict):
        s = r["summary"]
        return (
            s["no_travel_mi"],
            s["severe_mi"],
            s["high_mi"],
            s["avg_risk"],
            r["duration_s"],
        )

    ordered = sorted(range(len(routes)), key=lambda i: key(routes[i]))
    best = ordered[0]
    b, s = routes[best], routes[best]["summary"]
    bits = []
    if s["no_travel_mi"] == 0:
        bits.append("no no-travel miles")
    else:
        bits.append(f'{s["no_travel_mi"]} mi of no-travel weather')
    bits.append(f'{s["severe_mi"]} severe mi')
    bits.append(f'{s["high_mi"]} high mi')
    bits.append(f'avg risk {s["avg_risk"]:.2f}')
    bits.append(f'{b["duration_text"]}')
    reason = (
        f"Route {best + 1} ranks best: " + ", ".join(bits) + "."
    )
    if len(routes) > 1 and ordered[1] is not None:
        runner = routes[ordered[1]]["summary"]
        if runner["severe_mi"] > s["severe_mi"] or runner["no_travel_mi"] > s["no_travel_mi"]:
            reason += " It beats the next-best route on severe/no-travel mileage."
    return best, reason


def format_duration(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    if h:
        return f"{h}h {m}m"
    return f"{m}m"

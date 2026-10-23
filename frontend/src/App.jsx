import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import MapView from "./MapView";
import { geocode, planRoute, heatmap, RISK_COLORS, RISK_LABELS } from "./api";
import "./styles.css";

function useDebouncedGeocode(query) {
  const [results, setResults] = useState([]);
  const timer = useRef(null);
  useEffect(() => {
    clearTimeout(timer.current);
    if (!query || query.length < 3) {
      setResults([]);
      return;
    }
    timer.current = setTimeout(async () => {
      try {
        const data = await geocode(query);
        setResults(data.results || []);
      } catch {
        setResults([]);
      }
    }, 350);
    return () => clearTimeout(timer.current);
  }, [query]);
  return results;
}

function PlaceInput({ label, value, onPick }) {
  const [open, setOpen] = useState(false);
  const suggestions = useDebouncedGeocode(value.text);
  return (
    <div className="field place-field">
      <label>{label}</label>
      <input
        value={value.text}
        placeholder="City, State"
        onChange={(e) => {
          onPick({ text: e.target.value, lat: null, lon: null });
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
      />
      {open && suggestions.length > 0 && (
        <ul className="suggestions">
          {suggestions.map((s, i) => (
            <li
              key={i}
              onMouseDown={() =>
                onPick({ text: s.display_name.split(",").slice(0, 3).join(","), lat: s.lat, lon: s.lon })
              }
            >
              {s.display_name}
            </li>
          ))}
        </ul>
      )}
      {value.lat == null && value.text.length > 2 && (
        <small className="hint">Pick a suggestion to pin the location.</small>
      )}
    </div>
  );
}

function defaultDeparture() {
  const d = new Date();
  d.setDate(d.getDate() + 1);
  d.setHours(8, 0, 0, 0);
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(
    d.getMinutes()
  )}`;
}

export default function App() {
  const [origin, setOrigin] = useState({ text: "Dallas, TX", lat: 32.7767, lon: -96.797 });
  const [destination, setDestination] = useState({ text: "Houston, TX", lat: 29.7604, lon: -95.3698 });
  const [departure, setDeparture] = useState(defaultDeparture());
  const [loadLb, setLoadLb] = useState("40000");
  const [spacing, setSpacing] = useState("25");
  const [plan, setPlan] = useState(null);
  const [selectedRoute, setSelectedRoute] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [heatOn, setHeatOn] = useState(false);
  const [hourOffset, setHourOffset] = useState(0);
  const [heatPoints, setHeatPoints] = useState([]);
  const [heatLoading, setHeatLoading] = useState(false);
  const heatTimer = useRef(null);

  const canPlan = origin.lat != null && destination.lat != null && !loading;

  const corridor = useMemo(() => {
    if (!plan) return [];
    const geom = plan.routes[selectedRoute].geometry;
    const step = Math.max(1, Math.floor(geom.length / 120));
    return geom.filter((_, i) => i % step === 0);
  }, [plan, selectedRoute]);

  const fetchHeat = useCallback(
    async (offset) => {
      if (!plan || corridor.length === 0) return;
      setHeatLoading(true);
      try {
        const data = await heatmap({
          points: corridor,
          departure_iso: new Date(departure).toISOString(),
          hour_offset: offset,
          load_lb: Number(loadLb) || 40000,
        });
        setHeatPoints(data.points);
      } catch (e) {
        setHeatPoints([]);
      } finally {
        setHeatLoading(false);
      }
    },
    [plan, corridor, departure, loadLb]
  );

  // Refresh heat points when the slider moves (debounced) or route changes.
  useEffect(() => {
    if (!heatOn || !plan) {
      setHeatPoints([]);
      return;
    }
    clearTimeout(heatTimer.current);
    heatTimer.current = setTimeout(() => fetchHeat(hourOffset), 300);
    return () => clearTimeout(heatTimer.current);
  }, [heatOn, hourOffset, fetchHeat, plan]);

  async function onPlan(e) {
    e?.preventDefault();
    if (!canPlan) return;
    setLoading(true);
    setError("");
    setPlan(null);
    try {
      const data = await planRoute({
        origin: { lat: origin.lat, lon: origin.lon },
        destination: { lat: destination.lat, lon: destination.lon },
        departure_iso: new Date(departure).toISOString(),
        load_lb: Number(loadLb) || 0,
        spacing_miles: Number(spacing),
      });
      setPlan(data);
      setSelectedRoute(data.recommended_index);
    } catch (err) {
      setError(err.message || "Planning failed.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="app">
      <header>
        <h1>Weather-Aware Truck Routing</h1>
        <p>Pick the safest route for your load — weather risk vs. travel time.</p>
      </header>

      <form className="panel form" onSubmit={onPlan}>
        <PlaceInput label="Origin" value={origin} onPick={setOrigin} />
        <PlaceInput label="Destination" value={destination} onPick={setDestination} />
        <div className="field">
          <label>Departure</label>
          <input
            type="datetime-local"
            value={departure}
            onChange={(e) => setDeparture(e.target.value)}
          />
        </div>
        <div className="field">
          <label>Load weight (lb)</label>
          <input
            type="number"
            min="1"
            max="120000"
            value={loadLb}
            onChange={(e) => setLoadLb(e.target.value)}
          />
        </div>
        <div className="field">
          <label>Checkpoint spacing</label>
          <select value={spacing} onChange={(e) => setSpacing(e.target.value)}>
            <option value="10">Every 10 miles</option>
            <option value="25">Every 25 miles</option>
            <option value="50">Every 50 miles</option>
          </select>
        </div>
        <button type="submit" disabled={!canPlan} className="primary">
          {loading ? "Planning routes…" : "Plan routes"}
        </button>
      </form>

      {error && <div className="banner error">{error}</div>}
      {loading && <div className="banner info">Fetching routes and weather — this can take ~20 seconds…</div>}

      {plan && (
        <>
          {plan.notes?.map((n, i) => (
            <div key={i} className="banner warn">
              {n}
            </div>
          ))}
          <div className="layout">
            <div className="map-wrap">
              <MapView
                plan={plan}
                selectedRoute={selectedRoute}
                onSelectRoute={setSelectedRoute}
                heatPoints={heatPoints}
                heatOn={heatOn}
              />
              <div className="heat-controls">
                <label>
                  <input
                    type="checkbox"
                    checked={heatOn}
                    onChange={(e) => setHeatOn(e.target.checked)}
                  />{" "}
                  Weather heatmap
                </label>
                {heatOn && (
                  <label className="slider">
                    Forecast +{hourOffset}h
                    <input
                      type="range"
                      min="0"
                      max="48"
                      value={hourOffset}
                      onChange={(e) => setHourOffset(Number(e.target.value))}
                    />
                    {heatLoading && <small> loading…</small>}
                  </label>
                )}
              </div>
            </div>
            <div className="cards">
              <div className="recommend">
                <b>Recommended: Route {plan.recommended_index + 1}</b>
                <p>{plan.recommendation_reason}</p>
              </div>
              {plan.routes.map((r, i) => (
                <button
                  key={i}
                  className={`card ${i === selectedRoute ? "active" : ""} ${
                    i === plan.recommended_index ? "recommended" : ""
                  }`}
                  onClick={() => setSelectedRoute(i)}
                >
                  <div className="card-head">
                    <b>Route {i + 1}</b>
                    {i === plan.recommended_index && <span className="badge">RECOMMENDED</span>}
                  </div>
                  <div className="card-stats">
                    <span>{r.distance_mi} mi</span>
                    <span>{r.duration_text}</span>
                    <span>{r.checkpoints.length} checkpoints</span>
                  </div>
                  <div className="risk-bars">
                    {Object.entries(r.summary.miles_per_level).map(([lvl, mi]) =>
                      mi > 0 ? (
                        <span
                          key={lvl}
                          className="risk-chip"
                          style={{ background: RISK_COLORS[lvl] }}
                          title={`${RISK_LABELS[lvl]}: ${mi} mi`}
                        >
                          {mi} mi {RISK_LABELS[lvl]}
                        </span>
                      ) : null
                    )}
                  </div>
                  <div className="card-foot">
                    Avg risk {r.summary.avg_risk.toFixed(2)} · Worst: {RISK_LABELS[r.summary.worst_level]}
                  </div>
                </button>
              ))}
              <div className="assumptions">
                <b>Assumptions</b>
                <ul>
                  {plan.assumptions.map((a, i) => (
                    <li key={i}>{a}</li>
                  ))}
                </ul>
              </div>
            </div>
          </div>
        </>
      )}
      <footer>Routes: OSRM · Weather: Open-Meteo · Geocoding: Nominatim (all free, no keys).</footer>
    </div>
  );
}

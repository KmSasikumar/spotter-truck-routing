/**
 * Thin wrapper around the Django JSON API.
 * In dev, Vite proxies /api to Django (see vite.config.js).
 * In production builds, set VITE_API_URL to the backend base URL
 * (e.g. https://spotter-truck-routing-api.onrender.com) — no trailing slash.
 */
const API_BASE = (import.meta.env.VITE_API_URL || "").replace(/\/+$/, "");
export const RISK_COLORS = {
  low: "#27ae60",
  moderate: "#f5b301",
  high: "#e67e22",
  severe: "#d63031",
  no_travel: "#5b0f0f",
};

export const RISK_LABELS = {
  low: "Low",
  moderate: "Moderate",
  high: "High",
  severe: "Severe",
  no_travel: "No Travel",
};

async function asJson(res) {
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

export async function geocode(query) {
  const res = await fetch(`${API_BASE}/api/geocode/?q=${encodeURIComponent(query)}`);
  return asJson(res);
}

export async function planRoute(payload) {
  const res = await fetch(`${API_BASE}/api/plan/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return asJson(res);
}

export async function heatmap(payload) {
  const res = await fetch(`${API_BASE}/api/heatmap/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return asJson(res);
}

export function formatEta(iso) {
  try {
    return new Date(iso).toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

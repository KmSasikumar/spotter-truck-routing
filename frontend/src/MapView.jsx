import { useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import "leaflet.heat";
import { RISK_COLORS, RISK_LABELS, formatEta } from "./api";

// Fix default marker icons (Vite doesn't bundle Leaflet's image paths).
import markerIcon2x from "leaflet/dist/images/marker-icon-2x.png";
import markerIcon from "leaflet/dist/images/marker-icon.png";
import markerShadow from "leaflet/dist/images/marker-shadow.png";
delete L.Icon.Default.prototype._getIconUrl;
L.Icon.Default.mergeOptions({
  iconRetinaUrl: markerIcon2x,
  iconUrl: markerIcon,
  shadowUrl: markerShadow,
});

const ROUTE_COLORS = ["#2ecc71", "#3498db", "#9b59b6"];

function checkpointPopup(cp) {
  const rows = [
    `<b>Checkpoint ${cp.n + 1}</b> — ${cp.miles_along} mi`,
    `ETA: ${formatEta(cp.eta_iso)}`,
    `Wind: ${cp.wind_mph} mph · Rain: ${cp.rain_in_hr} in/hr · Snow: ${cp.snow_in_hr} in/hr`,
    `<b style="color:${RISK_COLORS[cp.risk]}">${RISK_LABELS[cp.risk]}</b>`,
    ...cp.reasons.map((r) => `· ${r}`),
  ];
  return rows.join("<br/>");
}

/**
 * Renders the Leaflet map: route polylines, risk-colored checkpoint markers,
 * origin/destination pins, optional weather heat layer, and a risk legend.
 */
export default function MapView({ plan, selectedRoute, onSelectRoute, heatPoints, heatOn }) {
  const mapRef = useRef(null);
  const divRef = useRef(null);
  const layerGroupRef = useRef(null);
  const heatRef = useRef(null);

  // Init map once.
  useEffect(() => {
    const map = L.map(divRef.current).setView([32.8, -96.8], 7);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "&copy; OpenStreetMap contributors",
      maxZoom: 18,
    }).addTo(map);
    layerGroupRef.current = L.layerGroup().addTo(map);
    mapRef.current = map;

    const legend = L.control({ position: "bottomright" });
    legend.onAdd = () => {
      const div = L.DomUtil.create("div", "risk-legend");
      div.innerHTML =
        "<b>Risk level</b><br/>" +
        Object.entries(RISK_COLORS)
          .map(
            ([k, c]) =>
              `<span class="legend-row"><i style="background:${c}"></i>${RISK_LABELS[k]}</span>`
          )
          .join("");
      return div;
    };
    legend.addTo(map);
    return () => map.remove();
  }, []);

  // Render routes + checkpoints whenever the plan or selection changes.
  useEffect(() => {
    const map = mapRef.current;
    const group = layerGroupRef.current;
    if (!map || !group || !plan) return;
    group.clearLayers();

    const bounds = [];
    plan.routes.forEach((route, i) => {
      const isSelected = i === selectedRoute;
      const line = L.polyline(route.geometry, {
        color: ROUTE_COLORS[i % ROUTE_COLORS.length],
        weight: isSelected ? 7 : 4,
        opacity: isSelected ? 1 : 0.55,
      });
      line.on("click", () => onSelectRoute(i));
      line.bindTooltip(
        `Route ${i + 1} — ${route.distance_mi} mi, ${route.duration_text}${
          i === plan.recommended_index ? " · RECOMMENDED" : ""
        }`,
        { sticky: true }
      );
      line.addTo(group);
      bounds.push(...route.geometry);
    });

    // Checkpoints for the selected route.
    const route = plan.routes[selectedRoute];
    route.checkpoints.forEach((cp) => {
      L.circleMarker([cp.lat, cp.lon], {
        radius: cp.n === 0 || cp.n === route.checkpoints.length - 1 ? 8 : 6,
        color: "#222",
        weight: 1.5,
        fillColor: RISK_COLORS[cp.risk],
        fillOpacity: 0.95,
      })
        .bindPopup(checkpointPopup(cp))
        .addTo(group);
    });

    const first = route.checkpoints[0];
    const last = route.checkpoints[route.checkpoints.length - 1];
    L.marker([first.lat, first.lon]).bindPopup("Origin").addTo(group);
    L.marker([last.lat, last.lon]).bindPopup("Destination").addTo(group);

    if (bounds.length) map.fitBounds(bounds, { padding: [30, 30] });
  }, [plan, selectedRoute]); // eslint-disable-line react-hooks/exhaustive-deps

  // Heat layer.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (heatRef.current) {
      map.removeLayer(heatRef.current);
      heatRef.current = null;
    }
    if (heatOn && heatPoints && heatPoints.length) {
      heatRef.current = L.heatLayer(
        heatPoints.map((p) => [p.lat, p.lon, Math.max(p.intensity, 0.05) / 4]),
        { radius: 28, blur: 18, max: 1, maxZoom: 12, gradient: {
          0.0: "#27ae60", 0.25: "#f5b301", 0.5: "#e67e22", 0.75: "#d63031", 1.0: "#5b0f0f",
        }}
      ).addTo(map);
    }
  }, [heatPoints, heatOn]);

  return <div ref={divRef} className="map" />;
}

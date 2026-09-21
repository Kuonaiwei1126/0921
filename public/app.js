/* 台灣氣象觀測地圖：載入 data/latest.json（GeoJSON），以 Leaflet 畫測站圓點。
   設計依 design.md §7.2：Canvas renderer、氣溫色階、縣市篩選、過期提示。 */

const MISSING_COLOR = "#9ca3af"; // 缺值灰

// 各變數的色階：由低到高，取第一個 value < max 的顏色；圖例同時標示文字（§7.2）
const VARS = {
  temp: {
    label: "氣溫", unit: "°C",
    scale: [
      { max: 20, color: "#3b82f6", label: "< 20" },
      { max: 25, color: "#22c55e", label: "20–25" },
      { max: 30, color: "#f97316", label: "25–30" },
      { max: Infinity, color: "#ef4444", label: "≥ 30" },
    ],
  },
  rain: {
    label: "雨量", unit: "mm",
    scale: [
      { max: 0.1, color: "#cbd5e1", label: "0" },
      { max: 10, color: "#93c5fd", label: "0–10" },
      { max: 30, color: "#3b82f6", label: "10–30" },
      { max: 80, color: "#1d4ed8", label: "30–80" },
      { max: Infinity, color: "#7c3aed", label: "≥ 80" },
    ],
  },
  humidity: {
    label: "濕度", unit: "%",
    scale: [
      { max: 60, color: "#f97316", label: "< 60" },
      { max: 75, color: "#facc15", label: "60–75" },
      { max: 90, color: "#22c55e", label: "75–90" },
      { max: Infinity, color: "#1d4ed8", label: "≥ 90" },
    ],
  },
};

let currentVar = "temp";
let currentCounty = "";
let allData = null;
let geoLayer = null;

const map = L.map("map", { preferCanvas: true }).setView([23.7, 121.0], 7);
const renderer = L.canvas({ padding: 0.5 });

L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 18,
  attribution: "© OpenStreetMap contributors",
}).addTo(map);

function colorFor(value, varKey) {
  if (value === null || value === undefined) return MISSING_COLOR;
  for (const step of VARS[varKey].scale) {
    if (value < step.max) return step.color;
  }
  return MISSING_COLOR;
}

function radiusForZoom(z) {
  return z >= 11 ? 8 : z >= 9 ? 6 : 4.5;
}

function fmt(value, unit) {
  return value === null || value === undefined ? "—" : `${value} ${unit}`;
}

function fmtTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d) ? iso : d.toLocaleString("zh-TW", { hour12: false });
}

function popupHtml(p) {
  const rows = [
    ["縣市鄉鎮", `${p.county ?? ""} ${p.town ?? ""}`],
    ["觀測時間", fmtTime(p.obsTime)],
    ["天氣", p.weather ?? "—"],
    ["氣溫", fmt(p.temp, "°C")],
    ["濕度", fmt(p.humidity, "%")],
    ["雨量", fmt(p.rain, "mm")],
    ["風速", fmt(p.wind, "m/s")],
    ["風向", fmt(p.windDir, "°")],
    ["氣壓", fmt(p.pressure, "hPa")],
    ["海拔", fmt(p.altitude, "m")],
  ];
  const body = rows.map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`).join("");
  return `<strong>${p.name}</strong>（${p.id}）<table class="popup-table">${body}</table>`;
}

function render() {
  if (geoLayer) geoLayer.remove();
  const radius = radiusForZoom(map.getZoom());

  geoLayer = L.geoJSON(allData, {
    filter: (f) => !currentCounty || f.properties.county === currentCounty,
    pointToLayer: (feature, latlng) => {
      const p = feature.properties;
      const value = p[currentVar];
      const marker = L.circleMarker(latlng, {
        renderer,
        radius,
        fillColor: colorFor(value, currentVar),
        fillOpacity: 0.85,
        color: "#334155",
        weight: 0.6,
      });
      const v = VARS[currentVar];
      marker.bindTooltip(`${p.name}：${fmt(value, v.unit)}`, { direction: "top" });
      marker.bindPopup(popupHtml(p));
      return marker;
    },
  }).addTo(map);

  const shown = geoLayer.getLayers().length;
  document.getElementById("count").textContent = `顯示 ${shown} 站`;
  renderLegend();
}

function renderLegend() {
  const v = VARS[currentVar];
  const rows = v.scale
    .map((s) => `<div class="row"><span class="swatch" style="background:${s.color}"></span>${s.label} ${v.unit}</div>`)
    .join("");
  document.getElementById("legend").innerHTML =
    `<strong>${v.label}</strong>${rows}` +
    `<div class="row"><span class="swatch" style="background:${MISSING_COLOR}"></span>缺值</div>`;
}

function setupControls() {
  document.querySelectorAll(".var-switch button").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".var-switch button").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      currentVar = btn.dataset.var;
      render();
    });
  });

  const select = document.getElementById("county");
  const counties = [...new Set(allData.features.map((f) => f.properties.county).filter(Boolean))]
    .sort((a, b) => a.localeCompare(b, "zh-TW"));
  for (const c of counties) {
    const opt = document.createElement("option");
    opt.value = c;
    opt.textContent = c;
    select.appendChild(opt);
  }
  select.addEventListener("change", () => {
    currentCounty = select.value;
    render();
  });

  map.on("zoomend", () => {
    const r = radiusForZoom(map.getZoom());
    geoLayer?.eachLayer((l) => l.setRadius(r));
  });
}

function showMeta(meta) {
  const el = document.getElementById("updated");
  el.textContent = `資料時間：${fmtTime(meta.generatedAt)}`;
  const ageMs = Date.now() - new Date(meta.generatedAt).getTime();
  if (!isNaN(ageMs) && ageMs > 6 * 3600 * 1000) {
    document.getElementById("stale").hidden = false;
  }
}

fetch("data/latest.json")
  .then((r) => {
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  })
  .then((data) => {
    allData = data;
    showMeta(data.metadata ?? {});
    setupControls();
    render();
  })
  .catch((err) => {
    document.getElementById("updated").textContent = `資料載入失敗：${err.message}`;
  });

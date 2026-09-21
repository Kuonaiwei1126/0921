#!/usr/bin/env python3
"""從中央氣象署 O-A0001-001 抓取自動氣象站觀測，存入 SQLite 並匯出 GeoJSON。

授權碼只從環境變數 CWA_API_KEY 讀取（見 design.md §9.3）。
任何一步失敗即以非 0 結束，且不覆蓋舊的 public/data/latest.json（§7.1）。
"""
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "weather.db"
JSON_PATH = ROOT / "public" / "data" / "latest.json"
API_URL = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/O-A0001-001"

TAIPEI_TZ = timezone(timedelta(hours=8))

# 台灣範圍（§4.3）：超出即略過並警告
LAT_RANGE = (21.0, 27.0)
LON_RANGE = (118.0, 123.0)

SCHEMA = """
CREATE TABLE IF NOT EXISTS stations (
    station_id   TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    lat          REAL NOT NULL,
    lon          REAL NOT NULL,
    altitude     REAL,
    county       TEXT,
    town         TEXT,
    updated_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS observations (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    station_id   TEXT NOT NULL REFERENCES stations(station_id),
    obs_time     TEXT NOT NULL,
    weather      TEXT,
    temp_c       REAL,
    humidity_pct REAL,
    rain_mm      REAL,
    wind_ms      REAL,
    wind_dir     REAL,
    pressure_hpa REAL,
    fetched_at   TEXT NOT NULL,
    UNIQUE (station_id, obs_time)
);
CREATE INDEX IF NOT EXISTS idx_obs_time ON observations (obs_time);
"""


def fetch() -> list[dict]:
    key = os.environ.get("CWA_API_KEY")
    if not key:
        raise SystemExit("缺少環境變數 CWA_API_KEY")
    r = requests.get(API_URL, params={"Authorization": key}, timeout=60)
    r.raise_for_status()
    return r.json()["records"]["Station"]


def wgs84(station: dict) -> tuple[float, float] | None:
    for c in station["GeoInfo"]["Coordinates"]:
        if c["CoordinateName"] == "WGS84":
            return float(c["StationLatitude"]), float(c["StationLongitude"])
    return None


def clean(value) -> float | None:
    """缺值代碼（"-99"、-99 等）與非數字 → None，其餘轉 float。"""
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v <= -50:  # CWA 缺值代碼 -99；台灣測站不可能出現 -50 以下的實際值
        return None
    return v


def clean_text(value) -> str | None:
    """文字欄位（Weather）的缺值代碼 → None。"""
    if value is None:
        return None
    s = str(value).strip()
    return None if s in ("", "-99") else s


def save(conn: sqlite3.Connection, stations: list[dict]) -> tuple[int, int]:
    now = datetime.now(TAIPEI_TZ).isoformat(timespec="seconds")
    n_station, n_obs = 0, 0
    for s in stations:
        sid = s["StationId"]
        coord = wgs84(s)
        if coord is None:
            print(f"警告：{sid} 找不到 WGS84 座標，略過", file=sys.stderr)
            continue
        lat, lon = coord
        if not (LAT_RANGE[0] <= lat <= LAT_RANGE[1] and LON_RANGE[0] <= lon <= LON_RANGE[1]):
            print(f"警告：{sid} 座標 ({lat}, {lon}) 超出台灣範圍，略過", file=sys.stderr)
            continue

        geo = s["GeoInfo"]
        conn.execute(
            """INSERT INTO stations (station_id, name, lat, lon, altitude, county, town, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(station_id) DO UPDATE SET
                 name=excluded.name, lat=excluded.lat, lon=excluded.lon,
                 altitude=excluded.altitude, county=excluded.county,
                 town=excluded.town, updated_at=excluded.updated_at""",
            (sid, s["StationName"], lat, lon, clean(geo.get("StationAltitude")),
             geo.get("CountyName"), geo.get("TownName"), now),
        )
        n_station += 1

        obs_time = s["ObsTime"]["DateTime"]
        if not obs_time or str(obs_time).strip() == "-99":
            print(f"警告：{sid} 觀測時間無效，略過觀測值", file=sys.stderr)
            continue
        w = s["WeatherElement"]
        cur = conn.execute(
            """INSERT INTO observations
               (station_id, obs_time, weather, temp_c, humidity_pct, rain_mm,
                wind_ms, wind_dir, pressure_hpa, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(station_id, obs_time) DO NOTHING""",
            (sid, obs_time, clean_text(w.get("Weather")),
             clean(w.get("AirTemperature")), clean(w.get("RelativeHumidity")),
             clean(w.get("Now", {}).get("Precipitation")),
             clean(w.get("WindSpeed")), clean(w.get("WindDirection")),
             clean(w.get("AirPressure")), now),
        )
        n_obs += cur.rowcount

    conn.execute("DELETE FROM observations WHERE obs_time < datetime('now', '-7 days')")
    conn.commit()
    return n_station, n_obs


def export(conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        """SELECT s.station_id, s.name, s.county, s.town, s.altitude, s.lat, s.lon,
                  o.obs_time, o.weather, o.temp_c, o.humidity_pct, o.rain_mm,
                  o.wind_ms, o.wind_dir, o.pressure_hpa
           FROM stations s
           JOIN observations o ON o.station_id = s.station_id
           WHERE o.obs_time = (SELECT MAX(obs_time) FROM observations
                               WHERE station_id = s.station_id)
           ORDER BY s.station_id"""
    ).fetchall()

    features = []
    for (sid, name, county, town, alt, lat, lon, obs_time, weather,
         temp, humidity, rain, wind, wind_dir, pressure) in rows:
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]},
            "properties": {
                "id": sid, "name": name, "county": county, "town": town,
                "altitude": alt, "obsTime": obs_time, "weather": weather,
                "temp": temp, "humidity": humidity, "rain": rain,
                "wind": wind, "windDir": wind_dir, "pressure": pressure,
            },
        })

    geojson = {
        "type": "FeatureCollection",
        "metadata": {
            "source": "中央氣象署 O-A0001-001",
            "license": "政府資料開放授權條款－第1版",
            "generatedAt": datetime.now(TAIPEI_TZ).isoformat(timespec="seconds"),
            "stationCount": len(features),
        },
        "features": features,
    }

    # 先寫暫存檔再取代，避免寫到一半失敗時毀掉舊檔（§7.1）
    JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = JSON_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(geojson, ensure_ascii=False, separators=(",", ":")),
                   encoding="utf-8")
    tmp.replace(JSON_PATH)
    return len(features)


def main() -> int:
    stations = fetch()
    print(f"API 回傳測站數：{len(stations)}")

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.executescript(SCHEMA)
        n_station, n_obs = save(conn, stations)
        print(f"寫入測站：{n_station}，新增觀測：{n_obs}")
        n_export = export(conn)
        print(f"匯出 latest.json：{n_export} 站")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

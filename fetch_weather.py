"""
單元 4~10：CWA API → 解析 JSON → Pandas 整理 → 存進 SQLite → 用 SQL 驗證

執行方式：
    python fetch_weather.py
"""
import json
import os
import sqlite3

import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()   # 讀取同資料夾的 .env 檔（裡面放 CWA_API_KEY=你的授權碼）

# ── 設定 ────────────────────────────────────────────────────────────
# 授權碼放在 .env，而 .env 已列在 .gitignore，上傳 GitHub 時不會把金鑰一起傳上去
API_KEY = os.getenv("CWA_API_KEY", "").strip()
if not API_KEY:
    raise SystemExit("找不到授權碼：請在專案資料夾建立 .env 檔，內容為  CWA_API_KEY=你的授權碼")

BASE_URL = "https://opendata.cwa.gov.tw/fileapi/v1/opendataapi"
DB_PATH = "data.db"

REGIONS = ["北部地區", "中部地區", "南部地區", "東北部地區", "東部地區", "東南部地區"]

# 備案用：把縣市歸到六大區域（離島不列入）
COUNTY_TO_REGION = {
    "基隆市": "北部地區", "臺北市": "北部地區", "新北市": "北部地區",
    "桃園市": "北部地區", "新竹市": "北部地區", "新竹縣": "北部地區",
    "苗栗縣": "中部地區", "臺中市": "中部地區", "彰化縣": "中部地區",
    "南投縣": "中部地區", "雲林縣": "中部地區",
    "嘉義市": "南部地區", "嘉義縣": "南部地區", "臺南市": "南部地區",
    "高雄市": "南部地區", "屏東縣": "南部地區",
    "宜蘭縣": "東北部地區", "花蓮縣": "東部地區", "臺東縣": "東南部地區",
}


# ── 單元 4：用 Requests 取得 JSON ───────────────────────────────────
def fetch(dataset_id):
    url = f"{BASE_URL}/{dataset_id}"
    params = {"Authorization": API_KEY, "downloadType": "WEB", "format": "JSON"}
    try:
        resp = requests.get(url, params=params, timeout=60)
    except requests.exceptions.SSLError:
        # 氣象署憑證在較新的 Python 上有時驗證不過，這是已知狀況
        print("⚠️ SSL 驗證失敗，改用 verify=False 重試")
        requests.packages.urllib3.disable_warnings()
        resp = requests.get(url, params=params, timeout=60, verify=False)
    resp.raise_for_status()          # 401=授權碼錯、404=資料集不存在
    return resp.json()


# ── 單元 5、6：解析 JSON，取出 MinT / MaxT ─────────────────────────
def parse_agr(data):
    """F-A0010-001 一週農業氣象預報：本來就是「區域 × 每日」"""
    locations = (data["cwaopendata"]["resources"]["resource"]["data"]
                 ["agrWeatherForecasts"]["weatherForecasts"]["location"])
    rows = []
    for loc in locations:
        elements = loc["weatherElements"]
        for mn, mx in zip(elements["MinT"]["daily"], elements["MaxT"]["daily"]):
            rows.append({
                "regionName": loc["locationName"],
                "dataDate": mn["dataDate"],
                "mint": float(mn["temperature"]),
                "maxt": float(mx["temperature"]),
            })
    return pd.DataFrame(rows)


def parse_county(data):
    """F-C0032-005 一週縣市預報：縣市 × 每 12 小時 → 彙整成 區域 × 每日"""
    rows = []
    for loc in data["cwaopendata"]["dataset"]["location"]:
        region = COUNTY_TO_REGION.get(loc["locationName"])
        if region is None:
            continue
        for element in loc["weatherElement"]:
            if element["elementName"] not in ("MinT", "MaxT"):
                continue
            for t in element["time"]:
                rows.append({
                    "regionName": region,
                    "dataDate": t["startTime"][:10],
                    "element": element["elementName"],
                    "value": float(t["parameter"]["parameterName"]),
                })
    long_df = pd.DataFrame(rows)
    mint = long_df[long_df.element == "MinT"].groupby(["regionName", "dataDate"])["value"].min()
    maxt = long_df[long_df.element == "MaxT"].groupby(["regionName", "dataDate"])["value"].max()
    df = pd.concat([mint.rename("mint"), maxt.rename("maxt")], axis=1).dropna().reset_index()
    # 依北→南→東的順序排，而不是筆畫順序
    df["regionName"] = pd.Categorical(df["regionName"], REGIONS, ordered=True)
    df = df.sort_values(["regionName", "dataDate"]).reset_index(drop=True)
    df["regionName"] = df["regionName"].astype(str)
    return df


def get_forecast():
    """先試老師指定的資料集，拿不到就換備案"""
    try:
        data = fetch("F-A0010-001")
        print("✅ 取得 F-A0010-001（一週農業氣象預報）")
        return data, parse_agr(data)
    except requests.HTTPError as e:
        print(f"⚠️ F-A0010-001 取不到（HTTP {e.response.status_code}），改用 F-C0032-005")
        data = fetch("F-C0032-005")
        print("✅ 取得 F-C0032-005（一週縣市天氣預報）")
        return data, parse_county(data)


# ── 單元 8、9：建立 SQLite 資料庫 ──────────────────────────────────
def save_to_db(df):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS TemperatureForecasts (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            regionName TEXT NOT NULL,
            dataDate   TEXT NOT NULL,
            mint       REAL,
            maxt       REAL,
            UNIQUE (regionName, dataDate)      -- 單元 20：重複執行不會重複插入
        )
    """)
    conn.executemany(
        """INSERT INTO TemperatureForecasts (regionName, dataDate, mint, maxt)
           VALUES (?, ?, ?, ?)
           ON CONFLICT (regionName, dataDate)
           DO UPDATE SET mint = excluded.mint, maxt = excluded.maxt""",
        df[["regionName", "dataDate", "mint", "maxt"]].values.tolist(),
    )
    conn.commit()
    conn.close()


# ── 單元 10：用 SQL 驗證 ───────────────────────────────────────────
def verify():
    conn = sqlite3.connect(DB_PATH)
    print("\n【查詢 1】所有地區名稱")
    for (name,) in conn.execute("SELECT DISTINCT regionName FROM TemperatureForecasts"):
        print("  ", name)
    print("\n【查詢 2】中部地區的氣溫資料")
    print(pd.read_sql_query(
        "SELECT * FROM TemperatureForecasts WHERE regionName = '中部地區' ORDER BY dataDate",
        conn).to_string(index=False))
    conn.close()


if __name__ == "__main__":
    raw, df = get_forecast()

    # 單元 5：把原始 JSON 存檔，用 VS Code 打開慢慢看結構
    with open("raw.json", "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)
    print("\n── 原始 JSON 前 800 字（完整內容在 raw.json）──")
    print(json.dumps(raw, ensure_ascii=False, indent=2)[:800])

    # 單元 7：用 Pandas 預覽
    print("\n── 整理後的資料 ──")
    print(df.head(10).to_string(index=False))
    print(f"共 {len(df)} 筆，{df.regionName.nunique()} 個地區")

    save_to_db(df)
    print(f"\n✅ 已寫入 {DB_PATH}")
    verify()

# -*- coding: utf-8 -*-
"""
키움 [0784] 투자자별 매매동향 수집 → supply_data.json
- 현물(KOSPI): ka10051(업종별투자자순매수) 의 '종합(KOSPI)' 행, 금액(억원). 키움 [0784] 종합과 100% 일치.
- 파생(선물/옵션콜/옵션풋/주식선물): 준비중(null).
- 날짜별로 호출 → 누적 저장 (기존 supply_data.json 거래일 + 오늘 추가).
환경변수 KIWOOM_APP_KEY / KIWOOM_APP_SECRET 필요.
사용: py -3.13 fetch_supply.py            (전체 거래일 갱신/백필)
      py -3.13 fetch_supply.py --last 5   (최근 5거래일만 — 테스트용)
"""
import os, sys, json, time
import datetime as dt
import requests
from pathlib import Path

BASE = "https://api.kiwoom.com"
OUT = Path(__file__).parent / "supply_data.json"

def token():
    r = requests.post(f"{BASE}/oauth2/token", json={
        "grant_type": "client_credentials",
        "appkey": os.environ["KIWOOM_APP_KEY"],
        "secretkey": os.environ["KIWOOM_APP_SECRET"],
    }, headers={"Content-Type": "application/json;charset=UTF-8"}, timeout=30)
    r.raise_for_status()
    t = r.json().get("token") or r.json().get("access_token")
    if not t: raise SystemExit(f"토큰 실패: {r.text}")
    return t

def pnum(s):
    s = str(s).strip().replace("+", "").replace("--", "-")
    try: return int(float(s))
    except: return 0

def fetch_kospi(tok, yyyymmdd):
    """ka10051 종합(KOSPI) 금액 순매수 → {ind,frn,inst} (억원) / 거래일 아니면 None"""
    r = requests.post(f"{BASE}/api/dostk/sect", headers={
        "content-type": "application/json;charset=UTF-8",
        "authorization": f"Bearer {tok}", "api-id": "ka10051",
        "cont-yn": "N", "next-key": "",
    }, json={"mrkt_tp": "0", "amt_qty_tp": "0", "base_dt": yyyymmdd, "dt": yyyymmdd, "stex_tp": "3"}, timeout=20)
    rows = r.json().get("inds_netprps", [])
    for row in rows:
        if row.get("inds_nm") == "종합(KOSPI)":
            if pnum(row.get("trde_qty")) == 0:   # 휴장일 방어
                return None
            return {
                "ind":  pnum(row["ind_netprps"]),
                "frn":  pnum(row["frgnr_netprps"]),
                "inst": pnum(row["orgn_netprps"]),
            }
    return None

def existing_map():
    if OUT.exists():
        j = json.loads(OUT.read_text(encoding="utf-8"))
        return {d["date"]: d["v"] for d in j.get("days", [])}
    return {}

def candidate_dates(lookback_days=230):
    today = dt.date.today()
    out, d = [], today
    for _ in range(lookback_days):
        if d.weekday() < 5:
            out.append(d.strftime("%Y-%m-%d"))
        d -= dt.timedelta(days=1)
    return sorted(out)

def main():
    last = None
    if "--last" in sys.argv:
        last = int(sys.argv[sys.argv.index("--last") + 1])
    tok = token()
    data = existing_map()                 # 기존 보존 (date -> v)
    cands = candidate_dates()
    if last: cands = cands[-last:]
    today = dt.date.today().strftime("%Y-%m-%d")
    to_fetch = [d for d in cands if d not in data]
    if today not in to_fetch and dt.date.today().weekday() < 5:
        to_fetch.append(today)            # 오늘은 항상 갱신
    print(f"기존 {len(data)}일 보존, 신규/갱신 수집 {len(to_fetch)}일")

    new = 0
    for d in to_fetch:
        spot = fetch_kospi(tok, d.replace("-", ""))
        if spot is None:
            continue                      # 휴장일
        data[d] = {"spot": spot, "fut": None, "callopt": None, "putopt": None, "sfut": None}
        new += 1
        print(f"  {d}  개인 {spot['ind']:+,}  외인 {spot['frn']:+,}  기관 {spot['inst']:+,}")
        time.sleep(0.25)

    # 휴장일 방어: 직전 거래일과 현물 값이 완전 동일하면 stale(휴장)로 보고 제외
    days, prev_spot = [], None
    for d in sorted(data):
        v = data[d]
        if prev_spot is not None and v.get("spot") == prev_spot:
            print(f"  [중복제거] {d} (직전일과 동일 → 휴장 추정)")
            continue
        days.append({"date": d, "v": v})
        prev_spot = v.get("spot")
    payload = {
        "updated": dt.datetime.now().isoformat(timespec="seconds"),
        "unit": "억원",
        "source": "키움 [0784] ka10051 (현물=실데이터 / 파생=준비중)",
        "days": days,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"저장: {OUT} (총 {len(days)}일, 신규 {new}일)")

if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""
코스피 수급 수집 → supply_data.json (현물=키움 / 파생=KRX)
- 현물(KOSPI): 키움 ka10051 '종합(KOSPI)' 금액. 키움 [0784]와 100% 일치.
- 파생(선물/옵션콜/옵션풋/주식선물): KRX MDCSTAT13101 (투자자별 거래실적), 기관합계 기준.
- 거래일 = 현물(키움)로 판정(휴장일 stale 제거). 그 거래일에 파생도 채움.
- 증분: 기존 json 보존, 누락분만 수집.
환경변수: KIWOOM_APP_KEY/SECRET (현물), KRX_ID/KRX_PW (파생).
사용: py -3.13 fetch_supply.py [--last N]
"""
import os, sys, json, time
import datetime as dt
import requests
from pathlib import Path
from pykrx.website.comm.auth import get_auth_session

OUT = Path(__file__).parent / "supply_data.json"
KIWOOM = "https://api.kiwoom.com"
KRX_URL = "http://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"

# 파생 상품: key -> (isuCd, isuOpt)
DRV = {
    "fut":     ("KR___FUK2I", ""),
    "callopt": ("KR___OPK2I", "C"),
    "putopt":  ("KR___OPK2I", "P"),
    "sfut":    ("KR___FUEQU", ""),
}

def pnum(s):
    s = str(s).strip().replace(",", "").replace("+", "").replace("--", "-")
    try: return int(float(s))
    except: return 0

# ── 현물: 키움 ──────────────────────────────────────────────
def kiwoom_token():
    r = requests.post(f"{KIWOOM}/oauth2/token", json={
        "grant_type": "client_credentials",
        "appkey": os.environ["KIWOOM_APP_KEY"], "secretkey": os.environ["KIWOOM_APP_SECRET"],
    }, headers={"Content-Type": "application/json;charset=UTF-8"}, timeout=30)
    t = r.json().get("token")
    if not t: raise SystemExit(f"키움 토큰 실패: {r.text}")
    return t

def fetch_spot(tok, dd):
    r = requests.post(f"{KIWOOM}/api/dostk/sect", headers={
        "content-type": "application/json;charset=UTF-8", "authorization": f"Bearer {tok}",
        "api-id": "ka10051", "cont-yn": "N", "next-key": "",
    }, json={"mrkt_tp": "0", "amt_qty_tp": "0", "base_dt": dd, "dt": dd, "stex_tp": "3"}, timeout=20)
    for row in r.json().get("inds_netprps", []):
        if row.get("inds_nm") == "종합(KOSPI)":
            if pnum(row.get("trde_qty")) == 0: return None
            return {"ind": pnum(row["ind_netprps"]), "frn": pnum(row["frgnr_netprps"]), "inst": pnum(row["orgn_netprps"])}
    return None

# ── 파생: KRX ───────────────────────────────────────────────
def fetch_drv(ks, isuCd, isuOpt, dd):
    body = {"bld": "dbms/MDC/STAT/standard/MDCSTAT13101", "locale": "ko_KR", "prodId": "",
        "strtDd": dd, "endDd": dd, "inqTpCd": "1", "prtType": "AMT", "prtCheck": "SUN",
        "isuCd": isuCd, "isuOpt": isuOpt, "aggBasTpCd": "",
        "strtDdBox1": dd, "endDdBox1": dd, "share": "1", "money": "3", "csvxls_isNo": "false"}
    try:
        rows = ks.post(KRX_URL, data=body, timeout=20).json().get("output", [])
    except Exception:
        return None
    d = {r["INVST_TP_NM"]: pnum(r.get("NETBID_TRDVAL")) for r in rows}
    if "합계" not in d: return None
    eok = lambda v: round(v / 1e8)
    return {"ind": eok(d.get("개인", 0)), "frn": eok(d.get("외국인", 0)), "inst": eok(d.get("기관합계", 0))}

# ── 거래일/저장 ─────────────────────────────────────────────
def existing_map():
    if OUT.exists():
        j = json.loads(OUT.read_text(encoding="utf-8"))
        return {d["date"]: d["v"] for d in j.get("days", [])}
    return {}

def candidate_dates(lookback=230):
    today = dt.date.today(); out, d = [], today
    for _ in range(lookback):
        if d.weekday() < 5: out.append(d.strftime("%Y-%m-%d"))
        d -= dt.timedelta(days=1)
    return sorted(out)

def main():
    last = int(sys.argv[sys.argv.index("--last")+1]) if "--last" in sys.argv else None
    ktok = kiwoom_token()
    ks = get_auth_session()          # KRX 로그인
    data = existing_map()
    cands = candidate_dates()
    if last: cands = cands[-last:]
    today = dt.date.today().strftime("%Y-%m-%d")

    # 1) 현물 신규(키움)
    new_spot = [d for d in cands if d not in data]
    if today not in new_spot and dt.date.today().weekday() < 5: new_spot.append(today)
    print(f"현물 수집 {len(new_spot)}일")
    for d in new_spot:
        sp = fetch_spot(ktok, d.replace("-", ""))
        if sp is None: continue
        if d in data: data[d]["spot"] = sp
        else: data[d] = {"spot": sp, "fut": None, "callopt": None, "putopt": None, "sfut": None}
        time.sleep(0.2)

    # 2) 휴장일 stale 제거 → 거래일 확정
    ordered, prev = [], None
    for d in sorted(data):
        sp = data[d].get("spot")
        if sp and sp == prev: continue
        ordered.append(d); prev = sp

    # 3) 파생 채우기(KRX) — 아직 안 채운 거래일만
    todo = [d for d in ordered if data[d].get("fut") is None]
    print(f"파생 수집 {len(todo)}일 (×4상품)")
    for i, d in enumerate(todo):
        dd = d.replace("-", "")
        for key, (isu, opt) in DRV.items():
            data[d][key] = fetch_drv(ks, isu, opt, dd)
            time.sleep(0.12)
        if (i+1) % 20 == 0: print(f"  ...{i+1}/{len(todo)}")

    days = [{"date": d, "v": data[d]} for d in ordered]
    payload = {"updated": dt.datetime.now().isoformat(timespec="seconds"), "unit": "억원",
               "source": "현물=키움[0784] ka10051 / 파생=KRX MDCSTAT13101", "days": days}
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"저장: {OUT} (총 {len(days)}일)")
    if days: print("최신:", json.dumps(days[-1], ensure_ascii=False))

if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""
코스피 수급 수집 → supply_data.json (현물=키움 / 파생=KRX)
- 현물(KOSPI): 키움 ka10051 '종합(KOSPI)' 금액. 키움 [0784]와 100% 일치.
- 파생(선물/옵션콜/옵션풋/주식선물): KRX MDCSTAT13101, 기관합계 기준. (pykrx 없이 순수 requests 로그인)
- 거래일 = 현물(키움)로 판정(휴장 stale 제거). 증분: 기존 json 보존, 누락분만.
- push: 로컬은 git, 서버(git 없음)는 GitHub Contents API(GITHUB_TOKEN).
키: 로컬 .env(서버) 또는 매매일지 .env(키움)+레지스트리(KRX). PC=git, 서버=GITHUB_TOKEN.
사용: python3 fetch_supply.py [--push] [--last N] [--redrv]
"""
import os, sys, json, time, base64, shutil, subprocess, re
import datetime as dt
import requests
from pathlib import Path
try:
    import winreg
except ImportError:
    winreg = None

OUT = Path(__file__).parent / "supply_data.json"
LOG = Path(__file__).parent / "supply_run.log"
JOURNAL_ENV = Path(r"C:\Users\user\OneDrive\바탕 화면\Trading\daily-trade-journal\.env")
KIWOOM = "https://api.kiwoom.com"
KRX_GET = "http://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
GITHUB_REPO = "hong2qqu-oss/-"

DRV = {  # key -> (isuCd, isuOpt)
    "fut": ("KR___FUK2I", ""), "callopt": ("KR___OPK2I", "C"),
    "putopt": ("KR___OPK2I", "P"), "sfut": ("KR___FUEQU", ""),
}
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

def log(m):
    line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}  {m}"
    print(line)
    try: LOG.open("a", encoding="utf-8").write(line + "\n")
    except Exception: pass

def pnum(s):
    s = str(s).strip().replace(",", "").replace("+", "").replace("--", "-")
    try: return int(float(s))
    except: return 0

# ── 키 로드 ─────────────────────────────────────────────────
def _reg_env(name):
    if not winreg: return None
    try:
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment")
        v, _ = winreg.QueryValueEx(k, name); winreg.CloseKey(k); return v
    except Exception:
        return None

def _load_envfile(p, keys):
    if not p.exists(): return
    for ln in p.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#") and "=" in ln:
            k, v = ln.split("=", 1); k = k.strip()
            if (keys is None or k in keys) and not os.environ.get(k):
                os.environ[k] = re.sub(r'\s+#.*$', '', v).strip()

def load_keys():
    _load_envfile(OUT.parent / ".env", None)           # 서버: 로컬 .env(전체)
    _load_envfile(JOURNAL_ENV, {"KIWOOM_APP_KEY", "KIWOOM_APP_SECRET"})  # PC: 키움
    for k in ("KRX_ID", "KRX_PW"):                      # PC: KRX는 레지스트리
        if not os.environ.get(k):
            v = _reg_env(k)
            if v: os.environ[k] = v

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

# ── KRX 로그인 (순수 requests) ──────────────────────────────
def krx_login():
    s = requests.Session()
    s.get("https://data.krx.co.kr/contents/MDC/COMS/client/MDCCOMS001.cmd", headers={"User-Agent": UA}, timeout=15)
    s.get("https://data.krx.co.kr/contents/MDC/COMS/client/view/login.jsp?site=mdc",
          headers={"User-Agent": UA, "Referer": "https://data.krx.co.kr/contents/MDC/COMS/client/MDCCOMS001.cmd"}, timeout=15)
    payload = {"mbrNm": "", "telNo": "", "di": "", "certType": "",
               "mbrId": os.environ["KRX_ID"], "pw": os.environ["KRX_PW"]}
    hdr = {"User-Agent": UA, "Referer": "https://data.krx.co.kr/contents/MDC/COMS/client/MDCCOMS001.cmd"}
    url = "https://data.krx.co.kr/contents/MDC/COMS/client/MDCCOMS001D1.cmd"
    d = s.post(url, data=payload, headers=hdr, timeout=15).json()
    if d.get("_error_code") == "CD011":   # 중복로그인
        payload["skipDup"] = "Y"
        d = s.post(url, data=payload, headers=hdr, timeout=15).json()
    if d.get("_error_code") != "CD001":
        raise SystemExit(f"KRX 로그인 실패: {d.get('_error_code')} {d.get('_error_message')}")
    log("KRX 로그인 완료")
    return s

KRX_HDR = {"User-Agent": UA, "X-Requested-With": "XMLHttpRequest",
           "Referer": "https://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd"}

def fetch_drv(s, isuCd, isuOpt, dd):
    body = {"bld": "dbms/MDC/STAT/standard/MDCSTAT13101", "locale": "ko_KR", "prodId": "",
        "strtDd": dd, "endDd": dd, "inqTpCd": "1", "prtType": "AMT", "prtCheck": "SUN",
        "isuCd": isuCd, "isuOpt": isuOpt, "aggBasTpCd": "",
        "strtDdBox1": dd, "endDdBox1": dd, "share": "1", "money": "3", "csvxls_isNo": "false"}
    try:
        rows = s.post(KRX_GET, data=body, headers=KRX_HDR, timeout=20).json().get("output", [])
    except Exception:
        return None
    d = {r["INVST_TP_NM"]: pnum(r.get("NETBID_TRDVAL")) for r in rows}
    if "합계" not in d: return None
    eok = lambda v: round(v / 1e8, 1)   # 억원 소수1자리
    return {"ind": eok(d.get("개인", 0)), "frn": eok(d.get("외국인", 0)), "inst": eok(d.get("기관합계", 0))}

# ── push (git or GitHub API) ────────────────────────────────
def github_api_push():
    tok = os.environ.get("GITHUB_TOKEN")
    if not tok: log("GITHUB_TOKEN 없음 — push 생략"); return
    api = f"https://api.github.com/repos/{GITHUB_REPO}/contents/supply_data.json"
    h = {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"}
    sha = None
    g = requests.get(api, headers=h, params={"ref": "main"}, timeout=20)
    if g.status_code == 200: sha = g.json().get("sha")
    body = {"message": f"수급 자동갱신 {dt.date.today():%Y-%m-%d}",
            "content": base64.b64encode(OUT.read_bytes()).decode(), "branch": "main"}
    if sha: body["sha"] = sha
    r = requests.put(api, headers=h, json=body, timeout=30)
    log("GitHub API push " + ("완료" if r.status_code in (200, 201) else f"실패 {r.status_code} {r.text[:120]}"))

def push():
    if shutil.which("git") and (OUT.parent / ".git").exists():
        p = OUT.parent
        subprocess.run(["git", "add", "supply_data.json"], cwd=p)
        if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=p).returncode != 0:
            subprocess.run(["git", "commit", "-m", f"수급 자동갱신 {dt.date.today():%Y-%m-%d}"], cwd=p)
            r = subprocess.run(["git", "push", "origin", "main"], cwd=p)
            log("git push " + ("완료" if r.returncode == 0 else "실패"))
        else:
            log("변경 없음 — push 생략")
    else:
        github_api_push()

# ── 거래일/메인 ─────────────────────────────────────────────
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
    log("===== 시작 =====")
    last = int(sys.argv[sys.argv.index("--last")+1]) if "--last" in sys.argv else None
    load_keys()
    ktok = kiwoom_token()
    ks = krx_login()
    data = existing_map()
    cands = candidate_dates()
    if last: cands = cands[-last:]
    today = dt.date.today().strftime("%Y-%m-%d")

    new_spot = [d for d in cands if d not in data]
    if today not in new_spot and dt.date.today().weekday() < 5: new_spot.append(today)
    log(f"현물 수집 {len(new_spot)}일")
    for d in new_spot:
        sp = fetch_spot(ktok, d.replace("-", ""))
        if sp is None: continue
        if d in data: data[d]["spot"] = sp
        else: data[d] = {"spot": sp, "fut": None, "callopt": None, "putopt": None, "sfut": None}
        time.sleep(0.2)

    ordered, prev = [], None
    for d in sorted(data):
        sp = data[d].get("spot")
        if sp and sp == prev: continue
        ordered.append(d); prev = sp

    redrv = "--redrv" in sys.argv
    todo = [d for d in ordered if redrv or data[d].get("fut") is None or d == today]
    log(f"파생 수집 {len(todo)}일")
    for i, d in enumerate(todo):
        dd = d.replace("-", "")
        for key, (isu, opt) in DRV.items():
            data[d][key] = fetch_drv(ks, isu, opt, dd)
            time.sleep(0.12)
        if (i+1) % 30 == 0: log(f"  ...{i+1}/{len(todo)}")

    days = [{"date": d, "v": data[d]} for d in ordered]
    payload = {"updated": dt.datetime.now().isoformat(timespec="seconds"), "unit": "억원",
               "source": "현물=키움[0784] ka10051 / 파생=KRX MDCSTAT13101", "days": days}
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log(f"저장 총 {len(days)}일")
    if "--push" in sys.argv: push()
    log("===== 끝 =====")

if __name__ == "__main__":
    main()

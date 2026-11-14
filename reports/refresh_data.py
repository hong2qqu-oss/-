# -*- coding: utf-8 -*-
"""보고서 데이터 갱신·신규 발표 감지 — 한 곳에서 (2026-11-14)

왜: FRED 는 노동부(BLS) 자료를 공식 발표보다 45분~2시간 넘게 늦게 올린다(2026-11: CPI +46분, 고용 +2시간 13분,
PPI +1시간 50분). 에이전트가 22:35·23:40 에 FRED 만 보다가 '신규 없음'으로 끝나고 PC 가 꺼지면 보고서가 다음 날로
밀렸다(11/6 고용, 11/13 PPI). → 고용·CPI·PPI 는 BLS 공개 API(키 불필요)에서 발표 시각에 바로 받는다.
그 밖의 지표는 지금처럼 FRED(캐시 우회). 어느 쪽이든 **뒤로 가는 덮어쓰기는 안 한다**(늦은 원천이 새 달을 지우지 못하게).

  python reports/refresh_data.py            # 감지용 12종 갱신(+ BLS 지표는 세부까지) → 태그별 상태, 새 발표는 NEW 줄
  python reports/refresh_data.py --tag PPI  # 그 지표의 교본 세부 시리즈 전부 갱신 (보고서 쓰기 직전에)
  python reports/refresh_data.py --detect   # 할일봇 감시용: 위와 같되 NEW 줄만 출력 (--tags NFP,CPI 로 일부만)
  python reports/refresh_data.py --verify   # BLS ↔ FRED 대응표 검산 (대응을 고쳤을 때)

BLS 공개 API 한도(미등록): 하루 25회·한 번에 25개·10년. 발표 일정표(data/release_calendar.json)에 그 지표 발표가
지났고 아직 새 달이 안 들어왔을 때만 부르고, 하루 20회에서 멈춘다(.claude/bls_quota.json).
"""
import sys, os, re, json, time, argparse, urllib.request
from datetime import datetime, timedelta, timezone, date

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
LOCAL = os.path.join(HERE, "..", ".claude")          # gitignore — 이 PC 전용 기록
QUOTA = os.path.join(LOCAL, "bls_quota.json")
LAGLOG = os.path.join(LOCAL, "release_lag.log")
BLS_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
BLS_DAILY_MAX = 20

# 감지용 시리즈 (마지막 관측이 state.json 보다 새로우면 신규 발표)
DETECT = {"NFP": "PAYEMS", "CPI": "CPIAUCSL", "PPI": "PPIFIS", "RETAIL": "RSAFS", "PCE": "PCEPI", "CLAIMS": "ICSA",
          "ADP": "ADPMNUSNERSA", "DURABLE": "DGORDER", "HOMESALES": "HSN1F", "CASESHILLER": "CSUSHPINSA",
          "CAPUTIL": "TCU", "GDP": "A191RL1Q225SBEA"}
# 교본(CHECKLIST.md) 제목 첫 단어 → 태그, 태그 → calc_series 묶음
SECTION = {"NFP": "NFP", "CPI": "CPI", "PPI": "PPI", "소매판매": "RETAIL", "PCE": "PCE", "ADP": "ADP", "CLAIMS": "CLAIMS",
           "DURABLE": "DURABLE", "HOMESALES": "HOMESALES", "CASESHILLER": "CASESHILLER", "CAPUTIL": "CAPUTIL", "GDP": "GDP"}
SETS_OF = {"CPI": ["cpi"], "PPI": ["ppi"], "DURABLE": ["durable"], "HOMESALES": ["homesales"], "CASESHILLER": ["caseshiller"],
           "CAPUTIL": ["caputil", "indpro"], "GDP": ["gdp_growth", "gdp_contrib", "gdp_price"]}
EXTRA = {"NFP": ["UNRATE"], "CLAIMS": ["ICSA", "CCSA"], "CAPUTIL": ["TCU"]}
NOT_FRED = re.compile(r"^(ISMM_|CB_|RICH_|CASS_|yahoo_|ecos_)|^LA001282$")

# FRED 이름 → BLS 시리즈 (2026-11-14 --verify 로 최근 3년 값이 같음을 확인). CES·LNS·CUSR·WPS 로 시작하는 ID 는 그대로 BLS ID.
BLS_MAP = {
    # 고용보고서 — 사업체 조사
    "PAYEMS": "CES0000000001", "USPRIV": "CES0500000001", "USGOVT": "CES9000000001", "USGOOD": "CES0600000001",
    "SRVPRD": "CES0700000001", "MANEMP": "CES3000000001", "USCONS": "CES2000000001", "USMINE": "CES1000000001",
    "USEHS": "CES6500000001", "USLAH": "CES7000000001", "USTRADE": "CES4200000001", "USWTRADE": "CES4142000001",
    "USPBS": "CES6000000001", "TEMPHELPS": "CES6056132001", "USINFO": "CES5000000001", "USFIRE": "CES5500000001",
    "USTPU": "CES4000000001", "USSERV": "CES8000000001", "AWHAETP": "CES0500000002",
    "AWHI": "CES0500000034",      # 생산·비감독직 총노동시간 지수 (전체 근로자 판 …016 이 아님 — 값이 다름)
    # 고용보고서 — 가계 조사
    "UNRATE": "LNS14000000", "CE16OV": "LNS12000000", "UNEMPLOY": "LNS13000000", "CIVPART": "LNS11300000",
    "NILFWJN": "LNS15026639", "UEMP27OV": "LNS13008636", "U6RATE": "LNS13327709",
    # CPI
    "CPIAUCSL": "CUSR0000SA0", "CPILFESL": "CUSR0000SA0L1E", "CPIUFDSL": "CUSR0000SAF1", "CPIENGSL": "CUSR0000SA0E",
    "CPIAPPSL": "CUSR0000SAA",
    # PPI
    "PPIFIS": "WPSFD4",
}
BLS_TAGS = ("NFP", "CPI", "PPI")
BLS_PREFIX = re.compile(r"^(CES|LNS|CUSR|WPS)\w+$")


def bls_id(fid):
    return BLS_MAP.get(fid) or (fid if BLS_PREFIX.match(fid) else None)


# ── 파일 ──
def read_csv(fid):
    p = os.path.join(DATA, fid + ".csv")
    rows = {}
    if os.path.exists(p):
        for l in open(p, encoding="utf-8"):
            c = l.strip().split(",")
            if len(c) >= 2 and re.match(r"\d{4}-\d{2}-\d{2}$", c[0]) and c[1] not in ("", "."):
                rows[c[0]] = c[1]
    return rows


def write_csv(fid, rows):
    """바뀌었을 때만 쓴다. 돌려주는 값 = 썼는지"""
    p = os.path.join(DATA, fid + ".csv")
    txt = "".join("%s,%s\n" % (k, rows[k]) for k in sorted(rows))
    old = open(p, encoding="utf-8").read().replace("\r\n", "\n") if os.path.exists(p) else None
    if old == txt:
        return False
    tmp = p + ".tmp"
    open(tmp, "w", encoding="utf-8", newline="\n").write(txt)
    os.replace(tmp, p)
    return True


def last_of(fid):
    r = read_csv(fid)
    return max(r) if r else ""


def load_json(path, default):
    try:
        return json.load(open(path, encoding="utf-8-sig"))
    except Exception:
        return default


# ── 원천: FRED ──
def fred_rows(fid):
    u = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=%s&_=%d" % (fid, int(time.time()))   # 약 7분 캐시 우회
    for i in range(3):
        try:
            t = urllib.request.urlopen(u, timeout=40).read().decode("utf-8", "replace")
            break
        except Exception:
            if i == 2:
                raise
            time.sleep(2 + 3 * i)
    rows = {}
    for l in t.strip().split("\n")[1:]:
        c = l.strip().split(",")
        if len(c) >= 2 and re.match(r"\d{4}-\d{2}-\d{2}$", c[0]) and c[1] not in ("", "."):
            rows[c[0]] = c[1]
    return rows


def update_from_fred(fid):
    """FRED 에서 받아 쓰되, 마지막 관측이 지금 파일보다 옛것이면 그대로 둔다. → (상태, 마지막 관측)"""
    old = read_csv(fid)
    try:
        new = fred_rows(fid)
    except Exception as e:
        return "fail:%s" % type(e).__name__, (max(old) if old else "")
    if not new:
        return "empty", (max(old) if old else "")
    if old and max(new) < max(old):
        return "kept(FRED가 더 옛것 %s)" % max(new), max(old)
    return ("updated" if write_csv(fid, new) else "same"), max(new)


# ── 원천: BLS ──
def quota_ok(n=1):
    q = load_json(QUOTA, {})
    d = date.today().isoformat()
    used = q.get(d, 0)
    if used + n > BLS_DAILY_MAX:
        return False
    os.makedirs(LOCAL, exist_ok=True)
    json.dump({d: used + n}, open(QUOTA, "w", encoding="utf-8"))
    return True


def bls_fetch(fids, years=3):
    """FRED 이름 목록 → {FRED 이름: {날짜: 값 글자}} (BLS 에 대응이 있는 것만). 한 번에 25개씩."""
    pairs = [(f, bls_id(f)) for f in fids if bls_id(f)]
    out = {}
    y = date.today().year
    for i in range(0, len(pairs), 25):
        chunk = pairs[i:i + 25]
        if not quota_ok():
            raise RuntimeError("BLS 하루 한도(%d회) 도달" % BLS_DAILY_MAX)
        body = json.dumps({"seriesid": [b for _, b in chunk], "startyear": str(y - years + 1), "endyear": str(y)}).encode()
        req = urllib.request.Request(BLS_URL, data=body, headers={"Content-Type": "application/json"})
        j = json.loads(urllib.request.urlopen(req, timeout=60).read())
        if j.get("status") != "REQUEST_SUCCEEDED":
            raise RuntimeError("BLS: %s %s" % (j.get("status"), "; ".join(j.get("message") or [])[:200]))
        back = {b: f for f, b in chunk}
        for s in j["Results"]["series"]:
            rows = {}
            for d in s.get("data", []):
                m = re.match(r"M(\d\d)$", d.get("period", ""))
                v = (d.get("value") or "").replace(",", "").strip()
                if not m or m.group(1) == "13" or not re.match(r"-?\d+(\.\d+)?$", v):
                    continue                      # 연평균(M13)·결측('-')은 뺌
                rows["%s-%s-01" % (d["year"], m.group(1))] = v
            if rows:
                out[back[s["seriesID"]]] = rows
        time.sleep(0.5)
    return out


def merge_bls(fid, rows):
    """BLS 값으로 그 달들을 바꾸고(수정치 반영) 새 달을 붙인다. BLS 가 지금 파일보다 옛것이면 손대지 않음."""
    old = read_csv(fid)
    if old and max(rows) < max(old):
        return "kept", max(old)
    merged = dict(old)
    merged.update(rows)
    return ("updated" if write_csv(fid, merged) else "same"), max(merged)


# ── 태그별 시리즈 목록 (교본 + calc_series 묶음) ──
def tag_series(tag):
    ids = [DETECT[tag]] if tag in DETECT else []
    have = {f[:-4] for f in os.listdir(DATA) if f.endswith(".csv")}
    try:
        L = open(os.path.join(HERE, "CHECKLIST.md"), encoding="utf-8").read().split("\n")
        heads = [(i, l) for i, l in enumerate(L) if l.startswith("## ")]
        for n, (i, h) in enumerate(heads):
            if SECTION.get(h[3:].split(" ")[0]) != tag:
                continue
            j = heads[n + 1][0] if n + 1 < len(heads) else len(L)
            for t in re.findall(r"\b[A-Z][A-Z0-9_]{3,}\b", "\n".join(L[i:j])):
                if t in have and t not in ids:
                    ids.append(t)
    except Exception:
        pass
    try:
        src = open(os.path.join(HERE, "calc_series.py"), encoding="utf-8").read()
        for s in SETS_OF.get(tag, []):
            m = re.search(r'"%s":\s*\[(.*?)\]' % s, src, re.S)
            for t in re.findall(r'"([A-Za-z0-9_]+)"', m.group(1)) if m else []:
                if t not in ids:
                    ids.append(t)
    except Exception:
        pass
    for t in EXTRA.get(tag, []):
        if t not in ids:
            ids.append(t)
    return [t for t in ids if not NOT_FRED.search(t)]


# ── 발표 일정으로 'BLS 를 부를 때인가' 판단 ──
def bls_due(tag, now=None):
    """그 지표 발표 시각이 지났고(30시간 안) 아직 그 달 자료가 파일에 없으면 True. 일정표가 없으면 False."""
    now = now or datetime.now(timezone.utc)
    cal = load_json(os.path.join(DATA, "release_calendar.json"), {}).get("events", [])
    have = last_of(DETECT[tag])[:7]
    for e in cal:
        if e.get("tag") != tag:
            continue
        t = datetime.strptime(e["at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        if not (t - timedelta(minutes=2) <= now <= t + timedelta(hours=30)):
            continue
        y, m = t.year, t.month - 1                 # 월간 지표: 발표 달의 전달이 대상
        if m == 0:
            y, m = y - 1, 12
        if have < "%04d-%02d" % (y, m):
            return True
    return False


def log_lag(tag, period, source):
    try:
        cal = load_json(os.path.join(DATA, "release_calendar.json"), {}).get("events", [])
        now = datetime.now(timezone.utc)
        best = None
        for e in cal:
            if e.get("tag") == tag:
                t = datetime.strptime(e["at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                if timedelta(0) <= now - t <= timedelta(hours=40) and (best is None or t > best):
                    best = t
        lag = "발표 %d분 뒤" % ((now - best).total_seconds() // 60) if best else "일정표에 없음"
        os.makedirs(LOCAL, exist_ok=True)
        open(LAGLOG, "a", encoding="utf-8").write("%s %s %s 새 자료 확인 (%s) — %s\n" % (
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"), tag, period, source, lag))
    except Exception:
        pass


def refresh(detect_only=False, only=None):
    """only = 볼 태그 목록(없으면 전부). 돌려주는 값 = [(태그, 마지막 관측)] 새 발표"""
    state = load_json(os.path.join(HERE, "state.json"), {})
    DET = {t: s for t, s in DETECT.items() if not only or t in only}
    before = {t: last_of(s) for t, s in DET.items()}
    src, notes = {}, []
    # 1) BLS — 발표가 지났는데 아직 안 들어온 지표만
    due = [t for t in BLS_TAGS if t in DET and bls_due(t)]
    if due:
        try:
            got = bls_fetch([DETECT[t] for t in due], years=2)
            for t in due:
                rows = got.get(DETECT[t])
                if rows and max(rows) > before[t]:
                    det = bls_fetch(tag_series(t))          # 새 달이 나왔다 → 세부 시리즈까지 한 번에
                    n = sum(1 for f, r in det.items() if merge_bls(f, r)[0] == "updated")
                    src[t] = "BLS"
                    notes.append("%s: BLS 에서 %s 수신 — 세부 %d종 갱신" % (t, max(rows)[:7], n))
                else:
                    notes.append("%s: BLS 아직 %s (발표 직후면 잠시 뒤 다시)" % (t, (max(rows)[:7] if rows else "응답 없음")))
        except Exception as e:
            notes.append("BLS 실패 — FRED 로 계속: %s" % e)
    # 2) FRED — 감지용 전부 (뒤로 가는 덮어쓰기 없음)
    for t, s in DET.items():
        st, last = update_from_fred(s)
        if st.startswith(("fail", "empty", "kept")):
            notes.append("%s(%s): FRED %s" % (t, s, st))
        if last > before[t] and t not in src:
            src[t] = "FRED"
    # 3) 판정
    new = []
    for t, s in DET.items():
        last = last_of(s)
        have = str(state.get(t, ""))
        if t == "CLAIMS":
            is_new = last > have
        elif t == "GDP":
            v = read_csv(s).get(last, "")
            est = str(state.get("GDP_EST", ""))
            is_new = last[:7] > have or (last[:7] == have and est not in ("", v) and abs(float(v) - float(est)) > 1e-9)
        else:
            is_new = last[:7] > have
        if is_new:
            new.append((t, last))
            if last > before[t]:
                log_lag(t, last[:7] if t != "CLAIMS" else last, src.get(t, "?"))
        if not detect_only:
            print("%-12s %-18s 파일 %s · 보고서 %s%s" % (t, s, last, have or "-", "  ← 새 발표" if is_new else ""))
    if not detect_only:
        for n in notes:
            print("· " + n)
    for t, last in new:
        print("NEW %s %s %s" % (t, last if t == "CLAIMS" else last[:7], src.get(t, "파일")))
    if not new and not detect_only:
        print("NO_NEW_DATA")
    return new


def refresh_tag(tag):
    ids = tag_series(tag)
    done = {}
    if tag in BLS_TAGS:
        try:
            for f, r in bls_fetch(ids).items():
                done[f] = "BLS " + merge_bls(f, r)[0]
        except Exception as e:
            print("· BLS 실패 — FRED 로 받음(뒤로 가는 덮어쓰기는 안 함): %s" % e)
    for f in ids:
        if f in done:
            continue
        st, last = update_from_fred(f)
        done[f] = "FRED " + st
        time.sleep(0.25)
    lasts = {}
    for f in ids:
        lasts.setdefault(last_of(f), []).append(f)
    print("%s 세부 %d종 갱신 — 마지막 관측별: %s" % (tag, len(ids), " / ".join("%s %d종" % (k, len(v)) for k, v in sorted(lasts.items(), reverse=True))))
    top = max(lasts) if lasts else ""
    for k, v in sorted(lasts.items()):
        if k != top and len(v) <= 12:
            print("  %s 까지만: %s" % (k, " ".join(v)))
    bad = [f for f, s in done.items() if "fail" in s or "empty" in s]
    if bad:
        print("· 수집 실패(기존 파일 유지): " + " ".join(bad))


def verify():
    """대응표 검산: BLS 값과 지금 파일(FRED) 값이 같은 달에 같은가"""
    bad = 0
    for tag in BLS_TAGS:
        ids = [f for f in tag_series(tag) if bls_id(f)]
        got = bls_fetch(ids)
        for f in ids:
            cur, b = read_csv(f), got.get(f)
            if not b:
                print("✗ %s(%s): BLS 응답 없음" % (f, bls_id(f))); bad += 1; continue
            common = sorted(set(cur) & set(b))
            diff = [(k, cur[k], b[k]) for k in common if abs(float(cur[k]) - float(b[k])) > 1e-6 * max(1, abs(float(b[k])))]
            old = [d for d in diff if d[0] < sorted(b)[-6]]            # 최근 5개월은 수정 시차로 다를 수 있음
            if old or len(common) < 12:
                print("✗ %s(%s): 겹치는 %d달 중 %d달 다름 %s" % (f, bls_id(f), len(common), len(diff), diff[:3])); bad += 1
            else:
                print("✓ %-18s %-15s %d달 같음%s · BLS 마지막 %s / 파일 %s" % (f, bls_id(f), len(common) - len(diff),
                      (" (최근 %d달은 수정 시차)" % len(diff)) if diff else "", max(b)[:7], max(cur)[:7] if cur else "-"))
    print("검산 %s" % ("통과" if not bad else "실패 %d건" % bad))
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag")
    ap.add_argument("--detect", action="store_true")
    ap.add_argument("--tags", default="", help="감지할 태그만 (쉼표)")
    ap.add_argument("--verify", action="store_true")
    a = ap.parse_args()
    if a.verify:
        sys.exit(1 if verify() else 0)
    if a.tag:
        refresh_tag(a.tag.upper())
    else:
        refresh(detect_only=a.detect, only=[t.strip().upper() for t in a.tags.split(",") if t.strip()] or None)


if __name__ == "__main__":
    main()

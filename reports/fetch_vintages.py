# -*- coding: utf-8 -*-
"""발표일·수정 이력 미러 (2026-11-03, 🧭 경제 상황 탭용)

사용자 규칙: "지표 값이 그래프에 찍히는 시점은 무조건 발표일, 값은 나중 수정치로 바꾸고,
수정 이전 값은 클릭하면 볼 수 있게만". → 사이트는 reports/data/<ID>.csv(현재 수정치) 값을
여기서 만든 '첫 발표일'에 찍고, 클릭하면 아래 수정 이력을 보여 준다.

산출물 (머리줄 없음)
  reports/data/rel/<ID>.csv   관측일,첫 발표일,첫 발표치,표시   — 표시 e = 발표일 추정(ALFRED 기록 시작 전·나중에 덧붙인 옛 역사)
  reports/data/vint/<ID>.csv  관측일,적용 시작일,값           — 수정 이력 전체 (FULL 목록만, 클릭 팝업용)
  reports/data/rel/_manifest.json   시리즈별 원본 CSV 지문 — --changed 가 바뀐 것만 다시 받게

원천 (둘 다 같은 'Observations by Real-Time Period' 자료라 결과가 같다)
  --backend alfred : ALFRED 웹 다운로드 양식(키 불필요, 주거 IP 에서만 — 이 PC·보고서 에이전트)
  --backend api    : FRED 공식 API (FRED_API_KEY, GitHub Actions 미러)
  기본(auto) = FRED_API_KEY 가 있으면 api, 없으면 alfred

'진짜 첫 발표' 판정: 빈티지를 시간순으로 훑으며, 앞선 빈티지들의 마지막 관측보다 새 관측만 그 빈티지의 첫 발표로 본다
(첫 빈티지는 과거 일괄 기록이라 그 안의 마지막 관측만). 나중 빈티지가 옛 역사를 덧붙인 것(예: 1961년에 1939~ 추가)은
첫 발표가 아님 → 그 시리즈의 보통 발표 지연(중앙값)으로 추정하고 e 표시.

  py reports/fetch_vintages.py              # 목록 전부
  py reports/fetch_vintages.py --changed    # 원본 CSV 가 바뀐 시리즈만 (미러·에이전트용)
  py reports/fetch_vintages.py --only PAYEMS,UNRATE
"""
import sys, os, re, io, csv, json, time, zipfile, hashlib, argparse, statistics, urllib.request, urllib.parse
from datetime import date, timedelta

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
VDIR = os.path.join(DATA, "vint")
RDIR = os.path.join(DATA, "rel")
MANI = os.path.join(RDIR, "_manifest.json")

# 수정 이력 전체를 남길 시리즈 (클릭 팝업에서 1차·2차·벤치마크 수정까지 보임). 나머지는 첫 발표만.
FULL = ["PAYEMS", "UNRATE", "PCEPI", "PCEPILFE", "NEWORDER", "FRGSHPUSM649NCIS", "CPIAUCSL", "CPILFESL",
        "PPIFIS", "RSAFS", "TCU", "DGORDER", "HOUST", "PERMIT", "GDPC1", "ICSA"]   # INDPRO 는 1919~ 수정이 많아 1.1MB → 첫 발표만
# 사이트 지표 목록(index.html) 밖이지만 경제 상황 탭이 쓰는 것
EXTRA = ["FRGSHPUSM649NCIS", "USALOLITOAASTSAM"]


def get(url, data=None, t=120):
    # User-Agent 는 파이썬 기본값 그대로 — ALFRED 다운로드는 낯선 UA 를 붙이면 응답을 안 준다(2026-11-03 실측: 기본 1.4초, 지정 UA 무응답)
    for i in range(4):
        try:
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"} if data else {})
            return urllib.request.urlopen(req, timeout=t).read()
        except Exception as e:
            if i == 3:
                raise
            time.sleep(3 + 5 * i)


def indicator_ids():
    """index.html INDICATORS 중 미국 FRED 원천 시리즈 id (계산 지표는 재료 시리즈)"""
    h = open(os.path.join(HERE, "..", "index.html"), encoding="utf-8").read()
    i = h.find("const INDICATORS = [")
    j = h.find("\n];", i)
    block = h[i:j]
    out = []
    for m in re.finditer(r"\{\s*id:'", block):
        k, depth = m.start(), 0
        while k < len(block):
            c = block[k]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    break
            k += 1
        e = block[m.start():k + 1]
        g = lambda f: (re.search(f + r":'([^']+)'", e) or [None, None])[1]
        if (g("country") or "US") != "US":
            continue
        src = g("source")
        if src is None:
            out.append(g("id"))
        elif src == "fredchg":
            out.append(g("fredId"))
        elif src == "freddiff":
            out += [g("dfA"), g("dfB")]
        elif src == "fredratio":
            num = re.search(r"fredNum:\s*(\[[^\]]*\]|'[^']+')", e)
            if num:
                out += re.findall(r"'([^']+)'", num.group(1))
            out.append(g("fredDen"))
    return [x for x in out if x]


def freq_days(obs):
    ks = sorted(obs)[-60:]
    if len(ks) < 3:
        return 30
    ds = [date.fromisoformat(b) - date.fromisoformat(a) for a, b in zip(ks, ks[1:])]
    return statistics.median(d.days for d in ds)


def period_end(o, fd):
    d = date.fromisoformat(o)
    if fd < 10:                                   # 주간: 관측일 = 그 주 끝(FRED 관례)
        return d
    if fd < 45:
        n = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
        return n - timedelta(days=1)
    if fd < 120:
        m = d.month + 3
        n = date(d.year + (m > 12), (m - 1) % 12 + 1, 1)
        return n - timedelta(days=1)
    return date(d.year, 12, 31)


def rows_alfred(sid):
    page = get("https://alfred.stlouisfed.org/series/downloaddata?seid=" + sid).decode("utf-8", "replace")
    vd = re.findall(r'<option[^>]*value="(\d{4}-\d{2}-\d{2})"', page)
    if not vd:
        raise RuntimeError("ALFRED 빈티지 목록 없음")
    s = re.search(r'name="form\[obs_start_date\]"[^>]*value="([^"]+)"', page).group(1)
    e = re.search(r'name="form\[obs_end_date\]"[^>]*value="([^"]+)"', page).group(1)
    f = [("form[units]", "lin"), ("form[obs_start_date]", s), ("form[obs_end_date]", e)]
    f += [("form[selected_vintage_dates][]", v) for v in vd]
    f += [("form[entered_vintage_dates]", ""), ("form[file_type]", "1"), ("form[file_format]", "csv"), ("form[download_data]", "")]
    b = get("https://alfred.stlouisfed.org/series/downloaddata?seid=" + sid, data=urllib.parse.urlencode(f).encode(), t=300)
    z = zipfile.ZipFile(io.BytesIO(b))
    name = [n for n in z.namelist() if n.lower().endswith(".csv")][0]
    rd = csv.reader(io.StringIO(z.read(name).decode("utf-8", "replace")))
    head = next(rd)
    out = []
    for r in rd:                                  # period_start_date, 값, realtime_start_date, realtime_end_date
        if len(r) >= 3 and r[0] and r[2]:
            out.append((r[0], r[2], r[1]))
    return out


def rows_api(sid, key):
    out, off = [], 0
    while True:
        u = ("https://api.stlouisfed.org/fred/series/observations?series_id=%s&api_key=%s&file_type=json"
             "&realtime_start=1776-07-04&realtime_end=9999-12-31&limit=100000&offset=%d&sort_order=asc") % (sid, key, off)
        j = json.loads(get(u, t=300))
        obs = j.get("observations", [])
        for o in obs:
            out.append((o["date"], o["realtime_start"], o["value"]))
        off += len(obs)
        if not obs or off >= int(j.get("count", 0)):
            break
        time.sleep(0.6)
    return out


def build(rows):
    hist = {}
    for o, rt, v in sorted((o[:10], rt[:10], v.strip()) for o, rt, v in rows):
        if v in ("", "."):
            continue
        try:
            x = float(v)
        except ValueError:
            continue
        h = hist.setdefault(o, [])
        if h and h[-1][0] == rt:
            h[-1] = (rt, x)
        elif not h or h[-1][1] != x:
            h.append((rt, x))
    return hist


def releases(hist):
    """관측 → (발표일, 첫 값 또는 None, 추정 여부)"""
    fd = freq_days(hist)
    firsts = sorted((h[0][0], o, h[0][1]) for o, h in hist.items())
    gen, front, v0 = {}, None, firsts[0][0] if firsts else None
    i = 0
    while i < len(firsts):
        rt = firsts[i][0]
        grp = []
        while i < len(firsts) and firsts[i][0] == rt:
            grp.append(firsts[i]); i += 1
        mx = max(o for _, o, _ in grp)
        for _, o, v in grp:
            ok = (o == mx) if rt == v0 else (front is None or o > front)
            gen[o] = (rt, v, ok)
        front = mx if front is None or mx > front else front
    lag = lambda o, rt: (date.fromisoformat(rt) - period_end(o, fd)).days
    lags = [lag(o, rt) for o, (rt, v, ok) in gen.items() if ok]
    L = statistics.median(lags[-120:]) if lags else (5 if fd < 10 else 30)
    out = {}
    for o, (rt, v, ok) in gen.items():
        if ok and not (-7 <= lag(o, rt) <= L + 150):
            ok = False
        if ok:
            out[o] = (rt, v, False)
        else:
            out[o] = ((period_end(o, fd) + timedelta(days=int(L))).isoformat(), None, True)
    return out, L


def fmt(x):
    s = ("%.6f" % x).rstrip("0").rstrip(".")
    return s if s not in ("-0", "") else "0"


def write_if_changed(path, text):
    old = open(path, encoding="utf-8").read() if os.path.exists(path) else None
    if old == text:
        return False
    open(path, "w", encoding="utf-8", newline="\n").write(text)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default="auto", choices=["auto", "alfred", "api"])
    ap.add_argument("--changed", action="store_true")
    ap.add_argument("--only", default="")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    key = os.environ.get("FRED_API_KEY", "")
    be = a.backend if a.backend != "auto" else ("api" if key else "alfred")
    os.makedirs(VDIR, exist_ok=True)
    os.makedirs(RDIR, exist_ok=True)
    ids = []
    for x in indicator_ids() + EXTRA + FULL:
        if x not in ids and os.path.exists(os.path.join(DATA, x + ".csv")):
            ids.append(x)
    # 일간 시세(금리·스프레드 등)는 고쳐지지 않고 발표 = 관측일이라 뺀다
    def daily(x):
        ks = [l.split(",")[0] for l in open(os.path.join(DATA, x + ".csv"), encoding="utf-8").read().split("\n")[-80:] if l]
        return freq_days(ks) < 6
    ids = [x for x in ids if not daily(x)]
    if a.only:
        ids = [x for x in a.only.split(",") if x]
    if a.list:
        print(len(ids), " ".join(ids))
        return
    man = json.load(open(MANI, encoding="utf-8")) if os.path.exists(MANI) else {}
    done = fail = skip = 0
    for n, sid in enumerate(ids, 1):
        src = os.path.join(DATA, sid + ".csv")
        hsh = hashlib.md5(open(src, "rb").read()).hexdigest() if os.path.exists(src) else ""
        need_v = sid in FULL and not os.path.exists(os.path.join(VDIR, sid + ".csv"))
        # ALFRED 가 원본 CSV 보다 뒤처져 있었으면(고용·CPI·PPI 는 BLS 에서 먼저 받음 — FRED·ALFRED 는 몇 시간 뒤) 따라올 때까지 다시 받는다
        try:
            csv_last = [l for l in open(src, encoding="utf-8").read().split("\n") if l][-1].split(",")[0]
        except Exception:
            csv_last = ""
        behind = man.get(sid, {}).get("last", "") < csv_last
        if a.changed and man.get(sid, {}).get("hash") == hsh and os.path.exists(os.path.join(RDIR, sid + ".csv")) and not need_v and not behind:
            skip += 1
            continue
        try:
            rows = rows_api(sid, key) if be == "api" else rows_alfred(sid)
            hist = build(rows)
            if not hist:
                raise RuntimeError("빈 자료")
            rel, L = releases(hist)
            txt = "".join("%s,%s,%s,%s\n" % (o, d, "" if v is None else fmt(v), "e" if est else "") for o, (d, v, est) in sorted(rel.items()))
            ch = write_if_changed(os.path.join(RDIR, sid + ".csv"), txt)
            if sid in FULL:
                vt = "".join("%s,%s,%s\n" % (o, rt, fmt(x)) for o in sorted(hist) for rt, x in hist[o])
                ch = write_if_changed(os.path.join(VDIR, sid + ".csv"), vt) or ch
            est = sum(1 for v in rel.values() if v[2])
            man[sid] = {"hash": hsh, "last": max(hist), "lag": L, "est": est, "src": be, "t": time.strftime("%Y-%m-%dT%H:%M:%S")}
            done += 1
            print("[%d/%d] %s 관측 %d · 추정 %d · 보통 지연 %s일%s" % (n, len(ids), sid, len(rel), est, L, " · 바뀜" if ch else ""), flush=True)
        except Exception as e:
            fail += 1
            print("[%d/%d] %s 실패 — 기존 파일 유지: %s" % (n, len(ids), sid, e), flush=True)
        time.sleep(1.0 if be == "alfred" else 0.6)
    write_if_changed(MANI, json.dumps(man, ensure_ascii=False, indent=0, sort_keys=True) + "\n")
    print("완료: 갱신 %d · 건너뜀 %d · 실패 %d (원천 %s)" % (done, skip, fail, be))


if __name__ == "__main__":
    main()

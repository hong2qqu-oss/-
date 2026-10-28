# -*- coding: utf-8 -*-
"""
지표 보고서용 결정론적 계산기 — 보고서에 들어가는 모든 YoY / MoM / 3개월 연율은 이 출력에서 복사한다.
LLM 암산 금지 (2026-09-12: YoY 기준월을 13개월 전으로 잘못 잡아 8/12 7월 CPI 보고서부터 YoY 전체가 한 칸 밀린 사고).

사용:
  python reports/calc_series.py CPIAUCSL CPILFESL            # 최근 2개월(당월·전월) 표
  python reports/calc_series.py --months 4 CPIAUCSL          # 최근 4개월
  python reports/calc_series.py --set cpi                    # 교본 CPI 세트 전부
  python reports/calc_series.py --set ppi
  python reports/calc_series.py --diff CPIAUCSL              # 레벨 대신 전월 차이(비농업고용 등 diffMode)
  python reports/calc_series.py --levels --set ism           # 수준 모드 — 확산지수·서베이(ISM·소비자신뢰·지역 연은)와 SAAR 수준(신규주택)
  python reports/calc_series.py --qtr ANXAVS NEWORDER        # 분기 평균 연율 — 핵심자본재 출하 → GDP 장비투자 추적

정의:
  YoY   = value[t] / value[t-12개월] - 1        (기준월 = 정확히 12개월 전 같은 월)
  MoM   = value[t] / value[t-1개월] - 1
  3M연율 = (value[t] / value[t-3개월])^4 - 1     (창 = t-2, t-1, t 세 달의 MoM 누적. t-3은 기준점)
  가속/감속 = 3M연율이 전월 3M연율보다 높으면 ▲, 낮으면 ▼  (부호가 아니라 변화로 판정)
  창 교체 = 이번 달 3M창에 새로 들어온 달(t)과 빠진 달(t-3) — 3개월 연율 변화를 서술할 때 반드시 명시
  --levels  (확산지수는 %변화가 무의미 — 수준과 차이로 본다)
    전월차 = value[t] - value[t-1] (포인트) / 3M평균 = t-2~t 평균, ▲▼ = 전월 3M평균 대비
    12M범위 = t-11~t 최저·최고 / 장기 백분위 = 시리즈 전 기간 중 value[t] 이하 비율
    '이보다 높았던 마지막 달' = t 이전에 value[t]보다 컸던 가장 최근 달 ('N개월 만의 최고' 문장의 근거)
    연초~ 평균 vs 작년 같은 기간 평균 vs 작년 전체 평균 (신규주택판매처럼 단월 표본오차가 큰 SAAR 수준용)
  --qtr     분기 평균 연율 = (이번 분기 평균 / 직전 분기 평균)^4 - 1. 분기 중 일부 달만 있으면 있는 달만 평균하고 그 달 수를 같이 찍는다
"""
import csv, sys, os, argparse, math
sys.stdout.reconfigure(encoding="utf-8")

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

SETS = {
    "cpi": ["CPIAUCSL", "CPILFESL", "CUSR0000SAH1", "CUSR0000SEHA", "CUSR0000SEHC", "CUSR0000SA0L2",
            "CUSR0000SACL1E", "CUSR0000SASLE",
            "CPIUFDSL", "CUSR0000SAF11", "CUSR0000SEFV", "CPIENGSL", "CUSR0000SETB01", "CUSR0000SEHF01",
            "CUSR0000SETA01", "CUSR0000SETA02", "CPIAPPSL", "CUSR0000SAM1", "CUSR0000SAM2", "CUSR0000SAS4",
            "CUSR0000SETG01"],
    "ppi": ["PPIFIS", "WPSFD4131", "WPSFD49116",
            "WPSFD49501", "WPSFD49502", "WPSFD49207", "WPSFD49209", "WPSFD49208", "WPSFD4111", "WPSFD4121",
            "WPSFD49213", "WPSFD49214", "WPSFD49206",
            "WPSID54", "WPSID53", "WPSID52", "WPSID61", "WPSID62", "WPSID63", "WPSID64",
            "WPS0571", "WPS057", "WPS101", "WPS102", "WPS081", "WPS061", "WPS0221", "WPS0122"],
    # 2026-10-28 편입 (교본 DURABLE·HOMESALES·CASESHILLER·ISM·CONF·RICHMOND 섹션)
    "durable": ["DGORDER", "ADXTNO", "ADXDNO", "A36SNO", "AMVPNO", "ANAPNO", "ADAPNO",
                "A31SNO", "A32SNO", "A33SNO", "A34SNO", "A35SNO",
                "ATCGNO", "ANDENO", "ADEFNO", "NEWORDER", "ANDEVS", "ANXAVS",
                "AMDMVS", "AMDMUO", "ANXAUO", "AMDMTI"],
    "homesales": ["HSN1F", "HSN1FNE", "HSN1FMW", "HSN1FS", "HSN1FW", "HSN1FNSA", "MSACSR",
                  "HNFSEPUSSA", "NHFSEPCS", "NHFSEPUCS", "NHFSEPNTS", "MNMFS", "MSPNHSUS", "ASPNHSUS"],
    "caseshiller": ["CSUSHPINSA", "CSUSHPISA", "SPCS20RNSA", "SPCS20RSA", "SPCS10RNSA", "SPCS10RSA", "HPIPONM226S"]
                   + [c + s for c in ["AT", "BO", "CR", "CH", "CE", "DA", "DN", "DE", "LV", "LX",
                                      "MI", "MN", "NY", "PH", "PO", "SD", "SF", "SE", "TP", "WD"] for s in ("XRNSA", "XRSA")],
    "ism": ["ISMM_PMI", "ISMM_NEWORD", "ISMM_PROD", "ISMM_EMP", "ISMM_SUPDEL", "ISMM_INV",
            "ISMM_CUSTINV", "ISMM_PRICES", "ISMM_BACKLOG", "ISMM_EXPORT", "ISMM_IMPORT"],
    "conf": ["CB_CCI", "CB_PSI", "CB_EI", "CB_BIZ_GOOD", "CB_BIZ_BAD", "CB_JOBS_PLENTY", "CB_JOBS_HARD", "CB_LABOR_DIFF",
             "CB_EXP_BIZ_UP", "CB_EXP_BIZ_DOWN", "CB_EXP_JOBS_MORE", "CB_EXP_JOBS_FEWER", "CB_EXP_INC_UP", "CB_EXP_INC_DOWN",
             "CB_INFL12"],
    "richmond": ["RICH_COMP", "RICH_SHIP", "RICH_NEWORD", "RICH_EMP", "RICH_BACKLOG", "RICH_CAPUTIL", "RICH_VENDLEAD",
                 "RICH_WAGE", "RICH_FGINV", "RICH_RAWINV", "RICH_LOCAL", "RICH_PRICE_PAID", "RICH_PRICE_RECD",
                 "RICH_E_SHIP", "RICH_E_NEWORD", "RICH_E_EMP", "RICH_E_CAPEX", "RICH_E_LOCAL",
                 "RICH_E_PRICE_PAID", "RICH_E_PRICE_RECD"],
}

def load(sid):
    path = os.path.join(DATA, sid + ".csv")
    d = {}
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.reader(f):
            if len(r) < 2:
                continue
            try:
                d[r[0][:7]] = float(r[1])
            except ValueError:
                continue
    return d

def shift(ym, k):
    y, m = int(ym[:4]), int(ym[5:7])
    m -= k
    while m <= 0:
        m += 12; y -= 1
    while m > 12:
        m -= 12; y += 1
    return f"{y:04d}-{m:02d}"

def pct(a, b):
    return (a / b - 1.0) * 100.0

def calc(d, ym, diff=False):
    need = [ym, shift(ym, 1), shift(ym, 3), shift(ym, 4), shift(ym, 12)]
    if any(k not in d for k in need):
        return None
    if diff:
        mom = d[ym] - d[shift(ym, 1)]
        m3 = (d[ym] - d[shift(ym, 3)]) / 3.0
        yoy = d[ym] - d[shift(ym, 12)]
        prev3 = (d[shift(ym, 1)] - d[shift(ym, 4)]) / 3.0
        return dict(yoy=yoy, mom=mom, a3=m3, a3prev=prev3, level=d[ym])
    yoy = pct(d[ym], d[shift(ym, 12)])
    mom = pct(d[ym], d[shift(ym, 1)])
    a3 = ((d[ym] / d[shift(ym, 3)]) ** 4 - 1.0) * 100.0
    a3prev = ((d[shift(ym, 1)] / d[shift(ym, 4)]) ** 4 - 1.0) * 100.0
    return dict(yoy=yoy, mom=mom, a3=a3, a3prev=a3prev, level=d[ym])

def levels(sid, d, ym):
    if ym not in d:
        return f"   {ym}: 관측 없음(미공표)"
    v = d[ym]
    p = d.get(shift(ym, 1))
    w = [d.get(shift(ym, k)) for k in range(3)]
    wp = [d.get(shift(ym, k)) for k in range(1, 4)]
    a3 = sum(w) / 3 if None not in w else None
    a3p = sum(wp) / 3 if None not in wp else None
    yr = [d[shift(ym, k)] for k in range(12) if shift(ym, k) in d]
    lo = min((d[shift(ym, k)], shift(ym, k)) for k in range(12) if shift(ym, k) in d)
    hi = max((d[shift(ym, k)], shift(ym, k)) for k in range(12) if shift(ym, k) in d)
    hist = [x for k, x in d.items() if k <= ym]
    pctl = 100.0 * sum(1 for x in hist if x <= v) / len(hist)
    higher = max((k for k, x in d.items() if k < ym and x > v), default=None)
    lower = max((k for k, x in d.items() if k < ym and x < v), default=None)
    arrow = "" if a3 is None or a3p is None else ("▲" if a3 > a3p else ("▼" if a3 < a3p else "＝"))
    f = lambda x, fmt="{:.1f}": "  -  " if x is None else fmt.format(x)
    line = (f"   {ym} {v:8.2f} {f(None if p is None else v - p, '{:+.2f}'):>7} {f(a3, '{:.2f}'):>7} {f(a3p, '{:.2f}'):>7}  {arrow:2} "
            f"{lo[0]:g}({lo[1]})~{hi[0]:g}({hi[1]})  {pctl:5.1f}% ({min(d)}~)  ↑{higher or '없음'} / ↓{lower or '없음'}")
    if p is None:
        line += "  ※전월 관측 없음"
    y = ym[:4]; ly = str(int(y) - 1)
    ytd = [d[k] for k in d if k[:4] == y and k <= ym]
    lyy = [d[k] for k in d if k[:4] == ly and k[5:] <= ym[5:]]
    lyf = [d[k] for k in d if k[:4] == ly]
    if ytd and lyy:
        line += (f"\n            연초~ 평균 {sum(ytd)/len(ytd):.2f} ({len(ytd)}개월) | {ly} 같은 기간 {sum(lyy)/len(lyy):.2f}"
                 f" | {ly} 전체 {sum(lyf)/len(lyf):.2f} ({len(lyf)}개월)")
    return line


def quarter(sid, d):
    last = max(d)
    q0 = shift(last, (int(last[5:7]) - 1) % 3)          # 이번 분기 첫 달
    cur = [d[shift(q0, -k)] for k in range(3) if shift(q0, -k) <= last and shift(q0, -k) in d]
    prev = [d.get(shift(q0, k)) for k in (1, 2, 3)]
    if None in prev or not cur:
        return f"== {sid}: 분기 데이터 부족"
    a, b = sum(cur) / len(cur), sum(prev) / 3
    qn = f"{q0[:4]}Q{(int(q0[5:7]) - 1) // 3 + 1}"
    return (f"== {sid}  {qn} 평균 {a:,.1f} ({len(cur)}/3개월) vs 직전 분기 {b:,.1f} → 분기 연율 {((a / b) ** 4 - 1) * 100:.2f}%"
            + ("" if len(cur) == 3 else f"   ※{qn}은 {len(cur)}개월만 — 있는 달만의 평균 (남은 달은 미반영)"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("series", nargs="*")
    ap.add_argument("--set", choices=sorted(SETS))
    ap.add_argument("--months", type=int, default=2)
    ap.add_argument("--asof", help="YYYY-MM (기본: 시리즈 마지막 월)")
    ap.add_argument("--diff", action="store_true", help="레벨 차이 모드(비농업고용 등)")
    ap.add_argument("--levels", action="store_true", help="수준 모드(확산지수·서베이·SAAR 수준)")
    ap.add_argument("--qtr", action="store_true", help="분기 평균 연율")
    a = ap.parse_args()
    series = list(a.series)
    if a.set:
        series = SETS[a.set] + series
    if not series:
        ap.error("시리즈 ID 또는 --set 필요")
    if a.qtr:
        for sid in series:
            try:
                print(quarter(sid, load(sid)))
            except FileNotFoundError:
                print(f"== {sid}: 파일 없음 (reports/data/{sid}.csv)")
        return
    if a.levels:
        for sid in series:
            try:
                d = load(sid)
            except FileNotFoundError:
                print(f"== {sid}: 파일 없음 (reports/data/{sid}.csv)"); continue
            last = max(d) if not a.asof else a.asof
            print(f"== {sid}  마지막 관측 {max(d)}  (수준 모드)")
            print(f"   {'월':7} {'값':>8} {'전월차':>7} {'3M평균':>7} {'전3M':>7}  방향 12M범위(최저~최고)  장기백분위  이보다 ↑높았던/↓낮았던 마지막 달")
            for k in range(a.months):
                print(levels(sid, d, shift(last, k)))
        print("\n주의: 확산지수는 50(ISM)·0(지역 연은) 기준선 대비 위치와 전월차로 읽는다. ▲▼는 3M평균 기준. "
              "'N개월 만의 최고/최저'는 ↑/↓ 칸의 달로만 쓴다.")
        return
    unit = "" if a.diff else "%"
    for sid in series:
        try:
            d = load(sid)
        except FileNotFoundError:
            print(f"== {sid}: 파일 없음 (reports/data/{sid}.csv)"); continue
        last = max(d) if not a.asof else a.asof
        print(f"== {sid}  마지막 관측 {max(d)}  레벨 {d[max(d)]:g}")
        print(f"   {'월':7} {'YoY':>8} {'MoM':>8} {'3M연율':>8} {'전월3M':>8}  방향  창(들어옴/빠짐)   12M기준월")
        for k in range(a.months):
            ym = shift(last, k)
            r = calc(d, ym, a.diff)
            if r is None:
                print(f"   {ym}: 데이터 부족"); continue
            arrow = "▲" if r["a3"] > r["a3prev"] else ("▼" if r["a3"] < r["a3prev"] else "＝")
            print(f"   {ym} {r['yoy']:8.2f}{unit} {r['mom']:8.2f}{unit} {r['a3']:8.2f}{unit} {r['a3prev']:8.2f}{unit}  {arrow}   "
                  f"{ym[5:]}월 in / {shift(ym,3)[5:]}월 out   {shift(ym,12)}")
    print("\n주의: 3M연율의 부호(누적 방향)와 당월 MoM(단월 방향)은 별개다 — 둘 다 써라. "
          "가속/감속은 ▲▼(전월 3M 대비)로만 판정하고, 상위·하위 항목(식품⊃외식, 교통⊃항공)을 같은 개수로 세지 마라.")

if __name__ == "__main__":
    main()

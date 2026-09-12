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

정의:
  YoY   = value[t] / value[t-12개월] - 1        (기준월 = 정확히 12개월 전 같은 월)
  MoM   = value[t] / value[t-1개월] - 1
  3M연율 = (value[t] / value[t-3개월])^4 - 1     (창 = t-2, t-1, t 세 달의 MoM 누적. t-3은 기준점)
  가속/감속 = 3M연율이 전월 3M연율보다 높으면 ▲, 낮으면 ▼  (부호가 아니라 변화로 판정)
  창 교체 = 이번 달 3M창에 새로 들어온 달(t)과 빠진 달(t-3) — 3개월 연율 변화를 서술할 때 반드시 명시
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

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("series", nargs="*")
    ap.add_argument("--set", choices=sorted(SETS))
    ap.add_argument("--months", type=int, default=2)
    ap.add_argument("--asof", help="YYYY-MM (기본: 시리즈 마지막 월)")
    ap.add_argument("--diff", action="store_true", help="레벨 차이 모드(비농업고용 등)")
    a = ap.parse_args()
    series = list(a.series)
    if a.set:
        series = SETS[a.set] + series
    if not series:
        ap.error("시리즈 ID 또는 --set 필요")
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

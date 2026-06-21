# -*- coding: utf-8 -*-
"""
KRX 코스피 투자자별 수급 수집 → supply_data.json
- 현물(KOSPI): pykrx로 일별 개인/외국인/기관 순매수(거래대금, 억원)
- 파생(선물/옵션콜/옵션풋/주식선물): 아직 미구현 → null (2단계에서 KRX 직접 호출 예정)
환경변수 KRX_ID / KRX_PW 필요 (KRX 무료 회원).
"""
import json
import datetime as dt
from pathlib import Path
from pykrx import stock

OUT = Path(__file__).parent / "supply_data.json"
EOK = 100_000_000  # 1억

def to_eok(v):
    return round(float(v) / EOK)

def fetch_spot(fromdate, todate):
    """현물 KOSPI 일별 투자자별 순매수(거래대금) → {YYYY-MM-DD: {ind,frn,inst}}"""
    df = stock.get_market_trading_value_by_date(fromdate, todate, "KOSPI", detail=False)
    # columns: 기관합계 / 기타법인 / 개인 / 외국인합계 / 전체
    out = {}
    for idx, row in df.iterrows():
        d = idx.strftime("%Y-%m-%d")
        out[d] = {
            "ind":  to_eok(row["개인"]),
            "frn":  to_eok(row["외국인합계"]),
            "inst": to_eok(row["기관합계"]),
        }
    return out

def main():
    today = dt.date.today()
    start = today - dt.timedelta(days=230)  # ~150 거래일 확보
    f, t = start.strftime("%Y%m%d"), today.strftime("%Y%m%d")
    print(f"수집 기간: {f} ~ {t}")

    spot = fetch_spot(f, t)
    dates = sorted(spot.keys())
    print(f"현물 거래일 수: {len(dates)}")

    days = []
    for d in dates:
        days.append({
            "date": d,
            "v": {
                "spot":    spot[d],
                "fut":     None,   # 파생 2단계
                "callopt": None,
                "putopt":  None,
                "sfut":    None,
            }
        })

    payload = {
        "updated": dt.datetime.now().isoformat(timespec="seconds"),
        "unit": "억원",
        "source": "KRX (현물=실데이터 / 파생=준비중)",
        "days": days,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"저장 완료: {OUT}  ({len(days)}일)")
    if days:
        print("최신일 예시:", days[-1])

if __name__ == "__main__":
    main()

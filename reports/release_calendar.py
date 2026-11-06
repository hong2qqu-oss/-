# -*- coding: utf-8 -*-
"""보고서 대상 지표의 발표 일정표 → reports/data/release_calendar.json (2026-11-06, 텔레그램 할일봇 발표 알림용)

사용자: "보고서 쓰게 만들어 둔 지표들은 발표 시간에 텔레그램 알람" — 할일봇(todo-bot/econ_alerts.py)이 이 파일을 읽어
발표 시각에 알린다. 할일봇이 매일 아침 이 스크립트를 다시 돌린다(일정이 바뀌어도 따라감).

날짜: 미국 통계 = FRED 발표 일정표(fred.stlouisfed.org/releases/calendar?rid=…, 주거 IP 에서만 열림)
      FOMC = 연준 일정 페이지, 일본은행 = 결정회의 일정표(주요 의견 공표일 포함), 한국은행 = 금통위 일정 표
      ISM·컨퍼런스보드·리치먼드 = FRED 에 없어 발표 규칙(첫 영업일 / 마지막 화요일 / 넷째 화요일)
시각: 기관 공식 시각(FRED 표의 시각은 FRED 가 올린 시각이라 가동률 등은 공식과 다름) — 미국 동부시간 → UTC 로 저장.

  py reports/release_calendar.py          # 오늘-2일 ~ +75일
"""
import sys, os, re, json, time, html, urllib.request
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "release_calendar.json")
ET, KST = ZoneInfo("America/New_York"), ZoneInfo("Asia/Seoul")
sys.path.insert(0, HERE)

# 태그 → (이름, FRED 발표 번호, 공식 시각 ET, FRED 표 시각 거르기)
US = {
    "NFP":         ("미국 고용보고서 (NFP·실업률)", 50, "08:30", None),
    "CPI":         ("미국 CPI", 10, "08:30", None),
    "PPI":         ("미국 PPI", 46, "08:30", None),
    "RETAIL":      ("미국 소매판매", 9, "08:30", None),
    "PCE":         ("미국 PCE 물가·개인소득", 54, "08:30", None),
    "GDP":         ("미국 GDP", 53, "08:30", None),
    "CLAIMS":      ("미국 실업수당 청구", 180, "08:30", None),
    "ADP":         ("ADP 민간고용", 194, "08:15", None),
    "DURABLE":     ("미국 내구재 수주 (속보)", 95, "08:30", "7:30 am"),   # M3 중 속보만 (9:00 am CT 줄은 공장주문 전체판)
    "HOMESALES":   ("미국 신규주택판매", 97, "10:00", None),
    "CASESHILLER": ("케이스실러 주택가격", 199, "09:00", None),
    "CAPUTIL":     ("미국 산업생산·설비가동률", 13, "09:15", None),
    "FREIGHT":     ("캐스 화물운송지수", 280, "09:00", None),           # 공식 시각 없음 — 오전 중
}
RULE = {  # FRED 에 없는 서베이 — 발표 규칙
    "ISM":      ("ISM 제조업 PMI", "10:00"),
    "CONF":     ("컨퍼런스보드 소비자신뢰", "10:00"),
    "RICHMOND": ("리치먼드 연은 제조업", "10:00"),
}


def get(url, t=40):
    for i in range(3):
        try:
            return urllib.request.urlopen(url, timeout=t).read().decode("utf-8", "replace")
        except Exception:
            if i == 2:
                raise
            time.sleep(3)


def at_et(d, hhmm):
    h, m = map(int, hhmm.split(":"))
    return datetime(d.year, d.month, d.day, h, m, tzinfo=ET)


def at_kst(d, hhmm):
    h, m = map(int, hhmm.split(":"))
    return datetime(d.year, d.month, d.day, h, m, tzinfo=KST)


def ev(tag, name, when, note="", est=False, kind="release"):
    u = when.astimezone(timezone.utc)
    return {"id": "%s|%s|%s" % (tag, kind, when.date().isoformat()), "tag": tag, "kind": kind, "name": name,
            "at": u.strftime("%Y-%m-%dT%H:%M:%SZ"), "kst": when.astimezone(KST).strftime("%Y-%m-%d %H:%M"),
            "note": note, "est": est}


def fred_dates(rid, years, only_time=None):
    out = []
    for y in years:
        h = get("https://fred.stlouisfed.org/releases/calendar?rid=%d&y=%d" % (rid, y))
        i, j = h.find("<table"), h.find("</table>", h.find("<table"))
        t = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", h[i:j]))) if i >= 0 else ""
        for m in re.finditer(r"\w+day (\w+ \d\d, \d{4}) (?:Updated )?(\d+:\d\d [ap]m)?", t):
            if only_time and m.group(2) != only_time:
                continue
            out.append(datetime.strptime(m.group(1), "%B %d, %Y").date())
        time.sleep(1)
    return sorted(set(out))


def biz_first(y, mo):          # 그달 첫 영업일 (1/1·1/2 월 대체휴일·노동절 제외)
    d = date(y, mo, 1)
    while d.weekday() >= 5 or (d.month == 1 and (d.day == 1 or (d.day == 2 and d.weekday() == 0))) or (d.month == 9 and d.weekday() == 0 and d.day <= 7):
        d += timedelta(days=1)
    return d


def nth_tue(y, mo, n):
    d = date(y, mo, 1)
    d += timedelta(days=(1 - d.weekday()) % 7)
    return d + timedelta(days=7 * (n - 1))


def last_tue(y, mo):
    d = date(y + (mo == 12), mo % 12 + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - 1) % 7)


def fomc(years):
    import fetch_fomc as F
    t = re.sub(r"\s+", " ", F.page_text(F.get(F.CAL)))
    out = []
    for blk in re.finditer(r"(\d{4}) FOMC Meetings(.*?)(?=\d{4} FOMC Meetings|$)", t):
        y = int(blk.group(1))
        if y not in years:
            continue
        for m in re.finditer(r"\b(" + "|".join(k.capitalize() for k in F.MONTHS) + r")(?:/(\w+))? (\d{1,2})(?:-(\d{1,2}))?(\*)?", blk.group(2)):
            mon = F.MONTHS[(m.group(2) if m.group(2) and m.group(2).lower() in F.MONTHS else m.group(1)).lower()]
            try:
                out.append((date(y, mon, int(m.group(4) or m.group(3))), bool(m.group(5))))
            except ValueError:
                pass
    return sorted(set(out))


def boj():
    import fetch_cb as C
    h = C.get(C.BOJ_SCHED)
    out, year = [], None
    for part in re.split(r"(?=<table)", h):
        ym = re.search(r"(\d{4})年", re.sub(r"<[^>]+>", " ", part[:3000]))
        for row in re.split(r"<tr", part):
            cells = [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", c))).replace(">", " ").strip() for c in re.split(r"<t[dh]", row)[1:]]
            if not cells or not re.search(r"\d+月", cells[0]):
                continue
            yy = int(ym.group(1)) if ym else date.today().year
            toks = re.findall(r"(?:(\d+)月\s*)?(\d+)日", cells[0])   # '10月29日（木）・30日（金）' · '4月30日・5月1日'
            if not toks:
                continue
            mo = None
            for m_, _ in toks:
                if m_:
                    mo = int(m_)
            dec = date(yy, mo, int(toks[-1][1]))   # 둘째 날 = 결정일
            op = None
            if len(cells) > 2:
                om = re.search(r"(?:(\d{4})年\s*)?(\d+)月\s*(\d+)日", cells[2])
                if om:
                    oy = int(om.group(1)) if om.group(1) else (yy + 1 if int(om.group(2)) < mo else yy)
                    op = date(oy, int(om.group(2)), int(om.group(3)))
            out.append((dec, op))
    return sorted(set(out))


def bok():
    import fetch_cb as C
    h = C.get(C.BOK_LIST)
    i = h.find('id="tableId"')
    yy = re.search(r"(\d{4})년도 제\d+차|\((\d{2})\d{2}\)", h)
    year = int(yy.group(1)) if yy and yy.group(1) else 2000 + int(yy.group(2)) if yy else date.today().year
    out = []
    j = h.find("</table>", i)   # 회의마다 첨부 HTML 이 길어 표 끝까지 읽어야 함
    for row in re.split(r"<tr", h[i:j if j > 0 else len(h)])[1:]:
        cells = [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", c))).strip() for c in re.split(r"<t[dh]", row)[1:]]
        if not cells:
            continue
        m = re.search(r"(\d{1,2})월\s*(\d{1,2})일\s*\(([월화수목금토일])\)", cells[0])
        if m:
            out.append(date(year, int(m.group(1)), int(m.group(2))))
    return sorted(set(out))


def bok_minutes_day(d):         # 회의일로부터 2주 경과 후 첫 화요일 (한은 일정 표 주석)
    x = d + timedelta(days=15)
    while x.weekday() != 1:
        x += timedelta(days=1)
    return x


def main():
    today = date.today()
    lo, hi = today - timedelta(days=2), today + timedelta(days=75)
    years = sorted({lo.year, hi.year})
    evs, fail = [], []
    for tag, (name, rid, hhmm, only) in US.items():
        try:
            for d in fred_dates(rid, years, only):
                if lo <= d <= hi:
                    evs.append(ev(tag, name, at_et(d, hhmm), "공식 시각 비공개 — 오전 중" if tag == "FREIGHT" else ""))
        except Exception as e:
            fail.append("%s: %s" % (tag, e))
    for y in years:
        for mo in range(1, 13):
            for tag, d in (("ISM", biz_first(y, mo)), ("CONF", last_tue(y, mo)), ("RICHMOND", nth_tue(y, mo, 4))):
                if lo <= d <= hi:
                    evs.append(ev(tag, RULE[tag][0], at_et(d, RULE[tag][1]), "발표 규칙으로 잡은 날짜", est=True))
    try:
        for d, sep in fomc(years):
            if lo <= d <= hi:
                evs.append(ev("FOMC", "FOMC 금리 결정" + (" (경제전망 포함)" if sep else ""), at_et(d, "14:00"), "의장 기자회견 30분 뒤"))
            mi = d + timedelta(days=21)
            if lo <= mi <= hi:
                evs.append(ev("FOMC_MINUTES", "FOMC 의사록 (%d/%d 회의)" % (d.month, d.day), at_et(mi, "14:00"), "회의 3주 뒤 규칙", est=True))
    except Exception as e:
        fail.append("FOMC: %s" % e)
    try:
        for d, op in boj():
            if lo <= d <= hi:
                evs.append(ev("BOJ", "일본은행 금리 결정", at_kst(d, "12:00"), "회의 끝나는 대로 — 보통 정오 전후", est=True))
            if op and lo <= op <= hi:
                evs.append(ev("BOJ_OPINION", "일본은행 주요 의견 (%d/%d 회의)" % (d.month, d.day), at_kst(op, "08:50")))
    except Exception as e:
        fail.append("BOJ: %s" % e)
    try:
        for d in bok():
            if lo <= d <= hi:
                evs.append(ev("BOK", "한국은행 금통위 기준금리 결정", at_kst(d, "10:00"), "오전 10시 무렵", est=True))
            mi = bok_minutes_day(d)
            if lo <= mi <= hi:
                evs.append(ev("BOK_MINUTES", "금통위 의사록 (%d/%d 회의)" % (d.month, d.day), at_kst(mi, "16:00"), "회의 2주 뒤 첫 화요일 규칙", est=True))
    except Exception as e:
        fail.append("BOK: %s" % e)
    evs.sort(key=lambda e: (e["at"], e["tag"]))
    body = {"events": evs, "fail": fail}
    old = json.load(open(OUT, encoding="utf-8")) if os.path.exists(OUT) else {}
    if old.get("events") != evs or old.get("fail") != fail:   # 바뀔 때만 씀 (빈 커밋 방지)
        body["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        tmp = OUT + ".tmp"
        json.dump(body, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        os.replace(tmp, OUT)
    print("일정 %d건 (%s ~ %s)%s" % (len(evs), lo, hi, " · 실패 " + "; ".join(fail) if fail else ""))


if __name__ == "__main__":
    main()

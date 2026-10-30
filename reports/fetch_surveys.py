# -*- coding: utf-8 -*-
"""
FRED에 없는 서베이 3종을 보도자료·공개 엑셀에서 받아 reports/data/<ID>.csv (date,value 머리줄 없음)로 저장한다.
FRED 미러와 같은 형식이라 calc_series.py 가 그대로 읽는다. 2026-10-28 사용자 요청으로 보고서 편입.

  ism       ISM 제조업 PMI — PR Newswire 보도자료의 'MANUFACTURING AT A GLANCE' 표 (하위지수 11종)
  cb        컨퍼런스보드 소비자신뢰 — PR Newswire 보도자료 본문 (총지수·현재·기대 + 응답 비율 10종 + 12개월 기대인플레)
  richmond  리치먼드 연은 제조업 — 리치먼드 연은 공개 엑셀 mfg_historicaldata.xlsx (SA 현재지수 + 6개월 기대)
  cass      캐스 화물지수 — 캐스 공개 엑셀(출하량·운임지출 원계열+계절조정, 트럭 운임지수) + 그 달 보고서 페이지
  all       위 넷 전부

사용:
  python reports/fetch_surveys.py all                 # 최신 보도자료만 확인 (평소 — 보고서 에이전트가 매 실행 호출)
  python reports/fetch_surveys.py ism --backfill      # 과거 보도자료 목록 2쪽(약 4년)을 훑어 이력 채우기 (최초 1회)
  python reports/fetch_surveys.py cb --out 임시폴더    # 시험용 — 다른 폴더에 쓰기

보도자료 원문(본문 텍스트)은 .claude/src_cache/<종류>_<YYYY-MM>.txt 에 저장된다(로컬 전용, 저장소에 안 올라감).
에이전트는 업종 코멘트·응답자 발언·구매 계획처럼 숫자 표 밖의 내용을 거기서 읽는다.

검산(통과 못 하면 그 달은 쓰지 않고 '검산 실패'로 멈춘다 — 값 추정 금지):
  ISM  PMI = 신규주문·생산·고용·공급자 납기·재고 5개 단순평균 (±0.15)
  CB   총지수 = 0.4×현재상황 + 0.6×기대 (±0.15)  — 현재 2개·기대 3개 하위지수의 평균이므로
  RICH 종합 = 0.33×출하 + 0.40×신규주문 + 0.27×고용 (±1, 발표치가 정수 반올림)
"""
import argparse, csv, html, io, os, re, subprocess, sys, time, zipfile
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from xml.etree import ElementTree as ET

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
SRC = os.path.join(HERE, "..", ".claude", "src_cache")
PRN = "https://www.prnewswire.com"
MONTHS = {m: i + 1 for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july",
                                          "august", "september", "october", "november", "december"])}


def get(url, binary=False):
    # curl 로 받는다 — PR Newswire 는 파이썬 urllib 기본 요청을 403 으로 막고 curl 은 받아준다 (2026-10-28 확인)
    r = subprocess.run(["curl", "-sSL", "--fail", "-m", "40", url], capture_output=True)
    if r.returncode != 0:
        raise ValueError(f"받기 실패 ({r.returncode}) {url} — {r.stderr.decode('utf-8', 'replace').strip()[:200]}")
    return r.stdout if binary else r.stdout.decode("utf-8", "replace")


# ── CSV 입출력 ───────────────────────────────────────────────────────────
def load(out, sid):
    p = os.path.join(out, sid + ".csv")
    d = {}
    if os.path.exists(p):
        with open(p, encoding="utf-8-sig") as f:
            for r in csv.reader(f):
                if len(r) >= 2:
                    try:
                        d[r[0][:7]] = float(r[1])
                    except ValueError:
                        pass
    return d


def save(out, sid, d):
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, sid + ".csv"), "w", encoding="utf-8", newline="\n") as f:
        for ym in sorted(d):
            f.write(f"{ym}-01,{d[ym]:g}\n")


def ym_add(ym, k):
    y, m = int(ym[:4]), int(ym[5:7]) + k
    while m <= 0:
        m += 12; y -= 1
    while m > 12:
        m -= 12; y += 1
    return f"{y:04d}-{m:02d}"


def cache_text(kind, ym, text):
    os.makedirs(SRC, exist_ok=True)
    with open(os.path.join(SRC, f"{kind}_{ym}.txt"), "w", encoding="utf-8") as f:
        f.write(text)


# ── HTML → 텍스트·표 ─────────────────────────────────────────────────────
class Tables(HTMLParser):
    def __init__(self):
        super().__init__(); self.tables = []; self._t = None; self._r = None; self._c = None

    def handle_starttag(self, tag, a):
        if tag == "table": self._t = []
        elif tag == "tr" and self._t is not None: self._r = []
        elif tag in ("td", "th") and self._r is not None: self._c = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._c is not None:
            self._r.append(re.sub(r"\s+", " ", "".join(self._c)).strip()); self._c = None
        elif tag == "tr" and self._r is not None:
            if self._t is not None: self._t.append(self._r)
            self._r = None
        elif tag == "table" and self._t is not None:
            self.tables.append(self._t); self._t = None

    def handle_data(self, d):
        if self._c is not None: self._c.append(d)


def page_text(h):
    h = re.sub(r"<script.*?</script>|<style.*?</style>", "", h, flags=re.S | re.I)
    h = re.sub(r"</(p|tr|h\d|li|div|table)>|<br\s*/?>", "\n", h, flags=re.I)
    h = re.sub(r"<t[dh][^>]*>", " | ", h, flags=re.I)
    t = html.unescape(re.sub(r"<[^>]+>", "", h))
    t = t.replace("“", '"').replace("”", '"').replace("’", "'").replace("–", "-").replace("—", " - ").replace("−", "-")
    t = re.sub(r"[ \t\xa0]+", " ", t)
    return re.sub(r"\n\s*\n+", "\n", t)


def release_body(t, start_pat, end_pats=("About The Conference Board", "About Institute for Supply Management", "SOURCE ")):
    m = re.search(start_pat, t)
    if not m: return None
    i = max(0, t.rfind("\n", 0, m.start()))
    j = min([k for k in (t.find(e, m.end()) for e in end_pats) if k > 0] or [len(t)])
    return t[i:j].strip()


def prn_links(source, pattern, pages):
    links = []
    for p in range(1, pages + 1):
        h = get(f"{PRN}/news/{source}/?page={p}&pagesize=100" if pages > 1 else f"{PRN}/news/{source}/")
        for l in re.findall(r'href="(/news-releases/[^"]+\.html)"', h):
            if re.search(pattern, l) and l not in links:
                links.append(l)
        if pages > 1: time.sleep(1)
    return links


# ── ISM 제조업 ──────────────────────────────────────────────────────────
ISM_ROWS = [("Manufacturing PMI", "ISMM_PMI"), ("New Orders", "ISMM_NEWORD"), ("Production", "ISMM_PROD"),
            ("Employment", "ISMM_EMP"), ("Supplier Deliveries", "ISMM_SUPDEL"), ("Inventories", "ISMM_INV"),
            ("Customers' Inventories", "ISMM_CUSTINV"), ("Prices", "ISMM_PRICES"), ("Backlog of Orders", "ISMM_BACKLOG"),
            ("New Export Orders", "ISMM_EXPORT"), ("Imports", "ISMM_IMPORT")]
ISM_PMI5 = ["ISMM_NEWORD", "ISMM_PROD", "ISMM_EMP", "ISMM_SUPDEL", "ISMM_INV"]


def ism_parse(h, link):
    m = re.search(r"-(" + "|".join(MONTHS) + r")-(\d{4})-", link)
    if not m: raise ValueError("링크에서 데이터월을 못 읽음: " + link)
    ym = f"{m.group(2)}-{MONTHS[m.group(1)]:02d}"
    tp = Tables(); tp.feed(h)
    tab = next((t for t in tp.tables if any(r and r[0].startswith("Manufacturing PMI") for r in t)
                and any(r and r[0] == "New Orders" for r in t)), None)
    if tab is None: raise ValueError(f"{ym}: 'MANUFACTURING AT A GLANCE' 표 없음")
    cur, prev = {}, {}
    for r in tab:
        name = (r[0] if r else "").replace("®", "").replace("’", "'").strip()
        for label, sid in ISM_ROWS:
            if name == label and len(r) >= 3 and sid not in cur:
                try:
                    cur[sid], prev[sid] = float(r[1]), float(r[2])
                except ValueError:
                    pass
    if len(cur) != len(ISM_ROWS):
        raise ValueError(f"{ym}: 표 항목 {len(cur)}/{len(ISM_ROWS)}개만 읽힘 — {sorted(set(s for _, s in ISM_ROWS) - set(cur))}")
    for d in (cur, prev):
        avg = sum(d[s] for s in ISM_PMI5) / 5
        if abs(avg - d["ISMM_PMI"]) > 0.15:
            raise ValueError(f"{ym}: 검산 실패 — 5개 평균 {avg:.2f} vs PMI {d['ISMM_PMI']}")
    return ym, cur, prev


def ism(out, backfill):
    links = prn_links("institute-for-supply-management", r"/manufacturing-pmi-at-", 2 if backfill else 1)
    series = {sid: load(out, sid) for _, sid in ISM_ROWS}
    last = max(series["ISMM_PMI"]) if series["ISMM_PMI"] else "0000-00"
    parsed = []
    for l in links:
        m = re.search(r"-(" + "|".join(MONTHS) + r")-(\d{4})-", l)
        if not m: continue
        ym = f"{m.group(2)}-{MONTHS[m.group(1)]:02d}"
        if not backfill and ym <= last: continue
        h = get(PRN + l); time.sleep(0.5 if backfill else 0)
        try:
            ym, cur, prev = ism_parse(h, l)
        except ValueError as e:
            print("  ✗ ISM", e); continue
        parsed.append((ym, cur, prev))
        body = release_body(page_text(h), r"\d{4} /PRNewswire/")    # 발표일 줄('TEMPE, Ariz., Oct. 1, 2026 /PRNewswire/')부터
        if body: cache_text("ism", ym, PRN + l + "\n\n" + body)
    # 오래된 달부터 쓰고, 다음 달 보도자료의 '전월' 값(수정치)으로 덮는다
    for ym, cur, prev in sorted(parsed):
        for sid in cur:
            series[sid].setdefault(ym, cur[sid]); series[sid][ym] = cur[sid]
            series[sid][ym_add(ym, -1)] = prev[sid]
    if not parsed:
        print(f"ISM: 새 보도자료 없음 (마지막 {last})"); return
    # 2022년 이전 헤드라인은 OECD 경유(FRED BSCICP02USM460S → 50 + 값/2, 2025년 ISM 공표치와 소수 1자리 일치)로 잇는다
    oecd = load(DATA, "BSCICP02USM460S")
    first = min(series["ISMM_PMI"])
    for ym, v in oecd.items():
        if ym < first:
            series["ISMM_PMI"][ym] = round(50 + v / 2, 1)
    for sid, d in series.items():
        save(out, sid, d)
    print(f"ISM: {len(parsed)}건 반영 — 마지막 {max(series['ISMM_PMI'])} PMI {series['ISMM_PMI'][max(series['ISMM_PMI'])]}"
          f" (하위지수 이력 {min(series['ISMM_NEWORD'])}~)")


# ── 컨퍼런스보드 소비자신뢰 ──────────────────────────────────────────────
CB_SHARES = [  # (ID, 문장에 들어 있는 구절) — 보도자료 끝의 'Present Situation / Expectations Six Months Hence' 목록
    ("CB_BIZ_GOOD", r"business conditions (?:were|are) [\"']?good"), ("CB_BIZ_BAD", r"business conditions (?:were|are) [\"']?bad"),
    ("CB_JOBS_PLENTY", r"jobs (?:were|are) [\"']?plentiful"), ("CB_JOBS_HARD", r"jobs (?:were|are) [\"']?hard to get"),
    ("CB_EXP_BIZ_UP", r"business conditions to improve"), ("CB_EXP_BIZ_DOWN", r"business conditions to worsen"),
    ("CB_EXP_JOBS_MORE", r"more jobs"), ("CB_EXP_JOBS_FEWER", r"fewer jobs"),
    ("CB_EXP_INC_UP", r"incomes? to increase"), ("CB_EXP_INC_DOWN", r"incomes? to (?:decline|decrease)"),
]
DOWN_WORDS = r"declin|fell|fall|decreas|dip|drop|slip|eas|retreat|weaken|deteriorat|plung|tumbl|soften|down"
NUM = r"(\d{2,3}(?:\.\d)?)"


def cb_index_sentence(body, name):
    """'The Present Situation Index—…—increased by 8.6 points to 120.4' → (현재, 전월후보[부호 2개])"""
    i = body.find(name)
    if i < 0: return None
    s = body[i:i + 600]
    s = s[:s.find(". ", s.find(" to ") if " to " in s else 0) + 1] if ". " in s else s
    m = re.search(r"\bto " + NUM + r"(?:\s*\(1985=100\))?(?: in \w+)?,? (?:up |down )?from " + NUM, s)
    if m: return float(m.group(1)), [float(m.group(2))]
    m = re.search(r"(?:by )?" + r"(\d+(?:\.\d)?)" + r" points?(?: in \w+)? to " + NUM, s)
    if m:
        ch, v = float(m.group(1)), float(m.group(2))
        return v, [round(v - ch, 1), round(v + ch, 1)]
    m = re.search(r"(?:unchanged|steady|flat)[^.]*?at " + NUM, s) or re.search(r"(?:was|remained|stood) at " + NUM, s)
    if m: return float(m.group(1)), [float(m.group(1))]
    m = re.search(r"\bto " + NUM + r"\b", s)          # 'also improved in August to 82.5.' — 전월 정보 없음
    if m: return float(m.group(1)), []
    return None


def cb_parse(h):
    t = page_text(h)
    body = release_body(t, r"Consumer Confidence Index® ")
    if not body: raise ValueError("본문 없음")
    flat = re.sub(r"\s+", " ", body)
    # 첫 문장 형식이 달마다 다르다: 'rose by 7.5 points to 91.7 (1985=100) in October, up from 84.2 in September' /
    # 'fell in September to 98.7 (1985=100), from an upwardly revised 105.6' / 'declined by 7.0 points in February to 98.3 (1985=100).'
    m = re.search(r"Consumer Confidence Index® (.{0,120}?)" + NUM + r" \(1985=100\)(.{0,160})", flat)
    if not m: raise ValueError("총지수 문장을 못 읽음: " + flat[:200])
    lead, tail = m.group(1), m.group(3).split(". ")[0]
    rel = re.search(r"(\w{3,9})\.? (\d{1,2}), (\d{4}) /PRNewswire/", flat)
    mon = MONTHS.get(next((k for k in MONTHS if rel and k.startswith(rel.group(1).lower()[:3])), ""), None)
    if not mon or not rel: raise ValueError("발표일을 못 읽음")
    ym = f"{rel.group(3)}-{mon:02d}"     # 매월 마지막 화요일 발표 = 그 달 데이터
    cur, prev = {"CB_CCI": float(m.group(2))}, {}
    pm = re.search(r"from (?:an? (?:upwardly |downwardly )?revised )?" + NUM, tail)
    ch = re.search(r"(\d{1,2}\.\d) points?", lead + " " + tail)
    if pm:
        prev["CB_CCI"] = float(pm.group(1))
    elif ch:
        down = re.search(DOWN_WORDS, lead)
        prev["CB_CCI"] = round(cur["CB_CCI"] + (1 if down else -1) * float(ch.group(1)), 1)
    psi, ei = cb_index_sentence(flat, "Present Situation Index"), cb_index_sentence(flat, "Expectations Index")
    if not psi or not ei: raise ValueError("현재상황·기대지수 문장을 못 읽음")
    cur["CB_PSI"], cur["CB_EI"] = psi[0], ei[0]
    if abs(0.4 * psi[0] + 0.6 * ei[0] - cur["CB_CCI"]) > 0.15:
        raise ValueError(f"검산 실패 — 0.4×{psi[0]}+0.6×{ei[0]} ≠ {cur['CB_CCI']}")
    if "CB_CCI" in prev and psi[1] and ei[1]:   # 전월 수정치: 부호 후보 중 총지수 항등식을 만족하는 조합
        best = min(((p, e) for p in psi[1] for e in ei[1]), key=lambda pe: abs(0.4 * pe[0] + 0.6 * pe[1] - prev["CB_CCI"]))
        if abs(0.4 * best[0] + 0.6 * best[1] - prev["CB_CCI"]) <= 0.15:
            prev["CB_PSI"], prev["CB_EI"] = best
    if "CB_CCI" in prev and "CB_PSI" not in prev:   # 한쪽만 전월값이 있으면 항등식으로 나머지를 푼다 (2024-08: 'EI for July was revised up to 81.1'과 일치)
        if len(psi[1]) == 1 and not ei[1]:
            prev["CB_PSI"] = psi[1][0]; prev["CB_EI"] = round((prev["CB_CCI"] - 0.4 * psi[1][0]) / 0.6, 1)
        elif len(ei[1]) == 1 and not psi[1]:
            prev["CB_EI"] = ei[1][0]; prev["CB_PSI"] = round((prev["CB_CCI"] - 0.6 * ei[1][0]) / 0.4, 1)
    # 응답 비율 목록 — 목록을 안 싣는 달도 있다(2025-12, 2026-08). 그 달은 다음 달 보도자료의 'from Y%'(전월 수정치)로 채워진다
    sents = [x.strip() for x in body.split("\n")]
    for sid, phrase in CB_SHARES:
        # 'Conversely, 22.5% of consumers said…' / 'However, 25.6% anticipated…' 처럼 접속어가 앞에 붙는 달도 있다
        lead_pct = r"(?:[A-Z][a-z]+, )?(\d{1,2}\.\d)%"
        sent = next((x for x in sents if re.match(lead_pct, x) and re.search(phrase, x)), None)
        if not sent: continue
        cur[sid] = float(re.match(lead_pct, sent).group(1))
        pm = re.search(r"from (\d{1,2}\.\d)%", sent)
        if pm: prev[sid] = float(pm.group(1))
        elif re.search(r"unchanged", sent): prev[sid] = cur[sid]
    for d in (cur, prev):
        if "CB_JOBS_PLENTY" in d and "CB_JOBS_HARD" in d:
            d["CB_LABOR_DIFF"] = round(d["CB_JOBS_PLENTY"] - d["CB_JOBS_HARD"], 1)
    if "CB_LABOR_DIFF" not in cur:   # 목록이 없으면 본문의 '노동시장 격차 … by 4.8 ppts to +7.5%'
        lm = re.search(r"labor market differential.{0,260}?(\d+(?:\.\d)?) ppts?,? to ([+-]?\d+(?:\.\d)?)%", flat)
        if lm:
            cur["CB_LABOR_DIFF"] = float(lm.group(2))
            down = re.search(DOWN_WORDS, flat[lm.start() + 150:lm.end()])
            prev.setdefault("CB_LABOR_DIFF", round(float(lm.group(2)) + (1 if down else -1) * float(lm.group(1)), 1))
    im = re.search(r"12-month inflation expectations.{0,140}?(?:to|at|of|was|were) (?:just )?(\d{1,2}\.\d)%", flat)
    if im: cur["CB_INFL12"] = float(im.group(1))
    return ym, cur, prev, body


CB_IDS = ["CB_CCI", "CB_PSI", "CB_EI", "CB_LABOR_DIFF", "CB_INFL12"] + [s for s, _ in CB_SHARES]


def cb(out, backfill):
    links = prn_links("the-conference-board", r"/us-consumer-confidence-", 2 if backfill else 1)
    series = {sid: load(out, sid) for sid in CB_IDS}
    last = max(series["CB_CCI"]) if series["CB_CCI"] else "0000-00"
    parsed = []
    for l in links:
        h = get(PRN + l); time.sleep(0.5 if backfill else 0)
        try:
            ym, cur, prev, body = cb_parse(h)
        except ValueError as e:
            print("  ✗ CB", l.split("/")[-1], e); continue
        if not backfill and ym < last: break        # 목록은 최신순 — 이미 가진 달보다 옛것이면 그만
        parsed.append((ym, cur, prev))
        cache_text("cb", ym, PRN + l + "\n\n" + body)
        if not backfill: break                       # 평소엔 최신 1건(이번 달 + 전월 수정치)만
    if not parsed:
        print(f"CB: 보도자료 못 읽음 (마지막 {last})"); return
    for ym, cur, prev in sorted(parsed):
        for sid, v in cur.items(): series[sid][ym] = v
        for sid, v in prev.items(): series[sid][ym_add(ym, -1)] = v
    for sid, d in series.items():
        if d: save(out, sid, d)
    lm = max(series["CB_CCI"])
    print(f"CB: {len(parsed)}건 반영 — 마지막 {lm} 총지수 {series['CB_CCI'][lm]} (이력 {min(series['CB_CCI'])}~)")


# ── 리치먼드 연은 제조업 ────────────────────────────────────────────────
RICH_URL = "https://www.richmondfed.org/-/media/RichmondFedOrg/region_communities/regional_data_analysis/regional_economy/surveys_of_business_conditions/manufacturing/data/mfg_historicaldata.xlsx"
RICH_PAGE = "https://www.richmondfed.org/region_communities/regional_data_analysis/business_surveys/manufacturing"
RICH_COLS = [("sa_mfg_composite", "RICH_COMP"), ("sa_mfg_ship_c", "RICH_SHIP"), ("sa_mfg_new_orders_c", "RICH_NEWORD"),
             ("sa_mfg_emp_c", "RICH_EMP"), ("sa_mfg_bk_logs_c", "RICH_BACKLOG"), ("sa_mfg_cap_util_c", "RICH_CAPUTIL"),
             ("sa_mfg_vend_lead_c", "RICH_VENDLEAD"), ("sa_mfg_wage_c", "RICH_WAGE"), ("sa_mfg_fd_gds_inv_c", "RICH_FGINV"),
             ("sa_mfg_raw_mats_inv_c", "RICH_RAWINV"), ("sa_mfg_local_bus_cond_c", "RICH_LOCAL"),
             ("nsa_mfg_pct_chg_prcs_pd_c", "RICH_PRICE_PAID"), ("nsa_mfg_pct_chg_prcs_recd_c", "RICH_PRICE_RECD"),
             ("sa_mfg_ship_e", "RICH_E_SHIP"), ("sa_mfg_new_orders_e", "RICH_E_NEWORD"), ("sa_mfg_emp_e", "RICH_E_EMP"),
             ("sa_mfg_capital_expnd_e", "RICH_E_CAPEX"), ("sa_mfg_local_bus_cond_e", "RICH_E_LOCAL"),
             ("nsa_mfg_pct_chg_prcs_pd_e", "RICH_E_PRICE_PAID"), ("nsa_mfg_pct_chg_prcs_recd_e", "RICH_E_PRICE_RECD")]


def xlsx_rows(b, sheet=None):
    """표준 라이브러리만으로 시트를 읽는다 (openpyxl 없는 환경 대비). sheet = 시트 이름(없으면 첫 시트)."""
    z = zipfile.ZipFile(io.BytesIO(b)); ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", ns):
            shared.append("".join(x.text or "" for x in si.iter("{%s}t" % ns["m"])))
    path = "xl/worksheets/sheet1.xml"
    if sheet:   # 시트 이름 → 관계 id → 파일 경로
        rid_attr = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
        sh = next((s for s in ET.fromstring(z.read("xl/workbook.xml")).iter("{%s}sheet" % ns["m"]) if s.get("name") == sheet), None)
        if sh is None:
            raise ValueError(f"시트 '{sheet}' 없음")
        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        target = next(r.get("Target") for r in rels if r.get("Id") == sh.get(rid_attr))
        path = "xl/" + target.lstrip("/").replace("xl/", "", 1)
    rows = []
    for r in ET.fromstring(z.read(path)).iter("{%s}row" % ns["m"]):
        cells = {}
        for c in r.findall("m:c", ns):
            col = re.match(r"[A-Z]+", c.get("r")).group(0)
            idx = 0
            for ch in col: idx = idx * 26 + ord(ch) - 64
            v = c.find("m:v", ns)
            val = None if v is None else v.text
            if c.get("t") == "s" and val is not None: val = shared[int(val)]
            cells[idx - 1] = val
        rows.append([cells.get(i) for i in range(max(cells) + 1)] if cells else [])
    return rows


def richmond(out, backfill):
    rows = xlsx_rows(get(RICH_URL, binary=True))
    head = rows[0]
    col = {name: head.index(name) for name, _ in RICH_COLS if name in head}
    miss = [n for n, _ in RICH_COLS if n not in col]
    if miss: raise SystemExit(f"리치먼드 엑셀 열 이름이 바뀜: {miss} — fetch_surveys.py RICH_COLS 점검")
    series = {sid: {} for _, sid in RICH_COLS}
    for r in rows[1:]:
        if not r or r[0] in (None, ""): continue
        try:
            dt = date(1899, 12, 30) + timedelta(days=float(r[0]))
        except ValueError:
            continue
        ym = f"{dt.year:04d}-{dt.month:02d}"
        for name, sid in RICH_COLS:
            v = r[col[name]] if col[name] < len(r) else None
            try:
                series[sid][ym] = round(float(v), 2)
            except (TypeError, ValueError):
                pass
    lm = max(series["RICH_COMP"])
    for ym in [lm, ym_add(lm, -1)]:
        s = series
        calc = 0.33 * s["RICH_SHIP"][ym] + 0.40 * s["RICH_NEWORD"][ym] + 0.27 * s["RICH_EMP"][ym]
        if abs(calc - s["RICH_COMP"][ym]) > 1.0:
            raise SystemExit(f"리치먼드 {ym}: 검산 실패 — 가중합 {calc:.2f} vs 종합 {s['RICH_COMP'][ym]}")
    prev_last = max(load(out, "RICH_COMP") or {"0000-00": 0})
    for sid, d in series.items():
        save(out, sid, d)
    try:   # 발표 요약 문단(보도 페이지 본문) — 숫자는 엑셀에서, 서술은 여기서
        pt = page_text(get(RICH_PAGE))
        body = release_body(pt, r"Fifth District manufacturing activity", ("Manufacturing Survey Archive", "Release Schedule", "Contact Us"))
        dm = re.search(r"Manufacturing Survey\s+(\w+ \d{1,2}, \d{4})", pt)
        if body: cache_text("richmond", lm, RICH_PAGE + "\n발표일: " + (dm.group(1) if dm else "미확인") + "\n\n" + body[:6000])
    except Exception as e:
        print("  (요약 문단 저장 실패:", e, ")")
    print(f"RICHMOND: 마지막 {lm} 종합 {series['RICH_COMP'][lm]:g}" + (" — 새 달" if lm > prev_last else " (변화 없음)"))


# ── 캐스 화물지수 ────────────────────────────────────────────────────────
# 2026-10-30 편입: 사용자 "화물운송량 — 매달 나오고 PMI랑 상관 짙은 것, 3달 뒤 후행이면 안 봄".
# 캐스 출하량 YoY ~ ISM PMI 같은 달 상관 0.74(2017~, 코로나 제외 0.75) — BTS 화물지수 0.56·트럭 톤수 0.46보다 높고 두 달 덜 늦다.
CASS_XLSX = "https://www.cassinfo.com/hubfs/Cass%20Indexes%20Historical%20Data.xlsx"
CASS_PAGE = "https://www.cassinfo.com/freight-audit-payment/cass-transportation-indexes/"
CASS_SHEETS = [  # (시트, 원계열 ID, 계절조정 ID) — 엑셀 열: 월 / 지수 / 전년비 / 2년 누적 / 전월비 / (빈칸) / 계절조정 / SA 전월비
    ("Freight Index-Shipments", "CASS_SHIP", "CASS_SHIP_SA"),
    ("Freight Index-Expenditures", "CASS_EXP", "CASS_EXP_SA"),
    ("TL LH Index", "CASS_TLLH", None),   # 트럭 운임(Truckload Linehaul) — 원래 계절조정된 값
]


def cass(out, backfill):
    b = get(CASS_XLSX, binary=True)
    series, check = {}, {}
    for sheet, sid, sa_id in CASS_SHEETS:
        rows = xlsx_rows(b, sheet)
        hi = next(i for i, r in enumerate(rows) if r and len(r) > 2 and r[1] == "Month")
        head = rows[hi]
        sa_col = next((i for i, h in enumerate(head) if h == "Seasonally Adjusted"), None)
        d, dsa = {}, {}
        for r in rows[hi + 1:]:
            if len(r) < 3 or r[1] in (None, "") or r[2] in (None, ""):
                continue
            dt = date(1899, 12, 30) + timedelta(days=float(r[1]))
            ym = f"{dt.year:04d}-{dt.month:02d}"
            d[ym] = round(float(r[2]), 5)
            if sa_col is not None and sa_col < len(r) and r[sa_col] not in (None, ""):
                dsa[ym] = round(float(r[sa_col]), 5)
            if len(r) > 3 and r[3] not in (None, ""):
                check[(sid, ym)] = float(r[3])        # 엑셀의 전년비 — 검산용
        series[sid] = d
        if sa_id:
            series[sa_id] = dsa
    lm = max(series["CASS_SHIP"])
    for sid in ("CASS_SHIP", "CASS_EXP"):   # 검산: 원계열로 계산한 전년비 = 엑셀의 전년비
        d, prev = series[sid], ym_add(lm, -12)
        if prev in d and (sid, lm) in check and abs((d[lm] / d[prev] - 1) - check[(sid, lm)]) > 0.002:
            raise SystemExit(f"캐스 {sid} {lm}: 검산 실패 — 계산 {d[lm] / d[prev] - 1:.4f} vs 엑셀 {check[(sid, lm)]:.4f}")
    prev_last = max(load(out, "CASS_SHIP") or {"0000-00": 0})
    for sid, d in series.items():
        save(out, sid, d)
    try:   # 그 달 보고서 페이지(해설·계절조정 전월비 표)
        mon = [k for k, v in MONTHS.items() if v == int(lm[5:])][0]
        t = page_text(get(f"{CASS_PAGE}{mon}-{lm[:4]}"))
        i = t.find("Year-over-year change")
        j = min([k for k in (t.find("ACT Freight Expectations", i), t.find("Need more freight market", i)) if k > 0] or [i + 6000])
        if i > 0: cache_text("cass", lm, f"{CASS_PAGE}{mon}-{lm[:4]}\n\n" + t[max(0, i - 200):j])
    except Exception as e:
        print("  (보고서 페이지 저장 실패:", e, ")")
    print(f"CASS: 마지막 {lm} 출하 {series['CASS_SHIP'][lm]:g} (계절조정 {series['CASS_SHIP_SA'].get(lm, 0):g})"
          + (" — 새 달" if lm > prev_last else " (변화 없음)"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["ism", "cb", "richmond", "cass", "all"])
    ap.add_argument("--backfill", action="store_true", help="과거 보도자료로 이력 채우기 (ISM·CB)")
    ap.add_argument("--out", default=DATA, help="저장 폴더 (기본 reports/data)")
    a = ap.parse_args()
    jobs = ["ism", "cb", "richmond", "cass"] if a.what == "all" else [a.what]
    bad = 0
    for j in jobs:
        try:
            {"ism": ism, "cb": cb, "richmond": richmond, "cass": cass}[j](a.out, a.backfill)
        except (Exception, SystemExit) as e:
            bad += 1; print(f"✗ {j.upper()} 수집 실패: {e}")
    sys.exit(1 if bad == len(jobs) else 0)


if __name__ == "__main__":
    main()

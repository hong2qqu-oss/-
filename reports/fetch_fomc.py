# -*- coding: utf-8 -*-
"""
FOMC 문서 수집기 — 성명문·이행 노트·의장 기자회견 녹취록·의사록·경제전망(SEP)을 연준 사이트에서 받아
.claude/src_cache/fomc/ 에 텍스트로 남기고, 직전 성명문과의 단어 단위 대조(diff)를 만든다.
2026-10-28 사용자 요청("FOMC도 — 성명문이랑 회의 전문이랑 의의")으로 보고서 편입.
연준 문서는 미국 정부 저작물이라 전문 번역·게재가 가능하다.

  python reports/fetch_fomc.py check                 # state.json 의 FOMC·FOMC_MINUTES 보다 새 회의·의사록이 있는지
  python reports/fetch_fomc.py get 2026-10-28        # 그 회의의 성명문·이행 노트·기자회견·SEP + 직전 성명문 대조
  python reports/fetch_fomc.py get-minutes 2026-09-16  # 그 회의의 의사록 (회의 약 3주 뒤 공개)

  python reports/fetch_fomc.py get-jh 2026-08-28     # 잭슨홀 심포지엄 의장 연설 (매년 8월 말, 캔자스시티 연은 주최)

check 출력:
  NEW_STATEMENT 2026-10-28 presconf=full-preliminary|opening-preliminary|missing days=N
  NEW_MINUTES 2026-09-16 released=2026-10-07
  NEW_JACKSONHOLE 2026-08-28 (연사·제목)
  NO_FOMC_NEW
기자회견 녹취록은 회견 직후엔 모두발언만 담긴 PDF가 먼저 올라오고, 몇 시간~하루 뒤 같은 주소가 문답까지 담긴
완성본으로 바뀐다. 연준은 완성본에도 몇 주씩 'PRELIMINARY' 표시를 붙여 두므로 표시가 아니라 **문답이 들어 있나
(presconf=full-…)**로 판단한다 — full 이면 바로 쓴다. 회의 3일이 지나도 opening 뿐이면 있는 것으로 쓰고 그 사실을 적는다.
"""
import argparse, difflib, io, json, os, re, subprocess, sys
from datetime import date, datetime, timedelta, timezone

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", ".claude", "src_cache", "fomc")
FED = "https://www.federalreserve.gov"
CAL = FED + "/monetarypolicy/fomccalendars.htm"
MONTHS = {m: i + 1 for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july",
                                          "august", "september", "october", "november", "december"])}

sys.path.insert(0, HERE)
from fetch_surveys import page_text   # 같은 HTML→텍스트 정리 (따옴표·대시 통일)


def get(url, binary=False):
    """curl 로 받는다 (다른 수집기와 같은 방식). 없으면 None."""
    r = subprocess.run(["curl", "-sSL", "-m", "60", "-w", "\n%{http_code}", url], capture_output=True)
    if r.returncode != 0:
        raise ValueError(f"받기 실패 ({r.returncode}) {url}")
    body, code = r.stdout.rsplit(b"\n", 1)
    if code.strip() != b"200":
        return None
    return body if binary else body.decode("utf-8", "replace")


def ymd(d8):
    return f"{d8[:4]}-{d8[4:6]}-{d8[6:]}"


def save(name, text):
    os.makedirs(SRC, exist_ok=True)
    p = os.path.join(SRC, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


# ── 일정 ────────────────────────────────────────────────────────────────
def calendar():
    """(성명문 날짜 목록, {회의 날짜: 의사록 공개일}) — 연준 일정 페이지의 링크로 읽는다(일정이 바뀌어도 링크는 남는다)."""
    h = get(CAL)
    stmts = sorted(set(ymd(d) for d in re.findall(r"/newsevents/pressreleases/monetary(\d{8})a\.htm", h)))
    minutes = {}
    for m in re.finditer(r"fomcminutes(\d{8})\.htm", h):
        rel = re.search(r"Released (\w+) (\d{1,2}), (\d{4})", h[m.end():m.end() + 800])
        if rel and rel.group(1).lower() in MONTHS:
            minutes[ymd(m.group(1))] = f"{rel.group(3)}-{MONTHS[rel.group(1).lower()]:02d}-{int(rel.group(2)):02d}"
    return stmts, minutes


def upcoming(after):
    """일정 페이지 본문의 'December 8-9*' 같은 줄에서 아직 성명문이 없는 다음 회의 날짜(둘째 날)를 찾는다."""
    t = re.sub(r"\s+", " ", page_text(get(CAL)))
    out = []
    for blk in re.finditer(r"(\d{4}) FOMC Meetings(.*?)(?=\d{4} FOMC Meetings|$)", t):
        y = int(blk.group(1))
        for m in re.finditer(r"\b(" + "|".join(k.capitalize() for k in MONTHS) + r")(?:/(\w+))? (\d{1,2})(?:-(\d{1,2}))?(\*)?", blk.group(2)):
            end_mon = m.group(2) if m.group(2) and m.group(2).lower() in MONTHS else m.group(1)   # 'April/May 30-1' 은 둘째 달
            mon = MONTHS[end_mon.lower()]
            d = date(y, mon, int(m.group(4) or m.group(3))).isoformat()
            if d > after:
                out.append(d + (" (경제전망 포함)" if m.group(5) else ""))
    return sorted(set(out))[0] if out else "일정 미공개"


def today_et():
    # 미국 동부 날짜 (UTC-4로 고정 — 하루 단위 판정엔 충분)
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date()


# ── 성명문 ──────────────────────────────────────────────────────────────
def statement_text(d):
    h = get(f"{FED}/newsevents/pressreleases/monetary{d.replace('-', '')}a.htm")
    if h is None:
        return None
    t = page_text(h)
    i = re.search(r"For release at [^\n]*\n", t)
    j = t.find("For media inquiries")
    if not i or j < 0:
        raise ValueError(f"{d}: 성명문 경계를 못 찾음")
    lines = [l.strip() for l in t[i.end():j].split("\n")]
    keep = [l for l in lines if l and l not in ("Share", "-->") and not l.startswith("-->")]
    return "\n\n".join(keep)


def impl_note(d):
    h = get(f"{FED}/newsevents/pressreleases/monetary{d.replace('-', '')}a1.htm")
    if h is None:
        return None
    t = page_text(h)
    i = t.find("Decisions Regarding Monetary Policy Implementation")
    j = min([k for k in (t.find("For media inquiries", i), t.find("This information will be updated", i)) if k > 0] or [len(t)])
    return t[i:j].strip() if i >= 0 else None


def word_diff(old, new):
    """단어 단위 대조 → 마크다운: 지운 말 ~~…~~, 새 말 ++…++ (사이트 렌더러가 취소선·밑줄로 그린다)"""
    tok = lambda s: re.findall(r"\n\n|[^\s]+", s)
    a, b = tok(old), tok(new)

    def mark(tokens, m):   # 문단 경계(\n\n)는 표시 밖에 그대로 둔다 — 새 문단이 앞 문단에 붙지 않게
        res = []
        for k, c in enumerate(" ".join(tokens).split("\n\n")):
            if k: res.append("\n\n")
            if c.strip(): res.append(f"{m}{c.strip()}{m}")
        return res

    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "equal":
            out.append(" ".join(a[i1:i2]))
        if op in ("delete", "replace"):
            out += mark(a[i1:i2], "~~")
        if op in ("insert", "replace"):
            out += mark(b[j1:j2], "++")
    s = " ".join(out)
    return re.sub(r" ?\n\n ?", "\n\n", s)


# ── 기자회견 ─────────────────────────────────────────────────────────────
def presconf(d):
    b = get(f"{FED}/mediacenter/files/FOMCpresconf{d.replace('-', '')}.pdf", binary=True)
    if b is None:
        return "missing", None
    try:
        import pypdf
    except ImportError:
        return "no-pypdf", None
    r = pypdf.PdfReader(io.BytesIO(b))
    pages = [p.extract_text() or "" for p in r.pages]
    # 상태는 '문답이 들어 있나'로 판단한다. 연준은 문답까지 다 담긴 완성본에도 몇 주씩 'PRELIMINARY' 표시를 붙여 둔다
    # (2026-10-28 회견: 10/29 15:22 GMT에 17쪽 문답 포함본으로 바뀌었는데 표시는 그대로 → 표시만 보고 나흘을 기다린 사고, 11/01 사용자 질책)
    text = "\n".join(pages)
    speakers = set(re.findall(r"\n([A-Z][A-Z.'\- ]{3,40})\.\s", text)) - {"CHAIRMAN WARSH", "CHAIR WARSH"}
    speakers = {s for s in speakers if not s.startswith("CHAIR")}
    label = "final" if "FINAL" in pages[0][:300] else ("preliminary" if "PRELIMINARY" in pages[0][:300] else "unknown")
    status = ("full" if len(speakers) >= 2 else "opening") + "-" + label   # full-preliminary = 문답 포함(연준 표시는 예비)
    keep = []
    for p in pages:
        for l in p.split("\n"):
            if re.match(r"\s*Page \d+ of \d+\s*$", l) or re.search(r"Press Conference (FINAL|PRELIMINARY)\s*$", l):
                continue
            keep.append(l.rstrip())
    return status, "\n".join(keep).strip()


def sep(d):
    h = get(f"{FED}/monetarypolicy/fomcprojtabl{d.replace('-', '')}.htm")
    if h is None:
        return None
    t = page_text(h)
    i = t.find("Summary of Economic Projections")
    j = t.find("Last Update", i)
    return t[i:j].strip() if i >= 0 else None


# ── 의사록 ──────────────────────────────────────────────────────────────
def minutes_text(d):
    h = get(f"{FED}/monetarypolicy/fomcminutes{d.replace('-', '')}.htm")
    if h is None:
        return None
    t = page_text(h)
    i = t.find("Minutes of the Federal Open Market Committee")
    j = t.find("Back to Top", i)
    return t[i:j if j > 0 else len(t)].strip()


# ── 잭슨홀 ──────────────────────────────────────────────────────────────
# 2026-11-01 사용자 요청("잭슨홀도 추가해"). 연준 연설 목록 JSON에서 장소에 'Jackson Hole'이 들어간 의장 연설을 찾는다.
SPEECHES = FED + "/json/ne-speeches.json"


def jackson_hole():
    """[(날짜 YYYY-MM-DD, 연사, 제목, 링크)] — 의장(Chair/Chairman) 연설만, 최신순"""
    d = json.loads(get(SPEECHES).lstrip("﻿"))
    out = []
    for x in d:
        if "Jackson Hole" in x.get("lo", "") and re.search(r"\bChair(man)?\b", x.get("s", "")) and "Vice" not in x.get("s", ""):
            m, dd, y = x["d"].split()[0].split("/")
            out.append((f"{y}-{int(m):02d}-{int(dd):02d}", x["s"], html_unescape(x["t"]), x["l"]))
    return sorted(out, reverse=True)


def html_unescape(s):
    import html as _h
    return _h.unescape(_h.unescape(s))


def cmd_get_jh(d):
    hit = next((x for x in jackson_hole() if x[0] == d), None)
    if not hit:
        raise SystemExit(f"{d}: 잭슨홀 의장 연설 없음")
    t = page_text(get(FED + hit[3]))
    i = t.find(hit[2].split(":")[0][:30]) if hit[2] else -1
    i = t.find("\n", t.find(hit[1].split()[-1], max(i, 0))) if i >= 0 else 0
    j = min([k for k in (t.find("Last Update", i), t.find("Back to Top", i)) if k > 0] or [len(t)])
    body = t[i:j].strip()
    p = save(f"{d}_jacksonhole.txt", f"{FED}{hit[3]}\n{hit[1]} — {hit[2]} ({d})\n\n{body}")
    print(f"잭슨홀 {d} — {hit[1]} \"{hit[2]}\" — 본문 {len(body.split())}단어")
    print("  저장:", os.path.relpath(p, os.path.join(HERE, "..")))


# ── 명령 ────────────────────────────────────────────────────────────────
def cmd_check():
    st = json.load(open(os.path.join(HERE, "state.json"), encoding="utf-8-sig"))
    stmts, mins = calendar()
    today = today_et().isoformat()
    found = False
    for d in stmts:
        if d > st.get("FOMC", "0000") and d <= today and statement_text(d) is not None:
            status, _ = presconf(d)
            days = (date.fromisoformat(today) - date.fromisoformat(d)).days
            print(f"NEW_STATEMENT {d} presconf={status} days={days}")
            found = True
    for d, rel in sorted(mins.items()):
        if d > st.get("FOMC_MINUTES", "0000") and rel <= today:
            print(f"NEW_MINUTES {d} released={rel}")
            found = True
    try:
        jh = jackson_hole()
        if jh and jh[0][0][:4] > st.get("JACKSONHOLE", "0000") and jh[0][0] <= today:
            print(f"NEW_JACKSONHOLE {jh[0][0]} ({jh[0][1]} — {jh[0][2]})")
            found = True
    except Exception as e:
        print(f"(잭슨홀 확인 실패: {e})")
    if not found:
        print(f"NO_FOMC_NEW (마지막 성명 {st.get('FOMC')}, 마지막 의사록 {st.get('FOMC_MINUTES')})")
    print(f"다음 회의(둘째 날 = 성명 발표일): {upcoming(today)}")


def cmd_get(d):
    stmts, _ = calendar()
    cur = statement_text(d)
    if cur is None:
        raise SystemExit(f"{d}: 성명문 없음")
    prev_d = max((x for x in stmts if x < d), default=None)
    prev = statement_text(prev_d) if prev_d else None
    files = [save(f"{d}_statement.txt", cur)]
    if prev:
        files.append(save(f"{d}_prev_statement_{prev_d}.txt", prev))
        files.append(save(f"{d}_diff.md", f"직전 성명문({prev_d}) 대비 — ~~지운 말~~ ++새 말++\n\n" + word_diff(prev, cur)))
    note = impl_note(d)
    if note: files.append(save(f"{d}_implementation_note.txt", note))
    status, pc = presconf(d)
    if pc: files.append(save(f"{d}_presconf_{status}.txt", pc))
    sp = sep(d)
    if sp: files.append(save(f"{d}_sep.txt", sp))
    rng = re.search(r"target range for the federal funds rate (?:at|to) ([\d\-/ ]+?) (?:to|and) ([\d\-/ ]+?) percent", cur)
    vote = re.search(r"by an? (\d+) ?- ?(\d+) vote", cur) or re.search(r"(unanimous)", cur, re.I)
    print(f"성명문 {d} (직전 {prev_d}) — 금리 범위: {rng.group(1)}~{rng.group(2) if rng else ''}%" if rng else f"성명문 {d} (직전 {prev_d})")
    print(f"표결: {'-'.join(vote.groups()) if vote else '문구 확인 필요'} / 기자회견 녹취록: {status}"
          + (f" ({len(pc.split())}단어)" if pc else "") + f" / 경제전망(SEP): {'있음' if sp else '없음(SEP 없는 회의)'}")
    for f in files:
        print("  저장:", os.path.relpath(f, os.path.join(HERE, "..")))


def cmd_get_minutes(d):
    _, mins = calendar()
    t = minutes_text(d)
    if t is None:
        raise SystemExit(f"{d}: 의사록 없음 (아직 공개 전일 수 있다)")
    p = save(f"{d}_minutes.txt", t)
    body = t.split("\nAttendance")[0]
    heads = [l.strip() for l in body.split("\n") if 3 < len(l.strip()) < 90 and not re.search(r"[.:;,\"]$", l.strip())]
    print(f"의사록 {d} (공개 {mins.get(d, '미확인')}) — 본문 {len(body.split())}단어, 참석자 명단 제외")
    print("  절:", " / ".join(heads[:20]))
    print("  저장:", os.path.relpath(p, os.path.join(HERE, "..")))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["check", "get", "get-minutes", "get-jh"])
    ap.add_argument("date", nargs="?")
    a = ap.parse_args()
    if a.cmd == "check":
        cmd_check()
    elif not a.date:
        ap.error("날짜(YYYY-MM-DD) 필요")
    elif a.cmd == "get":
        cmd_get(a.date)
    elif a.cmd == "get-jh":
        cmd_get_jh(a.date)
    else:
        cmd_get_minutes(a.date)


if __name__ == "__main__":
    main()

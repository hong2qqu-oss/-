# -*- coding: utf-8 -*-
"""
일본은행(BoJ)·한국은행(BoK) 금리 결정 문서 수집기 — 2026-11-01 사용자 요청("BoJ랑 한국 금리결정도 부탁").
FOMC(fetch_fomc.py)와 같은 구성: 결정문 + 총재 발언(회견) + 의의, 회의 뒤 문서는 따로.
  BoJ: 결정문(공표문, 일·영) · 전망 보고서 기본적 견해(1·4·7·10월) · 총재 정례 기자회견 녹취(회의 1~6일 뒤, 일본어) · 주요 의견(약 10일 뒤)
  BoK: 통화정책방향(보도자료) · 총재 기자간담회 모두발언 · 참고자료 · 금통위 의사록(약 2주 뒤). 문답은 한은이 글로 공개하지 않는다(영상만)
       → 사용자 선택: 의사록 보고서로 보강

  python reports/fetch_cb.py check                  # state.json 의 BOJ·BOJ_PC·BOJ_OPINION·BOK·BOK_MINUTES 보다 새 것
  python reports/fetch_cb.py get-boj 2026-10-30     # 결정문(일·영)·전망 기본적 견해·회견 녹취(있으면)·직전 결정문
  python reports/fetch_cb.py get-boj-opinion 2026-09-18
  python reports/fetch_cb.py get-bok 2026-10-22     # 통화정책방향·모두발언·참고자료
  python reports/fetch_cb.py get-bok-minutes 2026-08-27

원문은 .claude/src_cache/cb/ 에 텍스트로 남는다(로컬). 일본은행 회견 녹취는 결정보다 늦게 나오므로
결정 보고서를 먼저 쓰고, 녹취가 올라오면(check 의 BOJ_PC_READY) 같은 보고서에 총재 발언 정리·전문 번역을 보강한다.
"""
import argparse, html, io, json, os, re, subprocess, sys
from datetime import date, datetime, timedelta, timezone

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", ".claude", "src_cache", "cb")
BOJ = "https://www.boj.or.jp"
BOJ_SCHED = BOJ + "/mopo/mpmsche_minu/index.htm"
BOK = "https://www.bok.or.kr"
BOK_LIST = BOK + "/portal/singl/crncyPolicyDrcMtg/listYear.do?mtgSe=A&menuNo=200755"


def get(url, binary=False):
    r = subprocess.run(["curl", "-sSL", "-m", "60", "-w", "\n%{http_code}", url], capture_output=True)
    if r.returncode != 0:
        raise ValueError(f"받기 실패 ({r.returncode}) {url}")
    body, code = r.stdout.rsplit(b"\n", 1)
    if code.strip() != b"200":
        return None
    return body if binary else body.decode("utf-8", "replace")


def pdf_text(b):
    import pypdf
    rd = pypdf.PdfReader(io.BytesIO(b))
    t = "\n".join(p.extract_text() or "" for p in rd.pages)
    hangul = re.findall(r"[가-힣]", t)
    if len(hangul) > 500 and t.count(" ") < len(hangul) * 0.08:
        t = hangul_layout(rd)
    # 일본어 PDF는 글자 사이에 공백이 끼어 나온다('わ が 国') — 일본어·한자 글자 사이 공백만 지운다(한글은 건드리지 않음)
    cjk = r"[　-ヿ㐀-鿿＀-￯]"
    return re.sub(rf"(?<={cjk}) (?={cjk})", "", t)


def hangul_layout(rd):
    """한은 의사록(한컴 PDF)은 기본 추출에서 띄어쓰기가 통째로 빠진다('2026년도제16차금융통화위원회') →
    글자 위치로 공백을 살리는 layout 추출 후, 양쪽 맞춤 공백을 하나로 줄이고 문단 안의 줄을 잇는다(2026-11-01)."""
    paras = []
    for p in rd.pages:
        buf = []
        for line in (p.extract_text(extraction_mode="layout") or "").split("\n"):
            s = re.sub(r"\s{2,}", " ", line).strip()
            if re.fullmatch(r"-\s*\d+\s*-", s):
                continue
            if not s:
                if buf: paras.append(buf); buf = []
                continue
            buf.append(s)
        if buf: paras.append(buf)
    out = []
    for lines in paras:
        txt = lines[0]
        for s in lines[1:]:   # 줄 끝에서 끊긴 단어('한다'+'는견해')는 붙이고, 그 밖엔 띄운다
            txt += ("" if re.search(r"[가-힣0-9]$", txt) and re.match(r"[가-힣]", s) else " ") + s
        out.append(txt)
    return "\n\n".join(out)


def pdf_date(b):
    """PDF 생성일 (한은 의사록엔 공개일이 본문에 없다 — 생성일이 공개일과 같거나 직전, 예: 8/27 회의 의사록 = 9/15 14:49)"""
    import pypdf
    m = re.match(r"D:(\d{4})(\d{2})(\d{2})", str((pypdf.PdfReader(io.BytesIO(b)).metadata or {}).get("/CreationDate", "")))
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def save(name, text):
    os.makedirs(SRC, exist_ok=True)
    p = os.path.join(SRC, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return os.path.relpath(p, os.path.join(HERE, ".."))


def today_kst():
    return (datetime.now(timezone.utc) + timedelta(hours=9)).date().isoformat()


# ── 일본은행 ────────────────────────────────────────────────────────────
def boj_meetings():
    """일정표 한 줄 = 회의 하나: {날짜: {statement, outlook_a, outlook_b, opinion, minutes, presconf}} (경로)"""
    h = get(BOJ_SCHED)
    out = {}
    for row in re.split(r"<tr", h):
        st = re.search(r"/mopo/mpmdeci/mpr_\d{4}/k(\d{6})a\.pdf", row)
        if not st:
            continue
        d = f"20{st.group(1)[:2]}-{st.group(1)[2:4]}-{st.group(1)[4:]}"
        links = re.findall(r'href="([^"]+\.pdf)"', row)
        pick = lambda pat: next((l for l in links if re.search(pat, l)), None)
        out[d] = dict(statement=st.group(0), outlook_a=pick(r"/mopo/outlook/gor\d{4}a\.pdf"), outlook_b=pick(r"/mopo/outlook/gor\d{4}b\.pdf"),
                      opinion=pick(r"/opinion_\d{4}/opi\d{6}\.pdf"), minutes=pick(r"/minu_\d{4}/g\d{6}\.pdf"),
                      presconf=pick(r"/kaiken_\d{4}/kk\d{6}a\.pdf"))
    return out


def cmd_get_boj(d):
    ms = boj_meetings()
    m = ms.get(d)
    if not m:
        raise SystemExit(f"{d}: 일본은행 회의 없음")
    files = []
    ja = get(BOJ + m["statement"], binary=True)
    en = get(BOJ + "/en" + m["statement"], binary=True)
    if ja: files.append(save(f"boj_{d}_statement_ja.txt", pdf_text(ja)))
    if en: files.append(save(f"boj_{d}_statement_en.txt", pdf_text(en)))
    prev = max((x for x in ms if x < d), default=None)
    if prev:
        pe = get(BOJ + "/en" + ms[prev]["statement"], binary=True)
        if pe: files.append(save(f"boj_{d}_prev_statement_{prev}_en.txt", pdf_text(pe)))
    for key in ("outlook_a", "outlook_b"):
        if m[key]:
            b = get(BOJ + m[key], binary=True)
            if b: files.append(save(f"boj_{d}_{key}.txt", pdf_text(b)))
    pc = "pending"
    if m["presconf"]:
        b = get(BOJ + m["presconf"], binary=True)
        if b:
            files.append(save(f"boj_{d}_presconf.txt", BOJ + m["presconf"] + "\n\n" + pdf_text(b)))
            pc = "ready"
    stx = pdf_text(en) if en else ""
    rate = re.search(r"remain at around ([\d.]+)\s*percent|at around ([\d.]+)\s*percent", stx)
    vote = re.search(r"\((an? \d+-\d+ majority vote|a unanimous vote)\)", stx)
    print(f"일본은행 {d} (직전 {prev}) — 정책금리 {(rate.group(1) or rate.group(2)) + '%' if rate else '문구 확인'}"
          f" / 표결 {vote.group(1) if vote else '문구 확인'} / 전망 보고서 {'있음' if m['outlook_a'] or m['outlook_b'] else '없음'}"
          f" / 총재 회견 녹취 {pc}")
    for f in files:
        print("  저장:", f)


def cmd_get_boj_opinion(d):
    m = boj_meetings().get(d)
    if not m or not m["opinion"]:
        raise SystemExit(f"{d}: 주요 의견 아직 없음")
    t = pdf_text(get(BOJ + m["opinion"], binary=True))
    print(f"일본은행 주요 의견 {d} — {len(t)}자")
    print("  저장:", save(f"boj_{d}_opinion.txt", BOJ + m["opinion"] + "\n\n" + t))


# ── 한국은행 ────────────────────────────────────────────────────────────
def bok_meetings():
    """{회의 날짜: {code, press, opening, reference, minutes, minutes_title}} — 같은 첨부 묶음(atchFileId)으로 회의별 짝짓기"""
    h = get(BOK_LIST)
    year = re.search(r"국문보도자료\((\d{2})\d{2}\)", h)
    out = {}
    for m in re.finditer(r'href="([^"]*fileDown\.do[^"]*atchFileId=([0-9a-f]+)[^"]*)"[^>]*>\s*([^<]+?\.pdf)', h):
        url, fid, name = html.unescape(m.group(1)), m.group(2), m.group(3).strip()
        g = out.setdefault(fid, {})
        if "국문보도자료" in name:
            g["press"], g["code"] = url, re.search(r"\((\d{4})\)", name).group(1)
            dm = None
            for dm in re.finditer(r"(\d{1,2})월\s*(\d{1,2})일\s*\([월화수목금토일]\)", h[:m.start()]):
                pass
            if dm:
                g["date"] = f"20{g['code'][:2]}-{int(dm.group(1)):02d}-{int(dm.group(2)):02d}"
        elif "모두발언" in name:
            g["opening"] = url
        elif "참고자료" in name:
            g["reference"] = url
        elif "의사록" in name:
            g["minutes"], g["minutes_title"] = url, name
    return {g["date"]: g for g in out.values() if "date" in g}


def cmd_get_bok(d):
    m = bok_meetings().get(d)
    if not m:
        raise SystemExit(f"{d}: 한은 회의 없음")
    files = []
    for key in ("press", "opening", "reference"):
        if m.get(key):
            b = get(BOK + m[key], binary=True)
            if b: files.append(save(f"bok_{d}_{key}.txt", pdf_text(b)))
    t = open(os.path.join(SRC, f"bok_{d}_press.txt"), encoding="utf-8").read() if m.get("press") else ""
    rate = re.search(r"기준금리를\s*(현\s*수준|[\d.]+%)?[^\n]{0,40}?\(?([\d.]+)%", t)
    print(f"한은 {d} (코드 {m['code']}) — 기준금리 {rate.group(2) + '%' if rate else '문구 확인'} / 모두발언 {'있음' if m.get('opening') else '없음'}"
          f" / 의사록 {'있음' if m.get('minutes') else '아직(약 2주 뒤)'}")
    for f in files:
        print("  저장:", f)


def cmd_get_bok_minutes(d):
    m = bok_meetings().get(d)
    if not m or not m.get("minutes"):
        raise SystemExit(f"{d}: 의사록 아직 없음")
    b = get(BOK + m["minutes"], binary=True)
    t, rel = pdf_text(b), pdf_date(b)
    print(f"한은 의사록 {d} — {m['minutes_title']} — {len(t)}자, 공개일(PDF 생성일 기준) {rel or '미확인'}")
    print("  저장:", save(f"bok_{d}_minutes.txt", f"{m['minutes_title']} · 공개일(PDF 생성일 기준) {rel or '미확인'}\n\n{t}"))


# ── 감지 ────────────────────────────────────────────────────────────────
def cmd_check():
    st = json.load(open(os.path.join(HERE, "state.json"), encoding="utf-8-sig"))
    today = today_kst()
    found = False
    try:
        ms = boj_meetings()
        for d, m in sorted(ms.items()):
            if d > st.get("BOJ", "0000") and d <= today:
                print(f"NEW_BOJ {d} presconf={'ready' if m['presconf'] else 'pending'}"); found = True
            elif d == st.get("BOJ") and d > st.get("BOJ_PC", "0000") and m["presconf"]:
                print(f"BOJ_PC_READY {d} (결정 보고서에 총재 회견 보강)"); found = True
            if m["opinion"] and d > st.get("BOJ_OPINION", "0000") and d <= st.get("BOJ", "0000"):
                print(f"NEW_BOJ_OPINION {d}"); found = True
    except Exception as e:
        print(f"(일본은행 확인 실패: {e})")
    try:
        bm = bok_meetings()
        for d, m in sorted(bm.items()):
            if d > st.get("BOK", "0000") and d <= today:
                print(f"NEW_BOK {d} code={m['code']}"); found = True
            if m.get("minutes") and d > st.get("BOK_MINUTES", "0000") and d <= st.get("BOK", "0000"):
                print(f"NEW_BOK_MINUTES {d} ({m['minutes_title']})"); found = True
    except Exception as e:
        print(f"(한은 확인 실패: {e})")
    if not found:
        print(f"NO_CB_NEW (BOJ {st.get('BOJ')} · BOK {st.get('BOK')})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["check", "get-boj", "get-boj-opinion", "get-bok", "get-bok-minutes"])
    ap.add_argument("date", nargs="?")
    a = ap.parse_args()
    if a.cmd == "check":
        return cmd_check()
    if not a.date:
        ap.error("날짜(YYYY-MM-DD) 필요")
    {"get-boj": cmd_get_boj, "get-boj-opinion": cmd_get_boj_opinion, "get-bok": cmd_get_bok,
     "get-bok-minutes": cmd_get_bok_minutes}[a.cmd](a.date)


if __name__ == "__main__":
    main()

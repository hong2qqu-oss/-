# -*- coding: utf-8 -*-
"""
일본 실질금리 과거분 보충 (1회성) → jp_real.json 에 병합

원리:
  JBTS BEI 페이지는 최근 약 3년만 보여주는 "롤링 윈도우"다.
  따라서 Wayback Machine 스냅샷 하나하나에 그 시점 기준 과거 3년치가 통째로 들어있다.
  스냅샷 수십 개를 겹쳐 붙이면 2016-12까지 거슬러 올라간다.

왜 2016-12 까지만인가:
  그 이전 스냅샷은 구 사이트의 다른 포맷(_graph/js/graph_bei.js)인데, 검증에서 탈락했다.
    - 2013년: 재무성 공식 명목금리와의 상관계수 -0.03 (사실상 무관 = 쓰레기)
    - 2009~2012년: -0.21 → -0.51%p 로 편차가 계속 커짐(정의 차이로 설명 안 됨)
  구 포맷은 필드명도 뒤바뀌어 있다(BEI 키에 실질금리가 들어있음). 통째로 버렸다.

검증 내장:
  저장 전에 재무성(財務省) 공식 국채금리와 대조해서 기준 미달이면 저장하지 않는다.
  JBTS 명목은 '10년 벤치마크 종목'의 복리이율이고 재무성 '10年'은 보간된 constant maturity라
  체계적으로 약 -0.07%p 낮게 나오는 게 정상이다. 그래서 '편차의 절대값'이 아니라
  '상관계수와 편차의 안정성(표준편차)'으로 판정한다.

사용: python fetch_jp_real_backfill.py [--push]
"""
import sys, os, re, json, time, math, subprocess
import datetime as dt
import urllib.request, urllib.parse
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = Path(__file__).parent
OUT = HERE / "jp_real.json"
LOG = HERE / "jp_real_run.log"
CACHE = HERE / ".wayback_cache"          # 과거 스냅샷은 안 변하므로 영구 캐시
TARGET = "www.bb.jbts.co.jp/ja/historical/marketdata05.html"
MOF_CSV = "https://www.mof.go.jp/jgbs/reference/interest_rate/data/jgbcm_all.csv"
CUTOFF = "2016-12-01"                    # 이 날짜 이후만 채택 (구 포맷 배제)
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
ERA = {"S": 1925, "H": 1988, "R": 2018}


def log(m):
    line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}  {m}"
    print(line)
    try:
        LOG.open("a", encoding="utf-8").write(line + "\n")
    except Exception:
        pass


def get(url, tries=3, timeout=120):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as f:
                return f.read()
        except Exception as e:
            last = e
            time.sleep(3 * (i + 1))
    raise last


def parse_chart(html):
    """fetch_jp_real.py 와 동일한 구조를 파싱. (날짜배열, [실질, 명목, BEI])"""
    cats = re.findall(r'"(\[\\"\d{4}/\d{2}/\d{2}\\".*?\])"', html, re.S)
    tbls = re.findall(r'"(\[\[[-\d.,\s\[\]]*?\]\])"', html, re.S)
    if not cats or not tbls:
        return None
    dates = json.loads(cats[0].replace('\\"', '"'))
    table = json.loads(tbls[0])
    if len(table) < 3 or not all(len(t) == len(dates) for t in table[:3]):
        return None
    return dates, table[:3]


# ── 1. Wayback 스냅샷 목록 ────────────────────────────────────────────────
def snapshot_list():
    cdx = ("http://web.archive.org/cdx/search/cdx?url="
           + urllib.parse.quote(TARGET, safe="")
           + "&output=json&filter=statuscode:200&collapse=digest")
    rows = json.loads(get(cdx))
    return [r[1] for r in rows[1:]]


# ── 2. 재무성 공식 명목금리 (검증 기준) ────────────────────────────────────
def load_mof():
    txt = get(MOF_CSV).decode("cp932", "replace")
    lines = [l for l in txt.splitlines() if l.strip()]
    hdr = next(i for i, l in enumerate(lines) if l.startswith("基準日"))
    i10 = lines[hdr].split(",").index("10年")
    out = {}
    for l in lines[hdr + 1:]:
        p = l.split(",")
        if p and p[0][:1] in ERA:
            try:
                y, m, d = p[0][1:].split(".")
                out[f"{int(y)+ERA[p[0][0]]}-{int(m):02d}-{int(d):02d}"] = float(p[i10])
            except ValueError:
                pass
    return out


def verify(rows, mof):
    """재무성 공식값과 대조. (통과여부, 리포트문자열)"""
    pairs = [(r["nom"], mof[r["d"]]) for r in rows if r["d"] in mof]
    if len(pairs) < 100:
        return False, f"대조 가능일 {len(pairs)}건 — 너무 적어 검증 불가"
    n = len(pairs)
    ours = [a for a, _ in pairs]
    offi = [b for _, b in pairs]
    ma, mo = sum(ours)/n, sum(offi)/n
    num = sum((a-ma)*(b-mo) for a, b in pairs)
    da = math.sqrt(sum((a-ma)**2 for a in ours))
    do = math.sqrt(sum((b-mo)**2 for b in offi))
    r = num/(da*do) if da and do else 0.0
    dif = [a-b for a, b in pairs]
    mean = sum(dif)/n
    sd = math.sqrt(sum((v-mean)**2 for v in dif)/n)
    nonbiz = sum(1 for x in rows if x["d"] not in mof)
    rep = (f"대조 {n}일 | 상관 {r:.5f} | 평균편차 {mean:+.4f}%p | 표준편차 {sd:.4f}%p | "
           f"재무성 영업일 외 {nonbiz}일")
    # 판정: 형태가 맞고(상관 높음) 편차가 안정적이며(표준편차 작음) 방향이 예상대로(약간 음수)
    ok = (r >= 0.95) and (sd <= 0.06) and (-0.25 <= mean <= 0.05) and nonbiz == 0
    return ok, rep


def main():
    log("=== 일본 실질금리 과거분 보충 시작 ===")
    CACHE.mkdir(exist_ok=True)

    tss = snapshot_list()
    log(f"Wayback 스냅샷 {len(tss)}개")

    merged = {}
    ok_snap = 0
    for ts in tss:
        fp = CACHE / f"{ts}.html"
        try:
            if fp.exists():
                html = fp.read_text(encoding="utf-8")
            else:
                html = get(f"http://web.archive.org/web/{ts}id_/https://{TARGET}").decode("utf-8", "replace")
                fp.write_text(html, encoding="utf-8")
                time.sleep(1.5)          # 아카이브 서버 배려
        except Exception as e:
            log(f"  {ts} 다운로드 실패: {type(e).__name__}")
            continue
        p = parse_chart(html)
        if not p:
            continue
        ok_snap += 1
        dates, (real, nom, bei) = p
        for i, d in enumerate(dates):
            iso = d.replace("/", "-")
            if iso < CUTOFF:
                continue                  # 구 포맷 구간은 채택하지 않음
            merged[iso] = {"d": iso, "real": real[i], "nom": nom[i], "bei": bei[i]}
    log(f"파싱 성공 {ok_snap}/{len(tss)} 스냅샷 → 고유 날짜 {len(merged)}일")

    rows = [merged[k] for k in sorted(merged)]
    if not rows:
        log("✗ 수집 0건"); sys.exit(1)

    # 내부 정합성
    bad = [r for r in rows if abs((r["nom"] - r["real"]) - r["bei"]) > 0.011]
    log(f"내부 정합성(BEI=명목-실질) 위반 {len(bad)}건 / {len(rows)}")

    # 재무성 대조
    log("재무성 공식 국채금리와 대조 중...")
    passed, rep = verify(rows, load_mof())
    log(f"  {rep}")
    if not passed:
        log("✗ 검증 실패 — 저장하지 않고 종료합니다.")
        sys.exit(1)
    log("✓ 검증 통과")

    # 기존 파일과 병합 (라이브 스크랩분 보존)
    old = []
    if OUT.exists():
        try:
            old = json.loads(OUT.read_text(encoding="utf-8")).get("days", [])
        except Exception:
            old = []
    m = {r["d"]: r for r in old}
    added = sum(1 for r in rows if r["d"] not in m)
    conflict = sum(1 for r in rows if r["d"] in m and m[r["d"]] != r)
    m.update({r["d"]: r for r in rows})
    days = [m[k] for k in sorted(m)]
    log(f"병합: 기존 {len(old)}일 + 신규 {added}일 = {len(days)}일 (기존값과 불일치 {conflict}건)")

    payload = {
        "updated": dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "unit": "%",
        "source": "日本相互証券 BB — 10년 물가연동국채/이표부국채 복리이율 (과거분 Wayback 보충)",
        "fields": {"real": "10년 실질금리(물가연동채)", "nom": "10년 명목국채", "bei": "기대인플레"},
        "days": days,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log(f"✓ 저장 완료 {days[0]['d']} ~ {days[-1]['d']}")

    if "--push" in sys.argv:
        subprocess.run(["git", "add", OUT.name], cwd=HERE)
        if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=HERE).returncode != 0:
            subprocess.run(["git", "commit", "-m",
                            f"일본 실질금리 과거분 보충 ({days[0]['d']}~)"], cwd=HERE)
            subprocess.run(["git", "pull", "--rebase", "origin", "main"], cwd=HERE)
            r = subprocess.run(["git", "push", "origin", "main"], cwd=HERE)
            log("git push " + ("완료" if r.returncode == 0 else "실패"))


if __name__ == "__main__":
    main()

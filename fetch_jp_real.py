# -*- coding: utf-8 -*-
"""
일본 10년 실질금리 수집 → jp_real.json  (일본상호증권 BB / bb.jbts.co.jp)

- real = 10년 물가연동국채 복리이율  (= 미국 TIPS 실질금리와 동일 개념)
- nom  = 10년 이표부 국채 복리이율
- bei  = 기대인플레 (검증: bei == nom - real, 소수 3자리에서 일치 확인됨)

왜 스크래퍼인가:
  bb.jbts.co.jp 는 corsproxy.io / allorigins / codetabs 전부 403·5xx로 막힘.
  → 브라우저에서 직접 못 부름. fetch_supply.py 와 같은 "파이썬이 긁어서
    JSON 커밋 → 사이트는 동일출처로 읽기" 패턴 사용.

원본 함정:
  차트 데이터가 HTML 안에 JS 문자열로 인라인. 날짜축에 중복이 있음
  (2026/05/01~05/29 18개가 두 번 들어가 있었음) → 반드시 dedup. 안 하면
  COT 톱니 현상과 같은 계단형 왜곡이 생긴다.

사용: python fetch_jp_real.py [--push]
"""
import json, re, sys, subprocess
import datetime as dt
import urllib.request
from pathlib import Path

try:  # 윈도우 콘솔(cp949)에서 ✓ 같은 문자 깨져 죽는 것 방지
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = Path(__file__).parent
OUT = HERE / "jp_real.json"
LOG = HERE / "jp_real_run.log"
URL = "https://www.bb.jbts.co.jp/ja/historical/marketdata05.html"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


def log(m):
    line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}  {m}"
    print(line)
    try:
        LOG.open("a", encoding="utf-8").write(line + "\n")
    except Exception:
        pass


def fetch_html():
    req = urllib.request.Request(URL, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=40) as r:
        return r.read().decode("utf-8", "replace")


def parse(html):
    """HTML에 인라인된 JS 문자열에서 날짜축 / 3계열 값을 뽑는다."""
    cats = re.findall(r'"(\[\\"\d{4}/\d{2}/\d{2}\\".*?\])"', html, re.S)
    tbls = re.findall(r'"(\[\[[-\d.,\s\[\]]*?\]\])"', html, re.S)
    if not cats or not tbls:
        raise RuntimeError("차트 데이터 블록을 못 찾음 — 페이지 구조가 바뀌었을 수 있음")

    dates = json.loads(cats[0].replace('\\"', '"'))
    table = json.loads(tbls[0])
    if len(table) < 3:
        raise RuntimeError(f"계열이 3개가 아님 ({len(table)}개) — 페이지 구조 변경 의심")

    real, nom, bei = table[0], table[1], table[2]
    n = len(dates)
    if not (len(real) == len(nom) == len(bei) == n):
        raise RuntimeError(f"길이 불일치: dates={n} real={len(real)} nom={len(nom)} bei={len(bei)}")

    # 날짜 중복 제거 — 뒤에 나온 값을 채택(정정치로 간주). 값까지 다르면 로그.
    merged, conflicts = {}, 0
    for i, d in enumerate(dates):
        iso = d.replace("/", "-")
        row = {"d": iso, "real": real[i], "nom": nom[i], "bei": bei[i]}
        prev = merged.get(iso)
        if prev and (prev["real"] != row["real"] or prev["nom"] != row["nom"]):
            conflicts += 1
        merged[iso] = row

    dropped = n - len(merged)
    if dropped:
        log(f"  중복 날짜 {dropped}건 제거 (값 상이 {conflicts}건)")

    rows = [merged[k] for k in sorted(merged)]

    # 정합성: bei == nom - real 인지 확인 (소수점 오차 허용)
    bad = [r for r in rows if abs((r["nom"] - r["real"]) - r["bei"]) > 0.02]
    if bad:
        log(f"  ⚠ BEI 불일치 {len(bad)}건 (예: {bad[0]})")

    return rows


def main():
    log("=== 일본 실질금리 수집 시작 ===")
    try:
        rows = parse(fetch_html())
    except Exception as e:
        log(f"✗ 실패: {e}")
        sys.exit(1)

    if not rows:
        log("✗ 데이터 0건")
        sys.exit(1)

    # 기존 파일과 병합 — 원본이 과거를 잘라내도 우리가 모은 건 유지
    old = []
    if OUT.exists():
        try:
            old = json.loads(OUT.read_text(encoding="utf-8")).get("days", [])
        except Exception:
            old = []
    merged = {r["d"]: r for r in old}
    added = sum(1 for r in rows if r["d"] not in merged)
    merged.update({r["d"]: r for r in rows})
    days = [merged[k] for k in sorted(merged)]

    last = days[-1]

    # 원본은 월 단위로만 갱신된다. 값이 그대로면 파일을 아예 건드리지 않는다.
    # (updated 타임스탬프만 바뀌어도 git은 변경으로 보고 매일 빈 커밋을 만든다)
    if days == old:
        log(f"변경 없음 — {len(days)}일, 최신 {last['d']} (파일/커밋 생략)")
        return

    payload = {
        "updated": dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "unit": "%",
        "source": "日本相互証券 BB — 10년 물가연동국채/이표부국채 복리이율",
        "fields": {"real": "10년 실질금리(물가연동채)", "nom": "10년 명목국채", "bei": "기대인플레"},
        "days": days,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    log(f"✓ {len(days)}일 저장 (신규 {added}) | {days[0]['d']} ~ {last['d']}")
    log(f"  최신: real={last['real']}%  nom={last['nom']}%  bei={last['bei']}%")

    if "--push" in sys.argv:
        push(last["d"])


def push(last_date):
    """fetch_supply.py 와 같은 방식. 변경 없으면 커밋 자체를 건너뛴다."""
    if not (HERE / ".git").exists():
        log("git 저장소가 아님 — push 생략")
        return
    subprocess.run(["git", "add", OUT.name], cwd=HERE)
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=HERE).returncode == 0:
        log("변경 없음 — push 생략")
        return
    subprocess.run(["git", "commit", "-m", f"일본 실질금리 갱신 {last_date}"], cwd=HERE)
    # 수급 스크래퍼도 같은 저장소에 push한다. 원격이 앞서 있으면 rebase 후 재시도.
    subprocess.run(["git", "pull", "--rebase", "origin", "main"], cwd=HERE)
    r = subprocess.run(["git", "push", "origin", "main"], cwd=HERE)
    log("git push " + ("완료" if r.returncode == 0 else "실패"))


if __name__ == "__main__":
    main()

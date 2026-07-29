# 경제레이더 · 민홍 (EI 대시보드)

거시 경제지표 차트 사이트. 지표 하나 고르면 시계열 차트 + 이동평균 + 통계박스가 뜬다.

**배포:** GitHub Pages `hong2qqu-oss/-` → `dashboard.html`

---

## ⚠️ 제일 먼저 알아야 할 것 3가지

### 1. `index.html` 고쳤으면 `dashboard.html`로 반드시 복사

배포되는 파일은 `dashboard.html`이고, 작업하는 파일은 `index.html`이다. 둘은 **바이트 단위로 같아야** 한다.

```bash
cp index.html dashboard.html
```

PowerShell이면 `Copy-Item index.html dashboard.html -Force`.
이거 빼먹으면 "고쳤는데 사이트에 반영이 안 된다"가 된다. 매번 한다.

### 2. 빌드 시스템이 없다

`index.html` **한 파일**이 전부다. HTML + CSS + JS 약 2,300줄. npm도 번들러도 없다.
외부 의존성은 CDN 3개(Chart.js, chartjs-plugin-zoom, date-fns 어댑터)뿐.
그냥 파일 열어서 고치면 된다.

### 3. 서버가 없다 = 데이터는 브라우저가 직접 가져온다

백엔드가 없어서 모든 데이터를 브라우저에서 fetch한다. 그래서 **CORS가 항상 걸림돌**이다.
`https://corsproxy.io/?` 프록시를 경유하는 게 기본 패턴이고, 그게 막히는 소스는
파이썬으로 긁어서 JSON을 커밋해두고 동일출처로 읽는다(아래 참조).

---

## 로컬에서 띄우기

`file://`로 열면 JSON fetch가 CORS로 막힌다. 반드시 HTTP로:

```bash
python -m http.server 3000
```

그리고 `http://localhost:3000/index.html`.
(`.claude/launch.json`에 "EI Dashboard"로 등록돼 있음)

**FRED API 키는 localStorage에 있다.** 새 브라우저/시크릿창에서 열면 키가 없어서
FRED 지표가 전부 "API 키가 없습니다" 에러를 낸다. 코드 버그가 아니다.
우측 상단 ⚙ 버튼으로 입력한다. (키는 코드에 하드코딩하지 말 것)

---

## 데이터 소스

| `source` | 함수 | 출처 | 비고 |
|---|---|---|---|
| *(없음)* | `fetchFRED(id, freq)` | FRED | 기본값. **API 키 필요.** 원천이 BLS/BEA라 Investing과 수치 일치 |
| `price` | `fetchYahooWeekly(yf)` | Yahoo | 주식/ETF/지수/선물 가격 |
| `ratio` | `fetchRatio(yf, yfDenom)` | Yahoo | 두 티커 비율 (예: MTUM/SPY) |
| `cnn` | `fetchCNN(cnnKey)` | CNN Fear&Greed | **1년치만 줌.** localStorage에 누적 머지해서 점점 길어짐 |
| `cot` | `fetchCOT(cotName)` | CFTC | 선물 순포지션. 주간 |
| `tsa` | `fetchTSA()` | TSA + Wayback | 공항 검색대 인원 |
| `jpreal` | `fetchJPReal(jpField)` | `jp_real.json` | 일본 10년 실질금리/명목/BEI. 스크래퍼 산출물 |
| `spread` | `fetchUSJPRealSpread()` | FRED + `jp_real.json` | 미-일 실질금리차 |
| *(수급탭)* | `supply_data.json` | 키움 + KRX | 코스피 수급. 스크래퍼 산출물 |

### 파이썬 스크래퍼 (CORS로 못 뚫는 소스용)

| 스크립트 | 산출물 | 실행 |
|---|---|---|
| `fetch_supply.py` | `supply_data.json` | `python fetch_supply.py --push` |
| `fetch_jp_real.py` | `jp_real.json` | `python fetch_jp_real.py --push` |

`--push`는 git add/commit/push까지 한다. 산출 JSON은 **커밋해야** 사이트가 읽는다.

---

## 탭 구조

`currentCountry` 값으로 사이드바를 필터링한다. 지표의 `country` 필드가 이 값과 매칭된다.

`US` / `KR` / `JP` / `SENTI`(📐센티먼트) / `COT`(📊) / `SUPPLY`(💹수급, 별도 패널) / `EU`(미구현·비활성)

---

## 지표 하나 추가하는 법 (제일 흔한 작업)

`INDICATORS` 배열(파일 상단, 700줄 근처)에 객체 하나 넣으면 끝이다.
사이드바 등록·차트·이동평균·줌은 전부 자동으로 붙는다.

**FRED 지표라면 이 한 줄이 전부:**

```js
{ id:'DGS5', name:'5년물 국채금리', unit:'%', category:'금리 & 스프레드', country:'US', freq:'w', noStats:true },
```

**Yahoo 가격이라면:**

```js
{ id:'SENTI_HYG', name:'HYG (하이일드 ETF)', unit:'$', category:'신용 / CREDIT',
  country:'SENTI', source:'price', yf:'HYG', lineRaw:true },
```

### 필드 의미

| 필드 | 뜻 |
|---|---|
| `id` | FRED 시리즈 ID (FRED 소스일 때) 또는 임의 고유키 |
| `category` | 사이드바 그룹 헤더. 같은 문자열끼리 묶인다 |
| `country` | 어느 탭에 나올지 |
| `source` | 위 표 참조. 생략하면 FRED |
| `transform:'yoy'` | 지수 레벨 → 전년동기비로 변환해서 표시 |
| `freq:'w'` \| `'m'` | FRED에서 주간/월간으로 집계해 받음(일간 데이터 축소용) |
| `diffMode:true` | MoM 대신 절대 증감으로 (비농업고용용) |
| `noStats:true` | YoY/MoM 통계박스 숨김 (금리·비율처럼 무의미한 지표) |
| `lineRaw:true` | 막대 대신 라인으로 |
| `refLines:[{y,color,label}]` | 수평 기준선 (풋콜 0.7/1.0 같은) |

### 새로운 종류의 소스를 추가할 때

1. `fetchXxx()` 작성 → `[{x:'YYYY-MM-DD', y:숫자}, ...]` 반환
2. `LINE_SOURCES` 맵에 등록 → 라인 차트로 그려지고 **오버레이도 자동 지원**
3. `SOURCE_LABEL` 맵에 출처 이름 추가 (차트 부제목에 표시됨)

---

## 밟으면 아픈 지뢰들 (전부 실제로 밟아봄)

**시계열은 반드시 날짜 정렬 + 중복 제거.**
COT는 CFTC 응답이 순서가 뒤섞여 오고, 일본 JBTS는 원본 HTML에 같은 날짜가
두 번 들어있었다(2026/05 18건). 안 걸러내면 차트가 톱니/계단으로 망가진다.

**CORS 프록시는 아무 데나 안 통한다.**
`bb.jbts.co.jp`는 corsproxy.io(403), allorigins(520), codetabs(522) 전부 막혔다.
새 소스 붙이기 전에 프록시로 실제 받아지는지 **먼저** 확인하고, 막히면 파이썬 스크래퍼 패턴으로 간다.

**FRED 시리즈 고를 때 Investing 수치와 맞는지 확인.**
PPI는 `PPIACO`(All Commodities)가 아니라 `PPIFIS`(Final Demand)여야 일반적으로 말하는 PPI와 맞는다.

**서로 다른 시장의 일간 시계열을 뺄 땐 `spreadAsOf()`를 쓴다.**
미국·일본은 휴장일이 다르다. 그냥 인덱스로 빼면 날짜가 어긋난다.
그리고 한쪽 데이터가 끊긴 뒤 마지막 값이 옆으로 끌려가 **가짜 스프레드**가 그려지지 않도록
staleness 가드(기본 7일)가 들어있다. 건드리지 말 것.

**윈도우 콘솔은 cp949라 `✓` 같은 문자에서 죽는다.**
파이썬 스크립트 상단에 `sys.stdout.reconfigure(encoding="utf-8")` 넣어둘 것.

**`.claude/`, `_*.py`, `*.log`는 gitignore 대상.** 커밋 안 된다.

---

## 알려진 한계 / 막힌 것

- **일본 실질금리는 2023-12부터**, 그리고 원본이 월 단위로 갱신돼 **약 1개월 지연**된다.
  따라서 미-일 실질금리차도 같은 제약을 받는다.
- **풋콜 장기 히스토리**: CBOE 유료. CNN 1년치 + localStorage 누적이 유일한 공짜 방법.
  브라우저별로 쌓이므로 기기 간 공유 안 됨.
- **원화/코스피/JGB COT 없음**: CFTC는 미국 시장만. KRX/JPX 별도 소스 필요.
- **ISM PMI**: FRED가 2019까지만 → 필라델피아 연준 서베이로 대체 중.
- **한국 기준금리**: 한국은행 ECOS API 키 필요 (미발급).
- **일본 CPI/PPI가 2021~22에서 끊김**: e-Stat API 키 필요 (미발급).
- **EU 탭 미구현**: ECB API 붙이면 됨.

---

## 배포

```bash
cp index.html dashboard.html
git add -A && git commit -m "설명" && git push
```

GitHub Pages가 `dashboard.html`을 서빙한다.
**지표 데이터 자체는 사이트가 열릴 때마다 실시간으로 받아오므로 재배포가 필요 없다.**
재배포가 필요한 건 코드를 고쳤을 때, 그리고 스크래퍼 JSON(`supply_data.json`,
`jp_real.json`)을 갱신했을 때뿐이다.

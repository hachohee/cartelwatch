# 카르텔워치 (CartelWatch) — 강원 공공계약 감시 보드

강원특별자치도 **18개 시군 · 도청 · 교육청(산하 교육지원청·학교)** 의 계약 공개 자료를 모아
① 계약을 한눈에 훑고 ② 이상징후 후보를 자동으로 뽑고 ③ 소재 카드로 키우고 ④ 팩트체크로 검증하는
PC·모바일 웹앱입니다.

- 배포: **https://hachohee.github.io/cartelwatch/**
- 성격: GitHub Pages **정적 사이트**(서버 없음). 데이터는 저장소 안 `data/*.json`만 읽습니다.
- 화면: 계약 데이터 / 이상징후 / 소재 보드 / 팩트체크 (좌측 4개 메뉴)
- 수집: **20개 기관 전부 홈페이지 크롤링 + 나라장터 API 양쪽 합산**(중복 제거) 후 **누적**

---

## 1. 구조

```
cartelwatch/
├─ index.html                 # 단일 페이지 (프레임워크 없음)
├─ assets/
│  ├─ style.css               # 레이아웃·카드·칸반·표 스타일
│  └─ app.js                  # 화면 4개 + localStorage 소재 보드/팩트체크
├─ data/
│  ├─ contracts.json          # 수집된 계약 (앱이 읽는 실제 데이터)
│  ├─ anomalies.json          # 4대 탐지 결과 + 임계치
│  ├─ meta.json               # 기관별 수집 상태·기간·수집 시각
│  └─ sample_contracts.json   # 실데이터가 없을 때만 쓰는 샘플(화면에 '샘플' 배지)
└─ scripts/
   ├─ agencies.json           # 기관 레지스트리 (기관 추가 = 여기 한 줄)
   ├─ fetch_contracts.py      # 홈페이지 크롤러 + (선택) 나라장터 API
   └─ detect_anomalies.py     # 4대 탐지 → anomalies.json
```

### 데이터 원칙

1. **브라우저는 나라장터 API를 직접 부르지 않습니다.** CORS·타임아웃·불안정 때문입니다.
   대신 저장소 안의 Python 수집 스크립트가 미리 긁어 `data/*.json`으로 커밋하고,
   웹앱은 그 **정적 JSON만** 읽습니다.
2. **인증키는 코드·저장소에 넣지 않습니다.** 스크립트는 환경변수 `DATA_GO_KR_KEY`만 읽고,
   키가 없으면 **지자체·교육청 홈페이지 크롤러만** 돌아갑니다(키 없이도 정상 동작).
3. **양쪽 합산**: 같은 기관을 홈페이지(소액 수의계약 강점)와 나라장터 API(큰 계약·입찰분)로
   두 번 조회해 합칩니다. 같은 계약은 하나로 병합하고 `source_type`(`crawl`/`g2b_api`/`both`)에 남깁니다.
4. **증분 누적**: 매일 전체를 덮어쓰지 않고 기존 `data/contracts.json`에 새 수집분을 upsert 합니다.
   일일 실행은 최근 10일, `--full`은 28일을 훑습니다.
5. 실데이터와 샘플을 섞지 않습니다. 샘플로 뜰 때는 상단에 **샘플 배지**가 붙습니다.
6. 원본 근거(계약번호·금액·계약일·상세 URL)를 표에서 항상 보이게 둡니다.

### 확장 뼈대(서랍 구조)

각 계약 레코드에 **`agency_id` · `vendor_key`(정규화 업체명) · `source_type`** 을 항상 남깁니다.
나중에 업체 정보(지역·업종), 지방보조금, 예산 집행 데이터를 같은 연결고리로 붙일 수 있습니다.
- `vendor_key`는 `(주)`·`주식회사`·공백·기호를 걷어낸 키입니다(예: `(주)토담하이텍구조` → `토담하이텍구조`).
- `anomalies.json`의 `summary.by_agency`에 기관별 건수·금액·이상징후 수를 미리 계산해 둡니다.

---

## 2. 데이터 수집

### 설치·실행 (파이썬 3.9+, 표준 라이브러리만 사용)

```bash
# 전체 기관 (홈페이지 + 나라장터 API 최근 10일) → data/contracts.json, data/meta.json
python3 scripts/fetch_contracts.py

# 첫 적재·정기 전체: API 28일치까지
python3 scripts/fetch_contracts.py --full

# 특정 기관만 (여러 번 지정 가능)
python3 scripts/fetch_contracts.py --agency goe --agency chuncheon

# 페이지 수 줄여서 빠르게 확인 / API 없이 홈페이지만
python3 scripts/fetch_contracts.py --pages 1 --no-api

# 기존 수집분과 합치지 않고 새로 쓰기
python3 scripts/fetch_contracts.py --reset

# 탐지 실행 → data/anomalies.json
python3 scripts/detect_anomalies.py
```

크롤링 예절: 요청 간 지연 0.5~1초(기관별 설정), User-Agent 명시, 기관별 페이지 상한(기본 3~8페이지).
실패는 **재시도 3회(지수 백오프)** 후 그 기관만 `실패`로 기록하고 나머지는 계속 수집합니다.
이번 수집이 전부 실패하면 **기존 `data/contracts.json`을 지우지 않습니다.**

### 수집 기관 추가

`scripts/agencies.json`의 `agencies` 배열에 한 줄 추가하면 됩니다.

```json
{
  "id": "chuncheon", "name": "춘천시", "category": "시군",
  "parser": "chuncheon",
  "list_url": "https://www.chuncheon.go.kr/contract/data/data1/data1-4/",
  "pages": 6
}
```

- `category`: `시군` / `도청` / `교육청` / `교육지원청`
- `parser`: 아래 파서 중 하나. 모르면 `auto`(URL 후보로 표 구조 자동 판별)

| parser | 대상 | 특징 |
|---|---|---|
| `chuncheon` | 춘천시 1인 견적 수의계약 | `ctrtAcctBookMngNo`를 계약번호로 사용 |
| `wonju_table` | 원주시 계약현황 | 헤더 이름으로 열 매핑 |
| `gyeyak` | 강릉·동해·홍천 (계약정보공개시스템 공통) | `ul.ul-body` 목록, `?p=` 페이지 |
| `confin_post` | 도청·평창 (세입세출 계약정보공개) | 1행형/2행형 표 모두 처리, `POST pageIndex` |
| `table_post_multi` | 정선·속초 (여러 게시판) | 헤더 이름 매핑, `POST pageIndex` |
| `yanggu_ajax` | 양구·인제 (jqGrid 수의계약현황) | `ajaxOptionalList.do` JSON 직접 호출 |
| `ihc_api` | 화천 (contract.ihc.go.kr) | `/api/situ` JSON, 10건 단위 |
| `goe_keris` | 강원특별자치도교육청 수의계약내역공개 | 목록+상세(금액·업체·대표자·사유) |
| `g2b_api` | 나라장터 API 수집 기관 | 홈페이지가 막혔거나 보완이 필요한 곳 |
| `auto` | 그 외 | 알려진 표 구조를 찾아 자동 판별 |

### TLS 인증서 체인 오류 기관 (허용목록)

중간 인증서가 빠진 지자체 서버는 검증이 실패합니다. 해당 호스트만 `tls_relaxed_hosts`에
적어 두면, **인증서 검증 실패일 때만** 검증을 완화해 읽기 전용으로 조회합니다.
목록에 없는 호스트는 검증 실패를 그대로 실패로 남기므로 다른 기관에는 영향이 없습니다.

```json
"tls_relaxed_hosts": ["ehojo.taebaek.go.kr"]
```

완화 접속을 실제로 쓴 기관은 `data/meta.json`의 각 기관 `tls` 항목에 호스트와
인증서 지문(SHA-256)이 남습니다. 지문이 바뀌면 서버 인증서가 교체된 것이니 확인하세요.
웹방화벽(WAF)이 자동 요청을 막는 기관은 우회하지 않고 `웹방화벽(WAF) 차단`으로만 기록합니다.

### 나라장터 API 키 (필수는 아니지만 강력 권장)

1. [공공데이터포털](https://www.data.go.kr) 로그인
2. **“조달청_나라장터 계약정보서비스”** 검색 → **활용신청** (자동 승인)
3. 마이페이지 → 개발계정 → **일반 인증키(Decoding)** 복사
4. 환경변수로만 사용:

```bash
export DATA_GO_KR_KEY="발급받은_인증키"
python3 scripts/fetch_contracts.py --full   # 20개 기관 전부 API까지 합산
```

> 키는 절대 파일에 저장하거나 커밋하지 마세요. `.env`도 커밋 금지입니다.

동작 방식(2026-11-25 개편):

- **20개 전 기관**(도청·교육청 + 18개 시군)에 대해 홈페이지 크롤링과 API를 **둘 다** 실행합니다.
  레지스트리 `narajangter.instt_nm`·`kind`(`town`/`province`/`edu`)로 계약기관 정식명을 매핑합니다
  (예: `"강원특별자치도 춘천시"`, `"강원특별자치도교육청"`).
- 오퍼레이션: `https://apis.data.go.kr/1230000/ao/CntrctInfoService/` 아래
  `getCntrctInfoListThngPPSSrch`(물품)·`getCntrctInfoListCnstwkPPSSrch`(공사)·`getCntrctInfoListServcPPSSrch`(용역).
  `insttNm`은 **앞부분 일치**라 `"강원특별자치도 삼척시"` 한 번으로 삼척시 본청·상수도사업소·읍면이 함께 잡힙니다.
  도청 질의(`"강원특별자치도"`)는 시군·교육청까지 잡히므로 그 몫은 걸러냅니다(각자 질의에서 받음).
- 조회기간 상한이 **7일**(2026-11-10 공지로 1개월→7일)이라, 최근 `--days`(기본 10일, `--full` 28일)를
  7일 창으로 잘라 거슬러 호출합니다. `G2B_DAYS=56`처럼 환경변수로도 조절할 수 있습니다.
- 날짜는 계약체결일자(`inqryDiv=1`), 금액은 **금차계약금액**(없으면 총계약금액), 업체는 `corpList`의
  주계약업체, 상세 링크는 `cntrctDtlInfoUrl`(나라장터 계약 상세)을 씁니다.

### 중복 제거 규칙 (두 출처 합칠 때)

1. **기관 + 계약번호**가 같으면 같은 계약 → 하나로 병합.
2. 계약번호 형식이 다른 출처끼리는 **기관 + 계약일 + 금액 + 정규화 업체명 앞부분** 서명으로 묶습니다.
   단, 그 서명이 **양쪽에 하나씩만 있을 때만** 병합합니다. 같은 업체가 같은 날 같은 금액으로 여러 건을
   나눠 받은 경우(쪼개기의 핵심 증거)는 합치지 않습니다.
3. 병합 시 홈페이지 레코드를 기본으로 두고 API의 빈 칸을 채우며, 상세 URL·계약방법은 홈페이지 원문을 보존합니다.

### 증분 누적

- 실행할 때마다 기존 `data/contracts.json`을 먼저 읽고, 새 수집분을 위 규칙으로 합쳐 다시 씁니다.
- `last_seen`(마지막으로 확인된 날)이 남아 어떤 계약이 언제 수집됐는지 추적할 수 있습니다.
- `data/contracts.json`은 `by_source`(홈페이지/API/양쪽) 집계와 `total`을 함께 저장합니다.

---

## 2-1. 화면 (버튼형 카드 UI, 2026-11-25 개편)

- **요약 카드 4장**: 총 계약 · 총 금액 · 수집 기관(성공/20) · 이상징후 후보.
- **유형 바로가기 카드 4장**: 쪼개기 / 몰아주기 / 신생법인 / 연말집행 — 누르면 그 유형만 보이는 이상징후 화면으로 이동.
- **기관 버튼 그리드**: `[전체] [도청] [교육청]` + 18개 시군 버튼. 각 버튼에 **건수·이상징후 배지**가 붙고,
  한 번 누르면 계약 표가 그 기관만 남습니다.
- **이상징후 화면**: 유형 버튼 탭 + 기관 버튼으로 이중 필터. 카드가 많으면 `N건 더 보기`로 나눠 그립니다.
- **정밀 검색**: 부서·업체·기간·금액·통합검색은 접이식 상자에 그대로 보존.
- 소재 보드·팩트체크·상세 모달·CSV·localStorage 키(`cw:board:v1`, `cw:fact:v1`)는 예전 그대로입니다.

---

## 3. 이상징후 판정 기준

`scripts/detect_anomalies.py` 상단 상수에서 바로 바꿀 수 있습니다.

| 유형 | 기준 |
|---|---|
| 쪼개기 수의계약 | 같은 부서·같은 업체 / **7일 이내** / 건별 **2,000만 원 미만** & 합산 **2,000만 원 이상** / 계약명 유사도 0.45 이상 |
| 일감 몰아주기 | 부서(계약이 적으면 기관) 내 한 업체 **점유율 35% 초과** & 3건 이상 & 5,000만 원 이상 |
| 신생법인 특혜(의심) | 수집 데이터 **첫 등장** 업체가 **1억 원 이상** 수주 (등장 1건) |
| 연말 예산 밀어내기 | **12/20~12/31** 집행 3건 이상 & 해당 기관 금액 비중 **25% 초과** |

⚠️ 탐지 결과는 **소재 후보**입니다. 위법·고의성 판단은 아닙니다.
특히 *신생법인*은 설립일 데이터가 없어 “데이터 첫 등장” 기준이므로,
법인등기부로 설립일을 확인하기 전에는 기사에 쓰지 마세요.

---

## 4. 매일 갱신

### (A) GitHub Actions (권장 · 서버 불필요)

`.github/workflows/update-data.yml` 이 매일 **06:10 KST**(21:10 UTC)에 수집·탐지 후
`data/*.json` 을 자동 커밋합니다.

- 저장소 → Settings → Actions → General → **Workflow permissions: Read and write** 로 설정
- API 키를 쓰려면 Settings → Secrets → Actions 에 `DATA_GO_KR_KEY` 추가 (선택)
- Actions 탭 → `update-data` → **Run workflow** 로 수동 실행도 가능

### (B) 내 PC에서 (수동/ cron)

```bash
cd cartelwatch
python3 scripts/fetch_contracts.py && python3 scripts/detect_anomalies.py
git add data && git commit -m "data: daily update" && git push
```

cron 예시(매일 06:10):

```
10 6 * * * cd ~/cartelwatch && python3 scripts/fetch_contracts.py && python3 scripts/detect_anomalies.py && git add data && git commit -m "data: daily update" && git push
```

---

## 5. 소재 보드 · 팩트체크 저장

서버가 없으므로 **브라우저 localStorage**에 저장합니다.

- 키: `cw:board:v1`(소재 카드), `cw:fact:v1`(체크 상태·메모)
- **JSON 내보내기/불러오기**로 백업·이전 (같은 id는 덮어씀)
- 브라우저 캐시/사이트 데이터를 지우면 사라지므로, 중요한 소재는 주기적으로 내보내 두세요.

---

## 6. 알려진 한계

- 지자체 사이트가 개편되거나 웹방화벽으로 자동 요청을 막으면 해당 기관은 `실패`로 표시됩니다.
  계약 데이터 화면의 **기관별 수집 상태** 표에서 바로 확인할 수 있습니다.
- 수집 페이지 수만큼만 최근 데이터가 들어옵니다(전체 이력 아님).
- 교육청 자료는 목록에 금액·업체가 없어 상세페이지를 건별로 열어 보강합니다
  (`detail_limit`로 상한 조절, 기본 54건/회).
- 몰아주기·연말집행은 계약 건수가 충분해야 잡히므로, 처음 며칠은 후보가 0건일 수 있습니다.
- 모든 금액은 원 단위. 원자료 오타(예: 2226년)는 통계에서 제외합니다.

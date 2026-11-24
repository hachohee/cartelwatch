# 수집 기관 현황 (2026-11-24 기준)

| 구분 | 기관 | 파서 | 상태 | 비고 |
|---|---|---|---|---|
| 교육청 | 강원특별자치도교육청(교육지원청·학교 포함) | goe_keris | 성공 70건 | 목록+상세 |
| 도청 | 강원특별자치도 | confin_post | 성공 80건 | 계약체결 현황(공사) |
| 시군 | 춘천시 | chuncheon | 성공 80건 | 1인 견적 수의계약 |
| 시군 | 원주시 | wonju_table | 성공 60건 | 상하수도사업소 공개분 |
| 시군 | 강릉시 | gyeyak | 성공 80건 | 계약현황 전체 |
| 시군 | 동해시 | gyeyak | 성공 80건 | 계약현황 전체 |
| 시군 | 홍천군 | gyeyak | 성공 80건 | 계약현황 전체 |
| 시군 | 평창군 | confin_post | 성공 120건 | 계약체결 현황 |
| 시군 | 정선군 | table_post_multi | 성공 90건 | 수의계약(공사·용역·물품) |
| 시군 | **횡성군** | gyeyak | 성공 80건 | gyeyak.hsg.go.kr — 군청 '계약정보공개' 메뉴가 이 시스템으로 연결 |
| 시군 | **양양군** | gyeyak | 성공 80건 | gyeyak.yangyang.go.kr — https 미지원 서버, http로 수집 |
| 시군 | **고성군** | gyeyak | 성공 80건 | www.gwgs.go.kr/gyeyak — 강원 고성군 주소(경남 고성은 goseong.go.kr) |
| 시군 | **영월군** | confin_post | 성공 80건 | yw.go.kr/confin/contract/continfo — POST pageIndex |
| 시군 | **양구군** | yanggu_ajax | 성공 120건 | 목록이 jqGrid(AJAX) → `ajaxOptionalList.do` JSON 직접 호출 |
| 시군 | 태백시 | gyeyak | 방화벽 차단 | ehojo.taebaek.go.kr/contract/lists — 구조는 gyeyak과 동일(확인), 웹방화벽이 자동 요청 차단 |
| 시군 | 삼척·속초·철원·화천·인제 | auto | 실패 | 아래 참고 |

## 6개 시군 추가 수집 메모 (2026-11-24)

- **횡성군**: 군청(www.hsg.go.kr)의 '계약정보공개' 메뉴가 별도 시스템 `gyeyak.hsg.go.kr`로 연결된다. 강릉·동해·홍천과 같은 gyeyak CMS라 기존 파서로 그대로 수집된다(`/contract/lists?p=N`).
- **양양군**: 포털(/gw/portal)의 '계약정보공개'가 `gyeyak.yangyang.go.kr`로 연결. 이 서버는 **https 접속을 거부(Connection refused)** 해 http로만 수집한다. 공개 조회 페이지라 http로도 같은 내용이 나온다.
- **고성군(강원)**: 강원 고성군청 주소는 `www.gwgs.go.kr`이다. 기존 후보였던 `www.goseong.go.kr`은 **경상남도 고성군**이라 계약정보를 받아도 다른 지자체가 된다(400 응답의 원인). 수집 주소는 `www.gwgs.go.kr/gyeyak/contract/lists?p=N`.
- **영월군**: 계약현황 표는 `/confin/contract/continfo`에 있다(기존 후보 `/confin/contract`는 표가 없는 안내 페이지). POST `pageIndex` 페이지네이션이라 기존 `confin_post` 파서로 수집된다. 발주기관은 `extra.발주기관`에 담긴다.
- **양구군**: 화면의 표는 jqGrid가 AJAX로 그린다. 화면 스크립트(`/contract/js/basis/optionalList.js`)가 호출하는 `POST /contract/basis/ajaxOptionalList.do`에 JSON 본문(`page`, `rowNum`, `ctrtNm` 등)을 보내면 `dataList` JSON을 돌려준다. 그래서 `yanggu_ajax` 파서를 새로 추가했다(수의계약현황, 전체 약 2.5만 건 중 최근 120건).
- **태백시**: 시청 홈페이지(www.taebaek.go.kr)의 '계약정보공개'가 `ehojo.taebaek.go.kr` 계약정보공개시스템으로 연결된다. 목록 `/contract/lists?p=N`은 **강릉·동해와 같은 gyeyak CMS**(`ul.ul-body` 10건, `/contract/post/{번호}`)라 파서는 `gyeyak`으로 맞춰 두었다. 다만 이 서버의 웹방화벽이 자동 요청(데이터센터 IP·파이썬 TLS)을 403/인증서 차단으로 막아, 이 작업 환경에서는 수집이 실패한다. 브라우저로는 정상 열람되므로 구조 확인·수동 확인은 가능하다.

## 자동 수집이 안 되는 기관 (수동 확인 필요)

- **삼척시**: 웹방화벽(WAF)이 자동 요청을 차단("웹방화벽 보안정책에 위배").
- **태백시**: 위 참고. 파서·주소는 확정, 네트워크 단계에서 차단.
- **속초시·철원군·화천군·인제군**: 이 작업 환경에서 TLS 핸드셰이크/DNS가 막혀 접속 자체가 실패.

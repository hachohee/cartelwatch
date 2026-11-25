#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
카르텔워치 수집기 (CartelWatch collector)
=========================================
강원특별자치도 18개 시군 + 강원특별자치도 본청 + 강원특별자치도교육청(산하 교육지원청·학교)의
계약 공개 홈페이지를 직접 긁어 data/contracts.json 을 만듭니다.

원칙
- **양쪽 합산(하이브리드)**: 20개 전 기관에 대해 홈페이지 크롤링(소액 1인 수의계약 강점)과
  나라장터 계약정보서비스(ao/CntrctInfoService, 큰 계약·입찰분)를 **둘 다** 조회해 합칩니다.
  두 출처가 같은 계약을 가리키면 하나로 병합합니다(계약번호 → 기관+계약일+금액+업체명 앞부분).
  DATA_GO_KR_KEY 가 없으면 홈페이지 크롤링만으로 정상 동작합니다.
- **증분 누적**: 기존 data/contracts.json 을 먼저 읽고, 이번 수집분을 upsert/중복제거해 쌓습니다.
  매일은 최근 10일(G2B_DAYS), --full 또는 --days 28 로 4주치를 훑습니다. --reset 은 새로 쓰기.
- 각 레코드에 vendor_key(정규화 업체명)·agency_id·source_type(crawl/g2b_api/both)을 남겨
  나중에 업체 정보·지방보조금·예산 집행 데이터를 같은 연결고리로 붙일 수 있게 합니다.
- 인증키는 코드·저장소에 절대 넣지 않습니다(환경변수만).
- 기관별 파서는 agencies.json 레지스트리로 분리. 한 기관이 깨져도 나머지는 계속 수집하고,
  실패는 meta.json 의 상태표에만 남깁니다.
- 크롤링 예절: 요청 간 지연(기본 0.7초), 명시적 User-Agent, 기관별 페이지 상한.

사용 예
  python3 scripts/fetch_contracts.py                 # 전체 기관(최근 10일 API + 홈페이지)
  python3 scripts/fetch_contracts.py --full          # 첫 적재·정기 전체(최근 28일 API)
  python3 scripts/fetch_contracts.py --agency goe --agency chuncheon
  python3 scripts/fetch_contracts.py --pages 1 --no-api --out data/contracts.json
"""

import argparse
import gzip
import hashlib
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from html import unescape

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(BASE_DIR)
DATA_DIR = os.path.join(ROOT_DIR, "data")
REGISTRY_PATH = os.path.join(BASE_DIR, "agencies.json")

KST = timezone(timedelta(hours=9))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# 화면에 "발주기관명"이 업체명 자리에 들어간 원자료를 걸러내기 위한 토큰
AGENCY_TOKENS = (
    "상하수도사업소", "강원지방조달청", "조달청", "본청", "시청", "군청", "도청",
    "교육청", "교육지원청", "사업소", "직속기관", "읍", "면", "동", "관리과", "과",
)


# ---------------------------------------------------------------- HTTP
class WafBlocked(RuntimeError):
    """지자체 웹방화벽(WAF)이 자동 요청을 차단한 경우. 재시도해도 결과가 같다."""


class Http:
    """재시도(3회, 지수 백오프) + 요청 간 지연을 지키는 최소 HTTP 클라이언트.

    tls_relaxed_hosts: 인증서 체인 오류(중간 인증서 누락·구형 DH 등)로만 접속이 막히는
    기관을 호스트 단위로 지정한다. 여기 없는 호스트는 지금처럼 인증서 검증을 그대로
    적용하고, 검증 실패는 그대로 실패로 남긴다(다른 기관에 영향 없음).
    """

    WAF_MARKERS = (
        "웹방화벽",
        "Web firewall security policies",
        "You don't have permission to access",
    )

    def __init__(self, delay=0.7, timeout=20, retries=3, verbose=True, tls_relaxed_hosts=None):
        self.delay = delay
        self.timeout = timeout
        self.retries = retries
        self.verbose = verbose
        self.tls_relaxed_hosts = {h.lower() for h in (tls_relaxed_hosts or []) if h}
        self.tls_relaxed_used = []     # 이번 수집에서 완화 접속을 실제로 쓴 호스트 기록
        self.last_cert_sha256 = ""
        self._last = 0.0
        self._ctx = self._make_ctx(verify=True)
        self._ctx_noverify = self._make_ctx(verify=False)

    @staticmethod
    def _make_ctx(verify=True):
        """지자체 사이트 상당수가 구형 TLS 암호군을 요구한다(SECLEVEL=1 필요)."""
        ctx = ssl.create_default_context()
        try:
            ctx.set_ciphers("DEFAULT@SECLEVEL=1")
        except ssl.SSLError:
            pass
        if not verify:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return ctx

    def _sleep(self):
        gap = self.delay - (time.time() - self._last)
        if gap > 0:
            time.sleep(gap)
        self._last = time.time()

    def fetch(self, url, data=None, headers=None, encoding=None):
        h = {
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9",
            "Accept-Encoding": "gzip",
            "Connection": "close",
        }
        if headers:
            h.update(headers)
        body = None
        if data is not None:
            if isinstance(data, (str, bytes)):
                # JSON 본문처럼 이미 직렬화된 요청 본문(양구군 jqGrid AJAX 등)
                body = data.encode("utf-8") if isinstance(data, str) else data
                h.setdefault("Content-Type", "application/json; charset=UTF-8")
            else:
                body = urllib.parse.urlencode(data).encode("utf-8")
                h.setdefault("Content-Type", "application/x-www-form-urlencoded; charset=UTF-8")

        host = (urllib.parse.urlsplit(url).hostname or "").lower()
        last_err = None
        for attempt in range(1, self.retries + 1):
            self._sleep()
            try:
                req = urllib.request.Request(url, data=body, headers=h)
                try:
                    resp = urllib.request.urlopen(req, timeout=self.timeout, context=self._ctx)
                except urllib.error.URLError as e:
                    # urllib 은 인증서 오류를 URLError(reason=SSLError)로 감싼다.
                    cause = e.reason if isinstance(e.reason, Exception) else e
                    if not isinstance(cause, ssl.SSLError):
                        raise
                    if host not in self.tls_relaxed_hosts:
                        raise
                    # 지정된 호스트에 한해서만 검증 없이 재시도(공개 조회 페이지, 읽기 전용)
                    resp = urllib.request.urlopen(req, timeout=self.timeout,
                                                  context=self._ctx_noverify)
                    self.tls_relaxed_used.append(host)
                    if self.verbose:
                        print(f"      · TLS 완화 접속({host}): {cause}")
                with resp:
                    self.last_cert_sha256 = self._peer_cert(resp)
                    raw = resp.read()
                    if resp.headers.get("Content-Encoding") == "gzip":
                        raw = gzip.decompress(raw)
                    if encoding:
                        text = raw.decode(encoding, "replace")
                    else:
                        text = None
                        for enc in ("utf-8", "euc-kr", "cp949"):
                            try:
                                text = raw.decode(enc)
                                break
                            except UnicodeDecodeError:
                                continue
                        if text is None:
                            text = raw.decode("utf-8", "replace")
                return check_waf(text, url)
            except WafBlocked:
                raise
            except Exception as e:  # noqa: BLE001 - 어떤 실패든 재시도 후 기록
                # 403 등 HTTP 오류도 본문이 WAF 차단 페이지이면 그렇게 기록한다
                if isinstance(e, urllib.error.HTTPError):
                    try:
                        body = e.read().decode("utf-8", "replace")
                        if any(m in body for m in self.WAF_MARKERS):
                            raise WafBlocked(f"웹방화벽(WAF) 차단(HTTP {e.code}): {url}") from e
                    except WafBlocked:
                        raise
                    except Exception:  # noqa: BLE001
                        pass
                last_err = e
                if self.verbose:
                    print(f"      ! 요청 실패({attempt}/{self.retries}) {e}")
                if attempt < self.retries:
                    time.sleep(min(2 ** attempt * 0.8, 8))
        raise RuntimeError(f"fetch failed: {url} ({last_err})")

    @staticmethod
    def _peer_cert(resp):
        """TLS 피어 인증서 지문(SHA-256). 완화 접속을 쓴 기관을 meta 에 남겨 추적한다."""
        try:
            sock = resp.fp.raw._sock
            cert = sock.getpeercert(binary_form=True)
            if cert:
                return hashlib.sha256(cert).hexdigest()
        except Exception:  # noqa: BLE001
            pass
        return ""


# ---------------------------------------------------------------- helpers
def check_waf(html, url):
    """WAF 차단 페이지를 일반 파싱 실패와 구분해 알린다."""
    if len(html) < 6000 and any(m in html for m in Http.WAF_MARKERS):
        raise WafBlocked(f"웹방화벽(WAF) 차단: {url}")
    return html


def txt(s):
    if s is None:
        return ""
    s = re.sub(r"<script.*?</script>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def money(s):
    if isinstance(s, (int, float)):
        return int(s)
    digits = re.sub(r"[^\d]", "", str(s or ""))
    return int(digits) if digits else 0


def norm_date(s):
    s = txt(s)
    m = re.search(r"(\d{4})[.\-/년]\s*(\d{1,2})[.\-/월]\s*(\d{1,2})", s)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return s


def is_agency_like(name):
    n = (name or "").strip()
    if not n:
        return True
    if n in AGENCY_TOKENS:
        return True
    return any(n == t or (len(n) <= 12 and n.endswith(t)) for t in AGENCY_TOKENS)


def make_id(agency_id, contract_no, title, date, vendor, amount):
    """id는 프로세스가 바뀌어도 같은 값이 나와야 한다(Python hash() 는 실행마다 달라짐)."""
    base = f"{agency_id}|{contract_no or ''}|{title}|{date}|{vendor}|{amount}"
    return f"{agency_id}:{hashlib.md5(base.encode('utf-8')).hexdigest()[:12]}"


# ------------------------------------------------- 확장 뼈대(연결고리)
def vendor_key(name):
    """업체명 정규화 키: 법인격 표기·공백·기호를 걷어낸다.

    나중에 업체 정보(지역·업종)·지방보조금·예산 집행을 같은 키로 붙이는 연결고리.
    """
    s = txt(name)
    if not s:
        return ""
    s = re.sub(r"\(주\)|\(유\)|\(합\)|\(자\)|\(사\)|㈜|주식회사|유한회사|합자회사|합명회사|사단법인|재단법인", "", s)
    s = re.sub(r"[\s\.\,\-\_\\/\(\)\[\]\{\}\"'`·ㆍ]", "", s)
    return s.upper()[:40]


def norm_contract_no(no):
    """계약번호 비교용 정규형(공백·하이픈 제거, 영문 대문자)."""
    return re.sub(r"[^0-9A-Za-z]", "", (no or "")).upper()


def record_keys(r):
    """중복 판정 키 목록. 하나라도 같으면 같은 계약으로 본다.

    1) (기관, 계약번호)          — 두 출처가 같은 계약번호를 쓸 때
    2) (기관, 계약일, 금액, 업체명 앞부분) — 계약번호 형식이 다른 출처끼리
    """
    keys = []
    no = norm_contract_no(r.get("contract_no"))
    if len(no) >= 5:
        keys.append(("no", r.get("agency_id"), no))
    vk = r.get("vendor_key") or vendor_key(r.get("vendor"))
    amt = int(r.get("amount") or 0)
    dt = (r.get("contract_date") or "")[:10]
    if vk and amt >= 10_000 and re.fullmatch(r"\d{4}-\d{2}-\d{2}", dt):
        keys.append(("sig", r.get("agency_id"), dt, amt, vk[:10]))
    return keys


def merge_record(base, extra):
    """같은 계약의 두 출처 레코드 병합(앞의 레코드를 기본으로, 빈 칸만 뒤에서 채움)."""
    out = dict(base)
    for k in ("department", "contract_no", "title", "contract_date", "vendor", "method", "detail_url"):
        if not out.get(k) and extra.get(k):
            out[k] = extra[k]
    if not out.get("amount") and extra.get("amount"):
        out["amount"] = extra["amount"]
    ex = dict(out.get("extra") or {})
    for k, v in (extra.get("extra") or {}).items():
        if k not in ex or (not ex.get(k) and v):
            ex[k] = v
    if ex:
        out["extra"] = ex
    srcs = []
    for s in (out.get("sources") or [out.get("source_type")]) + (extra.get("sources") or [extra.get("source_type")]):
        if s and s not in srcs:
            srcs.append(s)
    if srcs:
        out["sources"] = srcs
        out["source_type"] = "both" if len(srcs) > 1 else srcs[0]
    return out


def title_sim(a, b):
    ta = {w for w in re.findall(r"[0-9A-Za-z가-힣]{2,}", a or "")}
    tb = {w for w in re.findall(r"[0-9A-Za-z가-힣]{2,}", b or "")}
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _sources(r):
    return set(r.get("sources") or [r.get("source_type") or "crawl"])


def dedupe(records):
    """레코드 목록에서 중복 계약을 합친다.

    1) 기관+계약번호가 같은 건은 무조건 같은 계약 → 병합(먼저 온 레코드가 기본).
    2) 계약번호 형식이 다른 두 출처는 (기관+계약일+금액+업체명 앞부분) 서명으로 묶되,
       **그 서명이 양쪽에 하나씩만 있을 때만** 병합한다. 같은 업체가 같은 날 같은 금액으로
       여러 건을 나눠 받은 경우(쪼개기의 핵심 증거)에는 합치지 않는다.
    """
    out, pos_by_no, pos_by_id = [], {}, {}
    for r in records:
        no = norm_contract_no(r.get("contract_no"))
        hit = pos_by_id.get(r.get("id"))
        if hit is None and len(no) >= 5:
            hit = pos_by_no.get((r.get("agency_id"), no))
        if hit is not None:
            out[hit] = merge_record(out[hit], r)
            pos_by_id[out[hit].get("id")] = hit
        else:
            out.append(r)
            pos_by_id[r.get("id")] = len(out) - 1
            if len(no) >= 5:
                pos_by_no[(r.get("agency_id"), no)] = len(out) - 1

    sig_map = {}
    for i, r in enumerate(out):
        vk = r.get("vendor_key") or vendor_key(r.get("vendor"))
        dt = (r.get("contract_date") or "")[:10]
        amt = int(r.get("amount") or 0)
        if not vk or amt < 10_000 or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", dt):
            continue
        sig_map.setdefault((r.get("agency_id"), dt, amt, vk[:10]), []).append(i)

    parent = list(range(len(out)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for idxs in sig_map.values():
        if len(idxs) != 2:
            continue                       # 동일 서명이 여러 건이면 합치지 않는다
        i, j = idxs
        si, sj = _sources(out[i]), _sources(out[j])
        if not (({"crawl"} & si and {"g2b_api"} & sj) or ({"crawl"} & sj and {"g2b_api"} & si)):
            continue                       # 홈페이지와 API, 서로 다른 출처끼리만
        if title_sim(out[i].get("title"), out[j].get("title")) < 0.4:
            continue
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[max(ri, rj)] = min(ri, rj)

    groups = {}
    for i, r in enumerate(out):
        groups.setdefault(find(i), []).append(r)
    merged = []
    for root in sorted(groups):
        rows = groups[root]
        base = rows[0]
        for other in rows[1:]:
            base = merge_record(base, other)
        merged.append(base)
    return merged


def rec(agency_id, category, agency, **kw):
    vendor = (kw.get("vendor") or "").strip()
    stype = kw.get("source_type") or "crawl"
    return {
        "id": make_id(agency_id, kw.get("contract_no"), kw.get("title"),
                      kw.get("contract_date"), vendor, kw.get("amount")),
        "agency_id": agency_id,
        "category": category,          # 시군 / 도청 / 교육청 / 교육지원청 / 학교
        "agency": agency or "",
        "department": kw.get("department") or "",
        "contract_no": (kw.get("contract_no") or "").strip(),
        "title": (kw.get("title") or "").strip(),
        "amount": int(kw.get("amount") or 0),
        "contract_date": kw.get("contract_date") or "",
        "vendor": vendor,
        "vendor_key": vendor_key(vendor),
        "method": kw.get("method") or "",
        "detail_url": kw.get("detail_url") or "",
        "source_type": stype,
        "sources": [stype],
        "extra": kw.get("extra") or {},
    }


# ---------------------------------------------------------------- parsers
def p_chuncheon(ag, http, pages):
    """춘천시 계약정보공개(1인 견적 수의계약). 계약번호는 상세링크의 ctrtAcctBookMngNo."""
    items = []
    base = ag["list_url"].rstrip("/") + "/"
    pat = re.compile(
        r'<li class="serch_sub_result_list sub_rs01"><p>(\d+)</p></li>\s*'
        r'<li class="serch_sub_result_list sub_rs02"><p>(.*?)</p></li>\s*'
        r'<li class="serch_sub_result_list sub_rs03"><p>(.*?)</p></li>\s*'
        r'<li class="serch_sub_result_list sub_rs05"><p>(.*?)</p></li>\s*'
        r'<li class="serch_sub_result_list sub_rs04"><p><a href="(.*?)">(.*?)</a></p></li>\s*'
        r'<li class="serch_sub_result_list sub_rs06"><p>(.*?)</p></li>\s*'
        r'<li class="serch_sub_result_list sub_rs07"><p>(.*?)</p></li>\s*'
        r'<li class="serch_sub_result_list sub_rs08"><p>(.*?)</p></li>',
        re.S)
    for pg in range(1, pages + 1):
        html = http.fetch(f"{base}?pg={pg}")
        found = 0
        for m in pat.finditer(html):
            href = m.group(5).strip()
            mno = re.search(r"ctrtAcctBookMngNo=([0-9A-Za-z\-]+)", href)
            detail = href if href.startswith("http") else "https://www.chuncheon.go.kr" + href
            items.append(rec("chuncheon", "시군", "춘천시",
                             department=txt(m.group(4)),
                             contract_no=(mno.group(1) if mno else m.group(1)),
                             title=txt(m.group(6)),
                             amount=money(m.group(7)),
                             contract_date=norm_date(m.group(8)),
                             vendor=txt(m.group(9)),
                             method="수의계약(1인견적)",
                             detail_url=detail,
                             extra={"구분": txt(m.group(2)), "관서": txt(m.group(3))}))
            found += 1
        print(f"    · 춘천시 {pg}페이지 {found}건")
        if found == 0:
            break
    return items


def p_wonju_table(ag, http, pages):
    """원주시 계약현황 표. 헤더 이름으로 열을 매핑한다.

    원자료 버그: '계약상대자' 칸에 발주기관명(상하수도사업소 등)이 들어간 행이 있다.
    그런 행은 업체명을 비우고 원문값을 extra.원문_계약상대자 에 남긴다(발주처명을 업체명으로
    쓰지 않기 위해).
    """
    items = []
    for pg in range(1, pages + 1):
        url = ag["list_url"].replace("{page}", str(pg))
        html = http.fetch(url)
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S)
        idx = None
        found = 0
        for r in rows:
            cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, re.S)
            plain = [txt(c) for c in cells]
            if idx is None and "계약명" in plain and "계약일" in " ".join(plain):
                idx = {name: i for i, name in enumerate(plain)}
                continue
            if idx is None or len(cells) < 6 or not plain[0].isdigit():
                continue
            raw_vendor = plain[idx.get("계약상대자", 6)] if len(plain) > idx.get("계약상대자", 6) else ""
            href = re.search(r'href="([^"]+)"', r)
            detail = ""
            mno = ""
            if href:
                h = href.group(1).replace("&amp;", "&")
                mno = (re.search(r"cntrctMngNo=([0-9A-Za-z]+)", h) or [None, ""])[1] if re.search(r"cntrctMngNo=", h) else ""
                detail = h if h.startswith("http") else "https://www.wonju.go.kr/www/" + h.lstrip("./")
            vendor = "" if is_agency_like(raw_vendor) else raw_vendor
            items.append(rec("wonju", "시군", ag.get("agency_label", "원주시"),
                             department=plain[idx.get("부서명", 2)],
                             contract_no=mno or plain[0],
                             title=plain[idx.get("계약명", 3)],
                             amount=money(plain[idx.get("계약금액", 4)]),
                             contract_date=norm_date(plain[idx.get("계약일", 5)]),
                             vendor=vendor,
                             method=plain[idx.get("구분", 1)],
                             detail_url=detail,
                             extra=({"원문_계약상대자": raw_vendor} if vendor == "" and raw_vendor else {})))
            found += 1
        print(f"    · 원주시 {pg}페이지 {found}건")
        if found == 0:
            break
    return items


def p_gyeyak(ag, http, pages):
    """계약정보공개시스템(gyeyak) 공통 파서.

    강릉·동해·홍천·횡성·양양·고성·태백이 같은 CMS를 쓴다. ul.ul-body 목록 구조.
    페이지 이동: GET ?p=N
    """
    items = []
    origin = "/".join(ag["list_url"].split("/")[:3])
    for pg in range(1, pages + 1):
        url = ag["list_url"].replace("{page}", str(pg))
        html = http.fetch(url)
        blocks = re.findall(r'<ul class="ul-body">(.*?)</ul>', html, re.S)
        found = 0
        for b in blocks:
            def li(cls):
                m = re.search(r'<li class="%s[^"]*"[^>]*>(.*?)</li>' % cls, b, re.S)
                return m.group(1) if m else ""
            href = re.search(r'href="([^"]+)"', li("title"))
            mno = re.search(r"/contract/post/([0-9A-Za-z]+)", href.group(1)) if href else None
            depts = re.findall(r'<li class="dept[^"]*"[^>]*>(.*?)</li>', b, re.S)
            depts = [txt(d) for d in depts]
            dept = depts[1] if len(depts) > 1 else (depts[0] if depts else "")
            detail = ""
            if href:
                detail = href.group(1)
                if detail.startswith("/"):
                    detail = origin + detail
                elif not detail.startswith("http"):
                    detail = origin + "/" + detail.lstrip("./")
            items.append(rec(ag["id"], ag["category"], ag["name"],
                             department=dept,
                             contract_no=(mno.group(1) if mno else ""),
                             title=txt(li("title")),
                             amount=money(txt(li("amount"))),
                             contract_date=norm_date(li("date")),
                             vendor=txt(li("cust-nm")),
                             method=txt(li("category")),
                             detail_url=detail,
                             extra={}))
            found += 1
        print(f"    · {ag['name']} {pg}페이지 {found}건")
        if found == 0:
            break
    return items


def p_confin_post(ag, http, pages):
    """세입세출 계약정보공개 시스템(도청·평창·양구·영월 계열).

    두 가지 표 모양을 모두 처리한다.
      A) 1행형: 번호 | 발주기관 | 계약명 | 업체명 | 계약일자 | 계약금액 | 계약방법
      B) 2행형(도청): 1행 번호(rowspan=2)·발주기관·계약명(colspan=3),
                       2행 업체명·계약일자·계약금액·계약방법
    페이지 이동: POST pageIndex
    """
    items = []
    base = ag["list_url"]
    origin = "/".join(base.split("/")[:3])
    for pg in range(1, pages + 1):
        html = http.fetch(base, data={"pageIndex": pg})
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S)
        thead = re.search(r"<thead>.*?</thead>", html, re.S)
        hrows = re.findall(r"<tr[^>]*>(.*?)</tr>", thead.group(0), re.S) if thead else []
        single = any(len(re.findall(r"<th", hr)) >= 7 for hr in hrows)
        found = 0
        pending = None
        for r in rows:
            cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, re.S)
            plain = [txt(c) for c in cells]
            if single:
                if len(plain) < 7 or not re.fullmatch(r"\d+", plain[0]):
                    continue
                a = re.search(r'href="([^"]+)"', cells[2])
                href = ""
                if a:
                    h = a.group(1).replace("&amp;", "&")
                    href = h if h.startswith("http") else origin + h
                mno = re.search(r"ctrtAcctBookMngNo=([0-9A-Za-z]+)", href)
                items.append(rec(ag["id"], ag["category"], ag["name"],
                                 department="",
                                 contract_no=(mno.group(1) if mno else plain[0]),
                                 title=plain[2],
                                 amount=money(plain[5]),
                                 contract_date=norm_date(plain[4]),
                                 vendor=plain[3],
                                 method=plain[6],
                                 detail_url=href,
                                 extra={"발주기관": plain[1]}))
                found += 1
                continue
            if pending is not None and len(plain) >= 4:
                href, no, gov, title = pending
                items.append(rec(ag["id"], ag["category"], ag["name"],
                                 department="",
                                 contract_no=no,
                                 title=title,
                                 amount=money(plain[2]),
                                 contract_date=norm_date(plain[1]),
                                 vendor=plain[0],
                                 method=plain[3],
                                 detail_url=href,
                                 extra={"발주기관": gov}))
                pending = None
                found += 1
                continue
            if len(plain) >= 3 and re.fullmatch(r"\d+", plain[0]) and re.search(r'rowspan="?2', r):
                a = re.search(r'href="([^"]+)"', cells[2])
                href = ""
                if a:
                    h = a.group(1).replace("&amp;", "&")
                    href = h if h.startswith("http") else origin + h
                pending = (href, plain[0], plain[1], plain[2])
        print(f"    · {ag['name']} {pg}페이지 {found}건")
        if found == 0:
            break
    return items


def p_table_post_multi(ag, http, pages):
    """헤더 이름으로 열을 매핑하는 범용 표 파서(POST pageIndex).

    ag["list_urls"] 에 (url, 유형라벨) 목록을 두면 여러 게시판(공사/용역/물품)을 한 번에 긁는다.
    사이트마다 열 이름이 조금씩 달라도 ALIAS 로 흡수한다.
    """
    ALIAS = {
        "department": ("계약관서", "관서명", "관서", "발주부서", "부서명", "계약부서", "감독부서"),
        "title": ("계약명", "사업명", "공고명"),
        "amount": ("계약금액", "금액"),
        "contract_date": ("계약일자", "계약일"),
        "method": ("계약방법",),
        "vendor": ("계약업체", "업체명", "계약상대자", "도급업체", "수급업체"),
        "contract_no": ("번호",),
    }
    items = []
    urls = ag.get("list_urls") or [ag["list_url"]]
    origin = "/".join(urls[0][0].split("/")[:3]) if isinstance(urls[0], list) else \
        "/".join(urls[0].split("/")[:3])
    for spec in urls:
        url, label = (spec if isinstance(spec, list) else [spec, ""])
        for pg in range(1, pages + 1):
            html = http.fetch(url, data={"pageIndex": pg})
            rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S)
            idx = None
            found = 0
            for r in rows:
                cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, re.S)
                plain = [txt(c) for c in cells]
                if idx is None and any(a in plain for a in ALIAS["title"]):
                    idx = {}
                    for field, names in ALIAS.items():
                        for n in names:
                            if n in plain:
                                idx[field] = plain.index(n)
                                break
                    continue
                if idx is None or "title" not in idx:
                    continue
                def cell(f):
                    i = idx.get(f)
                    return plain[i] if i is not None and i < len(plain) else ""
                if not cell("title") or not cell("contract_date"):
                    continue
                href = re.search(r'href="([^"]+)"', cells[idx["title"]]) if idx["title"] < len(cells) else None
                detail = ""
                if href:
                    h = href.group(1).replace("&amp;", "&")
                    detail = h if h.startswith("http") else origin + h
                vendor = cell("vendor")
                items.append(rec(ag["id"], ag["category"], ag["name"],
                                 department=cell("department"),
                                 contract_no=cell("contract_no"),
                                 title=cell("title"),
                                 amount=money(cell("amount")),
                                 contract_date=norm_date(cell("contract_date")),
                                 vendor="" if is_agency_like(vendor) else vendor,
                                 method=cell("method"),
                                 detail_url=detail,
                                 extra={"게시판": label} if label else {}))
                found += 1
            print(f"    · {ag['name']}{('·' + label) if label else ''} {pg}페이지 {found}건")
            if found == 0:
                break
    return items


def p_yanggu_ajax(ag, http, pages):
    """양구군 수의계약현황(jqGrid 목록).

    HTML 표가 없고 ajaxOptionalList.do 가 JSON 을 돌려준다. 화면 스크립트
    (js/basis/optionalList.js)가 쓰는 파라미터를 그대로 보낸다.
    """
    items = []
    row_num = int(ag.get("row_num", 50))
    for pg in range(1, pages + 1):
        payload = json.dumps({
            "page": pg, "rowNum": row_num,
            "ctrtGovCd": "", "ctrtNm": "", "custNm": "", "ctrtKindCd": "",
            "ctrtAmtFr": "", "ctrtAmtTo": "", "ctrtYmdFr": "", "ctrtYmdTo": "",
        }, ensure_ascii=False)
        raw = http.fetch(ag["ajax_url"], data=payload, headers={
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Referer": ag.get("referer", ag["ajax_url"]),
        })
        data = json.loads(raw)
        rows = data.get("dataList") or []
        for r in rows:
            ymd = str(r.get("ctrtYmd") or "")
            date = (f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}" if re.fullmatch(r"\d{8}", ymd)
                    else norm_date(ymd))
            vendor = (r.get("custNm") or "").strip()
            no = r.get("ctrtAcctBookMngNo") or ""
            items.append(rec(ag["id"], ag["category"], ag["name"],
                             department=r.get("ctrtGovNm") or "",
                             contract_no=no,
                             title=r.get("ctrtNm") or "",
                             amount=money(r.get("ctrtAmt")),
                             contract_date=date,
                             vendor="" if is_agency_like(vendor) else vendor,
                             method=r.get("ctrtMeth") or "",
                             detail_url=(ag["detail_url"].replace("{no}", no) if ag.get("detail_url") and no else ""),
                             extra={"구분": r.get("ctrtKind") or "",
                                    "사업자등록번호": r.get("bizRegiNo") or "",
                                    "대표자": r.get("repreveHgNm") or "",
                                    "주소": r.get("basicAddr") or ""}))
        print(f"    · {ag['name']} {pg}페이지 {len(rows)}건 / 전체 {data.get('totalCount')}건")
        if len(rows) < row_num:
            break
    return items


def p_goe_keris(ag, http, pages):
    """강원특별자치도교육청 수의계약내역공개(교육지원청·각급 학교 포함).

    목록: 번호·계약기관·계약번호·계약명·계약일자
    금액·업체는 상세페이지에만 있어 detail_limit 건까지 상세를 열어 보강한다.
    """
    items = []
    base = ag["list_url"]
    origin = "/".join(base.split("/")[:3])
    limit = int(ag.get("detail_limit", 60))
    detail_done = 0
    for pg in range(1, pages + 1):
        html = http.fetch(base, data={"pageIndex": pg})
        body = re.search(r"<tbody>(.*?)</tbody>", html, re.S)
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", body.group(1) if body else html, re.S)
        found = 0
        for r in rows:
            cells = re.findall(r"<td[^>]*>(.*?)</td>", r, re.S)
            if len(cells) < 5:
                continue
            inst = txt(cells[1])
            cno = txt(cells[2])
            title = txt(cells[3])
            cdate = norm_date(cells[4])
            a = re.search(r'href="([^"]+)"', cells[3])
            detail = ""
            if a:
                h = a.group(1).replace("&amp;", "&")
                detail = h if h.startswith("http") else origin + h
            if "교육지원청" in inst:
                cat = "교육지원청"
            elif re.search(r"(학교|유치원|분교|특수교육원|교육연수원|도서관)$", inst):
                cat = "학교"
            else:
                cat = "교육청"
            amount, vendor, method, extra = 0, "", "수의계약", {}
            if detail and detail_done < limit:
                try:
                    d = http.fetch(detail)
                    kv = {txt(t): txt(v) for t, v in re.findall(
                        r"<th[^>]*>(.*?)</th>\s*<td[^>]*>(.*?)</td>", d, re.S)}
                    amount = money(kv.get("계약금액", "0"))
                    vendor = kv.get("업체명", "")
                    extra = {
                        "대표자": kv.get("대표자 성명", ""),
                        "주소": kv.get("주소", ""),
                        "계약기간": kv.get("계약기간", ""),
                        "수의계약사유": kv.get("수의계약사유", ""),
                        "사업장소": kv.get("사업장소", ""),
                    }
                    detail_done += 1
                except Exception as e:  # noqa: BLE001
                    print(f"      ! 상세 실패 {cno}: {e}")
            items.append(rec("goe", cat, inst,
                             department=extra.get("사업장소", ""),
                             contract_no=cno,
                             title=title,
                             amount=amount,
                             contract_date=cdate,
                             vendor=vendor,
                             method=method,
                             detail_url=detail,
                             extra=extra))
            found += 1
        print(f"    · 교육청 {pg}페이지 {found}건 (상세 {detail_done}건)")
        if found == 0:
            break
    return items


def p_ihc_api(ag, http, pages):
    """화천군 계약정보시스템(contract.ihc.go.kr) JSON API.

    목록이 JS(Handlebars)로 그려져 정적 HTML 에 표가 없고, /api/situ 가 JSON 을 돌려준다.
    한 쪽에 10건. ctrtAmt 는 "19,323,900 원" 형태 문자열.
    """
    items = []
    per = int(ag.get("row_num", 10))
    for pg in range(1, pages + 1):
        q = urllib.parse.urlencode({
            "fisyear": ag.get("fisyear", ""), "govCd": "", "ctrtNm": "", "custNm": "",
            "sCtrtYmd": "", "eCtrtYmd": "", "ctrtCate": "", "ctrtKind": "",
            "getMethod": "srch", "totalCount": "", "currentPage": pg,
        })
        raw = http.fetch(f"{ag['api_url']}?{q}", headers={
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": ag.get("referer", ag.get("list_url", ag["api_url"])),
        })
        data = json.loads(raw)
        rows = data.get("list") or []
        for r in rows:
            vendor = (r.get("custNm") or "").strip()
            no = r.get("ctrtNo") or ""
            items.append(rec(ag["id"], ag["category"], ag["name"],
                             department=r.get("govNm") or "",
                             contract_no=no,
                             title=r.get("ctrtNm") or "",
                             amount=money(r.get("ctrtAmt")),
                             contract_date=norm_date(r.get("ctrtYmd")),
                             vendor="" if is_agency_like(vendor) else vendor,
                             method=r.get("ctrtType") or "",
                             detail_url=(ag["detail_url"].replace("{no}", no)
                                         if ag.get("detail_url") and no else ""),
                             extra={"계약구분": r.get("ctrtKind") or "",
                                    "원문_계약상대자": vendor}))
        print(f"    · {ag['name']} {pg}페이지 {len(rows)}건 / 전체 {data.get('count')}건")
        if len(rows) < per:
            break
    return items


PARSERS = {
    "yanggu_ajax": p_yanggu_ajax,
    "ihc_api": p_ihc_api,
    "chuncheon": p_chuncheon,
    "wonju_table": p_wonju_table,
    "gyeyak": p_gyeyak,
    "confin_post": p_confin_post,
    "table_post_multi": p_table_post_multi,
    "goe_keris": p_goe_keris,
}


def p_auto(ag, http, pages):
    """URL 후보를 차례로 시험해 표 구조를 자동 판별(사이트 개편 대비)."""
    reasons = []
    for url in ag.get("probe_urls", []):
        try:
            html = http.fetch(url)
        except WafBlocked:
            raise
        except Exception as e:  # noqa: BLE001
            reasons.append(f"{url.split('//')[-1].split('/')[0]}: {str(e)[:80]}")
            continue
        if 'ul class="ul-body"' in html:
            ag2 = dict(ag, list_url=url, parser="gyeyak")
            return p_gyeyak(ag2, http, pages)
        if re.search(r'rowspan="2"', html) and "계약금액" in html:
            ag2 = dict(ag, list_url=url, parser="confin_post")
            return p_confin_post(ag2, http, pages)
        reasons.append(f"{url.split('//')[-1].split('/')[0]}: 알려진 표 구조 없음")
    raise RuntimeError("자동 판별 실패: " + " / ".join(reasons or ["시도한 URL 없음"]))


# ---------------------------------------------------------------- national API (보완 경로)
# 조달청_나라장터 계약정보서비스 (2026-11-10 스펙)
#  - 주소: https://apis.data.go.kr/1230000/ao/CntrctInfoService/<오퍼레이션>
#  - 나라장터검색조건 오퍼레이션(…PPSSrch)은 inqryBgnDate/inqryEndDate(YYYYMMDD)와
#    insttNm(계약기관명, 앞부분 일치)로 서버에서 걸러 준다. 그래서 시군 본청과 그 산하
#    사업소·읍면이 한 번에 잡힌다(예: "강원특별자치도 삼척시" → 삼척시·상수도사업소·근덕면).
#  - 같은 공지로 조회기간 상한이 1개월 → 7일로 줄어, 요청은 7일 창으로 잘라 여러 번 한다.
#  - 응답 JSON: response.body.items (목록), totalCount. 수요기관은 dminsttList 문자열.
G2B_BASE = "https://apis.data.go.kr/1230000/ao/CntrctInfoService"
G2B_OPS = (("물품", "getCntrctInfoListThngPPSSrch"),
           ("공사", "getCntrctInfoListCnstwkPPSSrch"),
           ("용역", "getCntrctInfoListServcPPSSrch"))
G2B_WINDOW_DAYS = 7        # 조회기간 상한(2026-11-10 공지)
G2B_DEFAULT_DAYS = 10      # 일일 수집 창(최근 10일). --full=28, --days, 환경변수 G2B_DAYS 로 조정
G2B_PAGE_ROWS = 500        # 페이지당 결과 수(최대 999)
G2B_MAX_PAGES = 40         # 한 창·한 기관명당 페이지 상한(안전장치)


class G2bError(RuntimeError):
    """나라장터 API가 오류 헤더(resultCode≠00)를 돌려준 경우."""


def g2b_call(key, op, params, http):
    """오퍼레이션 한 번 호출 → (rows, totalCount). 인증키는 환경변수에서만 온다."""
    qs = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    url = f"{G2B_BASE}/{op}?ServiceKey={urllib.parse.quote(key, safe='')}&{qs}"
    raw = http.fetch(url, headers={"Accept": "application/json"}, encoding="utf-8")
    data = json.loads(raw)
    if "response" not in data:
        hdr = {}
        for k in ("nkoneps.com.response.ResponseError", "OpenAPI_ServiceResponse"):
            blob = data.get(k) or {}
            hdr = blob.get("header") or blob.get("cmmMsgHeader") or {}
            if hdr:
                break
        msg = hdr.get("resultMsg") or hdr.get("returnAuthMsg") or "알 수 없는 응답"
        raise G2bError(f"{hdr.get('resultCode', '?')} {msg}")
    body = data["response"].get("body") or {}
    rows = body.get("items") or []
    if isinstance(rows, dict):
        rows = rows.get("item") or []
        if isinstance(rows, dict):
            rows = [rows]
    return rows, int(body.get("totalCount") or 0)


def g2b_vendor(corp_list):
    """corpList 문자열에서 주계약업체 상호. 없으면 첫 업체명."""
    first = ""
    for blob in re.findall(r"\[([^\]]*)\]", corp_list or ""):
        f = blob.split("^")
        if len(f) < 4:
            continue
        role, name = f[1].strip(), f[3].strip()
        if not name:
            continue
        if role == "주계약업체":
            return name
        first = first or name
    return first


def _instt_short(instt):
    return re.sub(r"^강원특별자치도\s*", "", (instt or "").strip())


GWD_TOWN_PREFIXES = (
    "춘천시", "원주시", "강릉시", "동해시", "태백시", "속초시", "삼척시",
    "홍천군", "횡성군", "영월군", "평창군", "정선군", "철원군", "화천군",
    "양구군", "인제군", "고성군", "양양군",
)
SCHOOL_SUFFIXES = ("초등학교", "중학교", "고등학교", "유치원", "특수학교", "분교장")


def g2b_agency(ag, instt):
    """나라장터 계약기관명 → (기관 표시명, 구분). 레지스트리 kind 에 따라 다릅니다."""
    kind = (ag.get("narajangter") or {}).get("kind") or "town"
    instt = (instt or "").strip()
    label = (ag.get("narajangter") or {}).get("agency_label") or ag.get("name") or ""
    if kind == "edu":
        parts = instt.split()
        if len(parts) >= 3:
            school = parts[-1]
            return school, ("학교" if school.endswith(SCHOOL_SUFFIXES) else "교육기관")
        if len(parts) == 2:
            second = parts[1]
            if "교육지원청" in second:
                return second.replace("강원특별자치도", "").strip(), "교육지원청"
            return second, ("학교" if second.endswith(SCHOOL_SUFFIXES) else "교육기관")
        return label, "교육청"
    if kind == "province":
        return label, "도청"
    return label, "시군"


def g2b_skip(ag, instt):
    """도청 질의는 '강원특별자치도'로 시작하는 모든 기관을 잡는다.

    시군·교육청 몫은 각자 질의에서 수집하므로 여기서는 버린다(기관별 중복 방지).
    """
    kind = (ag.get("narajangter") or {}).get("kind") or "town"
    if kind != "province":
        return False
    instt = (instt or "").strip()
    if instt.startswith("강원특별자치도교육청"):
        return True
    return any(instt.startswith("강원특별자치도 " + t) for t in GWD_TOWN_PREFIXES)


def g2b_record(ag, cat, r):
    """API 응답 1건 → 앱 표준 레코드. 금액은 금차계약금액(없으면 총계약금액)."""
    amount = money(r.get("thtmCntrctAmt")) or money(r.get("totCntrctAmt"))
    instt = txt(r.get("cntrctInsttNm") or "")
    agency, category = g2b_agency(ag, instt)
    return rec(ag["id"], category, agency,
               department=txt(r.get("cntrctInsttChrgDeptNm") or ""),
               contract_no=r.get("untyCntrctNo") or "",
               title=txt(r.get("cntrctNm") or r.get("cnstwkNm") or ""),
               amount=amount,
               contract_date=norm_date(r.get("cntrctCnclsDate") or r.get("cntrctDate") or ""),
               vendor=g2b_vendor(r.get("corpList")),
               method=r.get("cntrctCnclsMthdNm") or "",
               detail_url=r.get("cntrctDtlInfoUrl") or "",
               source_type="g2b_api",
               extra={"계약기관": _instt_short(instt),
                      "계약구분": r.get("bsnsDivNm") or cat,
                      "공고번호": r.get("ntceNo") or "",
                      "총계약금액": money(r.get("totCntrctAmt")),
                      "장기계속": r.get("lngtrmCtnuDivNm") or ""})


def fetch_narajangter(key, ag, days=None, verbose=False):
    """한 기관의 나라장터 계약을 최근 N일치 조회한다(7일 창으로 잘라 호출).

    레지스트리의 narajangter.instt_nm(계약기관 정식명, 앞부분 일치)으로 거슬러 호출한다.
    반환: (레코드 목록, 수집 정보 dict)
    """
    names = [n for n in (ag.get("narajangter") or {}).get("instt_nm", []) if n]
    if not names:
        raise G2bError("레지스트리에 narajangter.instt_nm 이 없습니다")
    days = max(int(days or os.environ.get("G2B_DAYS") or G2B_DEFAULT_DAYS), 1)
    http = Http(delay=0.25, timeout=40, retries=3, verbose=verbose)
    today = datetime.now(KST).date()
    start = today - timedelta(days=days - 1)
    items, by_cat, seen, calls, skipped = [], {}, set(), 0, 0
    window_end = today
    while window_end >= start:
        window_bgn = max(start, window_end - timedelta(days=G2B_WINDOW_DAYS - 1))
        for cat, op in G2B_OPS:
            for instt_nm in names:
                for page in range(1, G2B_MAX_PAGES + 1):
                    rows, total = g2b_call(key, op, {
                        "numOfRows": G2B_PAGE_ROWS, "pageNo": page, "type": "json",
                        "inqryDiv": 1,                      # 1 = 계약체결일자
                        "inqryBgnDate": window_bgn.strftime("%Y%m%d"),
                        "inqryEndDate": window_end.strftime("%Y%m%d"),
                        "insttNm": instt_nm,
                    }, http)
                    calls += 1
                    for r in rows:
                        if g2b_skip(ag, r.get("cntrctInsttNm")):
                            skipped += 1
                            continue
                        item = g2b_record(ag, cat, r)
                        if not item["title"] or item["id"] in seen:
                            continue
                        seen.add(item["id"])
                        items.append(item)
                        by_cat[cat] = by_cat.get(cat, 0) + 1
                    if not rows or page * G2B_PAGE_ROWS >= total:
                        break
                print(f"    · API {cat} {instt_nm} {window_bgn}~{window_end}: "
                      f"누적 {by_cat.get(cat, 0)}건")
        window_end = window_bgn - timedelta(days=1)
    info = {"period": [start.isoformat(), today.isoformat()],
            "by_category": by_cat, "calls": calls, "instt_nm": names}
    if skipped:
        info["skipped_other_agency"] = skipped
    return items, info


# ---------------------------------------------------------------- main
def load_existing(path):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d.get("contracts", []) if isinstance(d, dict) else d
    except Exception:  # noqa: BLE001
        return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--agency", action="append", default=None,
                    help="기관 id (여러 번 지정 가능). 생략하면 전체")
    ap.add_argument("--pages", type=int, default=None, help="기관별 페이지 수 덮어쓰기")
    ap.add_argument("--out", default=os.path.join(DATA_DIR, "contracts.json"))
    ap.add_argument("--reset", action="store_true", help="기존 수집분을 합치지 않고 새로 씀")
    ap.add_argument("--days", type=int, default=None, help="나라장터 API 조회 일수(기본 10, --full 은 28)")
    ap.add_argument("--full", action="store_true", help="나라장터 API 4주치(28일) 조회 — 첫 적재·정기 전체")
    ap.add_argument("--no-api", action="store_true", help="나라장터 API 없이 홈페이지만")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    with open(REGISTRY_PATH, encoding="utf-8") as f:
        registry = json.load(f)
    defaults = registry.get("defaults", {})
    agencies_all = registry["agencies"]
    agencies = agencies_all
    if args.agency:
        wanted = set(args.agency)
        agencies = [a for a in agencies if a["id"] in wanted]
        missing = wanted - {a["id"] for a in agencies}
        if missing:
            print(f"! 레지스트리에 없는 기관 id: {', '.join(sorted(missing))}")

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    old = [] if args.reset else load_existing(args.out)
    for r in old:                      # 이전 수집분에 확장 필드를 보강(하위 호환)
        r.setdefault("vendor_key", vendor_key(r.get("vendor")))
        r.setdefault("source_type", "crawl")
        if not r.get("sources"):
            r["sources"] = ["crawl", "g2b_api"] if r["source_type"] == "both" else [r["source_type"]]
    print(f"[카르텔워치] 기존 수집분 {len(old)}건 로드")

    base_http = Http(delay=defaults.get("delay_sec", 0.7), timeout=defaults.get("timeout_sec", 20),
                     retries=defaults.get("retries", 3), verbose=args.verbose)

    api_key = os.environ.get("DATA_GO_KR_KEY", "").strip()
    days = 28 if args.full else int(args.days or os.environ.get("G2B_DAYS") or G2B_DEFAULT_DAYS)
    all_new, status, g2b_runs = [], [], []
    for ag in agencies:
        pages = args.pages or ag.get("pages") or defaults.get("pages", 3)
        print(f"\n▶ {ag['name']} ({ag['id']}, {ag['category']}) · 최대 {pages}페이지")
        t0 = time.time()
        st = {"id": ag["id"], "name": ag["name"], "category": ag["category"],
              "parser": ag.get("parser", "auto"),
              "url": ag.get("list_url") or (ag.get("probe_urls") or [""])[0],
              "note": ag.get("note", ""),
              "status": "실패", "count": 0, "crawl_count": 0, "api_count": 0,
              "pages": pages,
              "collected_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
              "error": ""}
        rows_crawl = []
        try:
            http = Http(delay=ag.get("delay_sec", base_http.delay),
                        timeout=ag.get("timeout_sec", defaults.get("timeout_sec", 20)),
                        retries=ag.get("retries", defaults.get("retries", 3)), verbose=args.verbose,
                        tls_relaxed_hosts=ag.get("tls_relaxed_hosts"))
            parser = ag.get("parser", "auto")
            fn = p_auto if parser == "auto" else PARSERS[parser]
            rows_crawl = fn(ag, http, pages)
            if http.tls_relaxed_used:
                st["tls"] = {"relaxed_hosts": sorted(set(http.tls_relaxed_used)),
                             "cert_sha256": http.last_cert_sha256}
                print(f"    · TLS 인증서 검증 완화 사용: {', '.join(sorted(set(http.tls_relaxed_used)))}"
                      f" (지문 {http.last_cert_sha256[:16]}…)")
            rows_crawl = [r for r in rows_crawl if r["title"]]
            st["crawl_count"] = len(rows_crawl)
            st["status"] = "성공" if rows_crawl else "수집 0건"
            st["count"] = len(rows_crawl)
        except WafBlocked as e:
            st["error"] = f"{e} (자동 요청 차단 — IP 기반으로 보임, 우회하지 않음)"[:300]
            print(f"    ✗ {ag['name']} {e}")
        except Exception as e:  # noqa: BLE001 - 기관 하나 실패로 전체 중단 금지
            st["error"] = str(e)[:300]
            print(f"    ✗ {ag['name']} 실패: {e}")
        all_new.extend(rows_crawl)

        # ── 나라장터 API: 전 기관 공통(하이브리드 합산). 키가 있을 때만.
        if api_key and not args.no_api and ag.get("narajangter"):
            print(f"    ↻ {ag['name']} 나라장터 API 합산 수집({days}일)")
            try:
                rows_api, info = fetch_narajangter(api_key, ag, days=days, verbose=args.verbose)
                st["g2b"] = info
                st["api_count"] = len(rows_api)
                all_new.extend(rows_api)
                if rows_api or rows_crawl:
                    st["status"] = "성공"
                st["count"] = len(rows_crawl) + len(rows_api)
                st["source"] = ("홈페이지 + 나라장터 API" if rows_crawl and rows_api
                                else "나라장터 API" if rows_api else "홈페이지")
                if not rows_crawl and rows_api:
                    st["crawl_error"] = st.get("error") or ""
                    st["error"] = ""
                    st["parser"] = f"g2b_api(+{st.get('parser')})"
                    st["note"] = ("나라장터 API 수집(" + " ~ ".join(info["period"]) + ") · "
                                  + "·".join(f"{k} {v}건" for k, v in info["by_category"].items()))
                print(f"    ✓ 나라장터 API {len(rows_api)}건 (홈페이지 {len(rows_crawl)}건)")
                g2b_runs.append({"id": ag["id"], "name": ag["name"],
                                 "count": len(rows_api), "info": info, "error": ""})
            except Exception as e:  # noqa: BLE001
                st["g2b_error"] = str(e)[:300]
                print(f"    ! 나라장터 API 수집 실패: {e}")
                g2b_runs.append({"id": ag["id"], "name": ag["name"], "count": 0,
                                 "info": {}, "error": str(e)[:300]})
        st["elapsed"] = round(time.time() - t0, 1)
        status.append(st)

    # 나라장터 API 결과 요약(상태표 한 줄)
    if g2b_runs:
        ok = [g for g in g2b_runs if not g["error"]]
        status.append({"id": "narajangter", "name": f"나라장터 API({len(g2b_runs)}곳 합산)", "category": "전체",
                       "parser": "g2b_api", "url": G2B_BASE,
                       "status": "성공" if ok and sum(g["count"] for g in ok) else "실패",
                       "count": sum(g["count"] for g in g2b_runs), "pages": 1,
                       "collected_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
                       "note": f"조회 {len(g2b_runs)}곳 · 최근 {days}일 · "
                               + ", ".join(f"{g['name']} {g['count']}건" for g in g2b_runs if g["count"]),
                       "error": "; ".join(f"{g['name']}: {g['error']}" for g in g2b_runs if g["error"])[:300],
                       "elapsed": 0})
        print("\n▶ 나라장터 API: " +
              ", ".join(f"{g['name']} {g['count']}건" for g in g2b_runs))
    elif not api_key:
        print("\n· DATA_GO_KR_KEY 없음 → 홈페이지 크롤링만 수행합니다(정상 동작).")

    # ── 누적 병합 + 중복 제거 (이번 수집분 우선, 두 출처는 하나로 병합)
    today_s = datetime.now(KST).strftime("%Y-%m-%d")
    for r in all_new:
        r["_fresh"] = True
    records = dedupe(all_new + old)
    for r in records:
        if r.pop("_fresh", False):
            r.setdefault("first_seen", today_s)
            r["last_seen"] = today_s
    contracts = sorted(records, key=lambda x: (x.get("contract_date", ""), x.get("amount", 0)),
                       reverse=True)

    # 실패만 있고 새 데이터가 없으면 기존 파일을 건드리지 않는다
    ok_count = sum(1 for s in status if s["status"] == "성공")
    if all_new == [] and old:
        print("\n! 이번 수집분이 0건이라 기존 contracts.json 을 그대로 둡니다.")
    else:
        src_count = {"crawl": 0, "g2b_api": 0, "both": 0}
        EXTRA_KEEP = {"원문_계약상대자", "구분", "관서", "계약기관"}
        for r in contracts:
            src_count[r.get("source_type") or "crawl"] = src_count.get(r.get("source_type") or "crawl", 0) + 1
            # 빈 칸은 파일에서 덜어 용량을 줄인다(화면은 ?? 로 대체)
            for k in ("department", "method", "detail_url", "contract_no", "vendor_key", "extra", "first_seen", "sources"):
                if k in r and r[k] in ("", {}, None):
                    r.pop(k, None)
            ex = r.get("extra")
            if isinstance(ex, dict):
                ex = {k: v for k, v in ex.items() if k in EXTRA_KEEP and v}
                if ex:
                    r["extra"] = ex
                else:
                    r.pop("extra", None)
        payload = {
            "generated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
            "source": "live" if ok_count else "sample",
            "total": len(contracts),
            "merged_this_run": len(all_new),
            "by_source": src_count,
            "contracts": contracts,
        }
        tmp = args.out + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, args.out)
        print(f"\n[저장] {args.out} · 총 {len(contracts)}건 "
              f"(이번 수집 {len(all_new)}건 → 중복 병합 후 누적) · 출처 {src_count}")

    # 기관별 수집 상태 요약표
    api_total = sum(1 for c in contracts if "g2b_api" in (c.get("sources") or [c.get("source_type")]))
    for s in status:
        if s["id"] == "narajangter":
            s["count"] = api_total

    # meta.json (수집 상태표) — 기존 상태와 합쳐 기관별 최근 상태 유지
    meta_path = os.path.join(os.path.dirname(args.out), "meta.json")
    try:
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
    except Exception:  # noqa: BLE001
        meta = {}
    prev_status = {s["id"]: s for s in meta.get("agencies", [])}
    for s in status:
        prev_status[s["id"]] = s
    # 이번에 성공한 기관이 없더라도, 직전 수집이 실데이터였으면 상태를 유지한다
    # (한 기관만 재수집하다 실패해 앱이 '샘플 데이터'로 표시되는 것을 막는다)
    source = "live" if ok_count else (meta.get("source") or "sample")
    # 원자료 연도 오타(2226년·2029년 등)는 데이터 기간 계산에서 제외
    def _ok_date(v):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v or ""):
            return False
        if not 2000 <= int(v[:4]) <= 2035:
            return False
        # 계약일자는 미래일 수 없다(원본 입력 오류). 오늘+31일을 넘으면 기간 계산에서 뺀다.
        try:
            d = datetime.strptime(v, "%Y-%m-%d").date()
        except ValueError:
            return False
        return d <= datetime.now(KST).date() + timedelta(days=31)
    dates = [c.get("contract_date", "") for c in contracts if _ok_date(c.get("contract_date", ""))]
    src_total = {"crawl": 0, "g2b_api": 0, "both": 0}
    amt_total = 0
    for c in contracts:
        k = c.get("source_type") or "crawl"
        src_total[k] = src_total.get(k, 0) + 1
        amt_total += int(c.get("amount") or 0)
    meta.update({
        "generated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
        "total": len(contracts),
        "amount_total": amt_total,
        "by_source": src_total,
        "api_window_days": days,
        "source": source,
        "period": {"from": min(dates) if dates else "", "to": max(dates) if dates else ""},
        "agencies": sorted(prev_status.values(), key=lambda s: (s["category"], s["name"])),
        "registry": [{"id": a["id"], "name": a["name"], "category": a["category"]}
                     for a in agencies_all],
    })
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)

    print("\n=== 기관별 수집 상태 ===")
    for s in status:
        print(f"  {s['status']:<8} {s['name']:<16} {s['count']:>4}건 "
              f"({s['elapsed']}s) {s['error'][:60]}")
    fails = [s for s in status if s["status"] != "성공"]
    if fails:
        print(f"\n실패/0건 {len(fails)}곳: " + ", ".join(f"{s['name']}({s['status']})" for s in fails))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
카르텔워치 이상징후 탐지기 (CartelWatch detector)
=================================================
data/contracts.json 을 읽어 4대 탐지 결과를 data/anomalies.json 으로 만듭니다.

탐지 유형 (임계치는 아래 상수에서 한눈에 바꿀 수 있습니다)
 1) SPLIT          쪼개기 수의계약  : 같은 부서·같은 업체가 7일 안에 2건 이상,
                                      건별 2천만 원 미만 + 합산 2천만 원 이상
 2) CONCENTRATION  일감 몰아주기    : 부서(없으면 기관) 내 한 업체 점유율 35% 초과 & 3건 이상
 3) NEWCOMER       신생법인 특혜     : 데이터에 처음 등장한 업체가 5천만 원 이상 수주
 (설립일 데이터가 없어 '데이터 첫 등장' 기준입니다. 카드에 한계를 명시합니다.)
 4) YEAR_END       연말 예산 밀어내기: 12/20~12/31 집행 건이 기관 3건 이상 & 금액 비중 25% 초과

탐지 결과는 '소재 후보'입니다. 사실관계·위법성 판단은 취재로 확인해야 합니다.
"""

import json
import os
import re
from collections import defaultdict
from datetime import datetime, date, timedelta, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(BASE_DIR)
DATA = os.path.join(ROOT_DIR, "data")
KST = timezone(timedelta(hours=9))

# ---------------------------------------------------------------- 임계치
SPLIT_WINDOW_DAYS = 7
SPLIT_MIN_COUNT = 2
SPLIT_MAX_UNIT = 20_000_000        # 건별 2천만 원 미만(소액수의 한도 회피 의심)
SPLIT_MIN_TOTAL = 20_000_000       # 합산 2천만 원 이상이면 카드로 올림
SPLIT_SIMILARITY_MIN = 0.45        # 계약명 유사도

CONCENTRATION_MIN_SHARE = 0.35     # 부서 내 점유율 35% 초과
CONCENTRATION_MIN_COUNT = 3
CONCENTRATION_MIN_AMOUNT = 50_000_000

NEWCOMER_MIN_AMOUNT = 100_000_000
NEWCOMER_MAX_APPEARANCES = 1
NEWCOMER_MAX_CARDS = 25

YEAR_END_START = (12, 20)
YEAR_END_MIN_COUNT = 3
YEAR_END_MIN_SHARE = 0.25

MAX_CONTRACTS_PER_CARD = 12


def load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return default


def josa(word, with_batchim, without_batchim):
    """받침 유무에 따라 조사를 고른다(예: 한성토건'이' / (주)다우'가')."""
    w = (word or "").strip()
    if not w:
        return without_batchim
    # 끝의 괄호·기호는 건너뛰고 마지막 한글 글자로 판단한다(예: "정화건설(합)")
    ch = ""
    for c in reversed(w):
        if "가" <= c <= "힣":
            ch = c
            break
        if c.isalnum():
            return without_batchim
    if not ch:
        return without_batchim
    return with_batchim if (ord(ch) - 0xAC00) % 28 else without_batchim


def won(n):
    if n >= 100_000_000:
        return f"{n/100_000_000:.1f}억 원"
    if n >= 10_000:
        return f"{n/10_000:,.0f}만 원"
    return f"{n:,}원"


def parse_date(s):
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except Exception:  # noqa: BLE001
        return None


def vendor_key(name):
    """업체명 정규화 키(수집기의 vendor_key 와 같은 규칙). 표기 차이로 탐지가 갈리지 않게."""
    s = re.sub(r"\(주\)|\(유\)|\(합\)|\(자\)|\(사\)|㈜|주식회사|유한회사|합자회사|합명회사|사단법인|재단법인", "", name or "")
    s = re.sub(r"[\s\.\,\-\_\\/\(\)\[\]\{\}\"'`·ㆍ]", "", s)
    return s.upper()[:40]


def tokens(title):
    t = re.sub(r"[\(\[]?[0-9]+차[\)\]]?", " ", title)
    t = re.sub(r"[^0-9A-Za-z가-힣 ]", " ", t)
    return {w for w in t.split() if len(w) >= 2}


def similarity(a, b):
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb) + 0.3 * (len(ta & tb) > 0)


def slim(c, **extra):
    d = {
        "id": c["id"],
        "agency_id": c.get("agency_id", ""),
        "agency": c.get("agency", ""),
        "department": c.get("department", ""),
        "contract_no": c.get("contract_no", ""),
        "title": c.get("title", ""),
        "amount": c.get("amount", 0),
        "contract_date": c.get("contract_date", ""),
        "vendor": c.get("vendor", ""),
        "detail_url": c.get("detail_url", ""),
        "source_type": c.get("source_type", "crawl"),
    }
    d.update(extra)
    return d


def main():
    payload = load(os.path.join(DATA, "contracts.json"), {"contracts": []})
    raw = payload.get("contracts", [])
    contracts, bad_dates = [], 0
    for c in raw:
        if not c.get("contract_date"):
            continue
        d = parse_date(c["contract_date"])
        if not d or not (2000 <= d.year <= 2035):   # 원자료 오타(2226년 등)는 통계에서 제외
            bad_dates += 1
            continue
        contracts.append(c)
    anomalies = []

    by_agency = defaultdict(list)
    for c in contracts:
        by_agency[c.get("agency", "")].append(c)

    # ---------------- 1) 쪼개기
    buckets = defaultdict(list)
    for c in contracts:
        d = parse_date(c["contract_date"])
        if not d:
            continue
        if c.get("amount", 0) >= SPLIT_MAX_UNIT:
            continue
        vk = c.get("vendor_key") or vendor_key(c.get("vendor"))
        key = (c.get("agency_id") or c.get("agency"), c.get("department") or "", vk)
        buckets[key].append((d, c))

    seen_sets = set()
    for (aid, dept, _vk), items in buckets.items():
        agency = items[0][1].get("agency", "")
        vendor = items[0][1].get("vendor") or ""
        items.sort(key=lambda x: x[0])
        for i, (d0, c0) in enumerate(items):
            window = [c for d, c in items[i:] if (d - d0).days <= SPLIT_WINDOW_DAYS]
            if len(window) < SPLIT_MIN_COUNT:
                continue
            # 계약명이 비슷한 것끼리 묶는다(다른 사업 2건을 쪼개기로 오인하지 않도록)
            cluster = [c0] + [c for c in window[1:] if similarity(c0["title"], c["title"]) >= SPLIT_SIMILARITY_MIN]
            total = sum(c["amount"] for c in cluster)
            if len(cluster) < SPLIT_MIN_COUNT or total < SPLIT_MIN_TOTAL:
                continue
            sig = tuple(sorted(c["id"] for c in cluster))
            if sig in seen_sets:
                continue
            seen_sets.add(sig)
            anomalies.append({
                "id": "split-" + "-".join(sig[:2])[:40],
                "type": "SPLIT",
                "type_label": "쪼개기 수의계약",
                "severity": "상" if total >= SPLIT_MIN_TOTAL * 3 else "중",
                "title": f"{agency} {dept or ''} · {cluster[0]['title'][:40]}",
                "vendor": vendor,
                "agency": agency,
                "department": dept,
                "amount": total,
                "count": len(cluster),
                "summary": (f"{vendor or '업체 미상'}{josa(vendor, '이', '가')} {SPLIT_WINDOW_DAYS}일 안에 {len(cluster)}건을 "
                            f"나눠 받음 · 합산 {won(total)} (건별 최대 {won(max(c['amount'] for c in cluster))})"),
                "threshold": (f"같은 부서·같은 업체 / {SPLIT_WINDOW_DAYS}일 이내 / 건별 {won(SPLIT_MAX_UNIT)} 미만 & "
                              f"합산 {won(SPLIT_MIN_TOTAL)} 이상 / 계약명 유사도 {SPLIT_SIMILARITY_MIN} 이상"),
                "facts": {"합산금액": total, "건수": len(cluster),
                          "건별금액": sorted((c["amount"] for c in cluster), reverse=True),
                          "기간": f"{min(c['contract_date'] for c in cluster)} ~ {max(c['contract_date'] for c in cluster)}"},
                "checks": [
                    "지방계약법 시행령 제77조(분할계약 금지) 위반 여부",
                    "각 건의 계약 사유·견적 수(1인 견적 여부)",
                    "동일 사업을 나눈 근거가 있는지(예산 절감·현장 여건) 발주부서 해명",
                ],
                "contracts": [slim(c) for c in sorted(cluster, key=lambda x: x["contract_date"])][:MAX_CONTRACTS_PER_CARD],
            })

    # ---------------- 2) 일감 몰아주기 (부서 단위 + 기관 단위)
    for agency, rows in by_agency.items():
        total_amt = sum(r["amount"] for r in rows)
        if total_amt <= 0:
            continue
        dept_totals = defaultdict(int)
        dept_counts = defaultdict(int)
        for r in rows:
            dept_totals[r.get("department") or "-"] += r["amount"]
            dept_counts[r.get("department") or "-"] += 1

        def vendor_agg(key_fn):
            agg = defaultdict(lambda: {"amount": 0, "count": 0, "rows": []})
            for r in rows:
                if not r.get("vendor"):
                    continue
                k = key_fn(r)
                agg[k]["amount"] += r["amount"]
                agg[k]["count"] += 1
                agg[k]["rows"].append(r)
            return agg

        scopes = []
        # 기관 단위
        for vk, a in vendor_agg(lambda r: vendor_key(r["vendor"])).items():
            scopes.append(("기관", "-", vk, a, total_amt, len(rows),
                           CONCENTRATION_MIN_COUNT))
        # 부서 단위 (계약이 충분한 부서만)
        for (dept, vk), a in vendor_agg(lambda r: (r.get("department") or "-", vendor_key(r["vendor"]))).items():
            if dept == "-" or dept_counts[dept] < 5:
                continue
            scopes.append(("부서", dept, vk, a, dept_totals[dept], dept_counts[dept],
                           CONCENTRATION_MIN_COUNT))

        used = set()
        for scope, dept, vk, agg, base, base_count, min_count in scopes:
            if base <= 0:
                continue
            share = agg["amount"] / base
            if share <= CONCENTRATION_MIN_SHARE:
                continue
            if agg["count"] < min_count or agg["amount"] < CONCENTRATION_MIN_AMOUNT:
                continue
            if (dept, vk) in used:
                continue
            used.add((dept, vk))
            vendor = max({r["vendor"] for r in agg["rows"]},
                         key=lambda n: sum(1 for r in agg["rows"] if r["vendor"] == n))
            label = dept if scope == "부서" else agency + "(기관 전체)"
            anomalies.append({
                "id": f"conc-{agency}-{dept}-{vendor}"[:60],
                "type": "CONCENTRATION",
                "type_label": "일감 몰아주기",
                "severity": "상" if share >= 0.6 else "중",
                "title": f"{label} · {vendor} 점유율 {share*100:.1f}%",
                "vendor": vendor,
                "agency": agency,
                "department": "" if scope == "기관" else dept,
                "amount": agg["amount"],
                "count": agg["count"],
                "summary": (f"{label} 계약 {won(base)} 중 {vendor}{josa(vendor, '이', '가')} {won(agg['amount'])} "
                            f"({share*100:.1f}%, {agg['count']}건 / 모집단 {base_count}건)"),
                "threshold": (f"{scope} 내 한 업체 점유율 {int(CONCENTRATION_MIN_SHARE*100)}% 초과 & "
                              f"{min_count}건 이상 & {won(CONCENTRATION_MIN_AMOUNT)} 이상"),
                "facts": {"모집단 금액": base, "모집단 건수": base_count, "업체 금액": agg["amount"],
                          "점유율": f"{share*100:.1f}%", "건수": agg["count"]},
                "checks": [
                    "해당 업체의 다른 지자체·부서 수주 이력(지역·부서 편중)",
                    "업체 대표·주소·설립일과 발주부서 공무원과의 관계(이해충돌)",
                    "경쟁 업체가 입찰하지 못한 이유(자격 제한·지역 제한·견적 미요청)",
                ],
                "contracts": [slim(r) for r in sorted(agg["rows"], key=lambda x: x["amount"], reverse=True)][:MAX_CONTRACTS_PER_CARD],
            })

    # ---------------- 3) 신생법인 특혜(데이터 첫 등장 기준)
    v_rows = defaultdict(list)
    for c in contracts:
        if c.get("vendor"):
            v_rows[c.get("vendor_key") or vendor_key(c["vendor"])].append(c)
    for vk, rows in v_rows.items():
        rows.sort(key=lambda x: x["contract_date"])
        if len(rows) > NEWCOMER_MAX_APPEARANCES:
            continue
        first = rows[0]
        vendor = first.get("vendor") or ""
        if first.get("amount", 0) >= NEWCOMER_MIN_AMOUNT:
            anomalies.append({
                "id": f"new-{vendor}"[:60],
                "type": "NEWCOMER",
                "type_label": "신생법인 특혜(의심)",
                "severity": "하",
                "title": f"{first['agency']} · {vendor} 첫 등장 {won(first['amount'])}",
                "vendor": vendor,
                "agency": first["agency"],
                "department": first.get("department", ""),
                "amount": first["amount"],
                "count": len(rows),
                "summary": (f"수집된 계약 중 이 업체의 첫 계약이 {first['contract_date']} "
                            f"{won(first['amount'])} · 전체 등장 {len(rows)}건"),
                "threshold": (f"수집 데이터 내 등장 {NEWCOMER_MAX_APPEARANCES}건 이하 & 첫 계약 "
                              f"{won(NEWCOMER_MIN_AMOUNT)} 이상 (설립일 데이터 없음 → '데이터 첫 등장' 기준)"),
                "facts": {"첫 계약일": first["contract_date"], "첫 계약금액": first["amount"],
                          "등장 건수": len(rows)},
                "checks": [
                    "법인등기부·사업자등록증으로 설립일 확인(국세청 사업자 상태 조회)",
                    "설립 후 첫 수주까지 걸린 기간과 입찰 참여 자격",
                    "대표자·주소가 기존 수주 업체와 겹치는지(페이퍼컴퍼니 의심)",
                ],
                "contracts": [slim(r) for r in rows][:MAX_CONTRACTS_PER_CARD],
            })

    # ---------------- 4) 연말 예산 밀어내기
    for agency, rows in by_agency.items():
        if len(rows) < YEAR_END_MIN_COUNT:
            continue
        total_amt = sum(r["amount"] for r in rows)
        ye = []
        for r in rows:
            d = parse_date(r["contract_date"])
            if d and (d.month, d.day) >= YEAR_END_START:
                ye.append(r)
        if len(ye) >= YEAR_END_MIN_COUNT and total_amt > 0:
            share = sum(r["amount"] for r in ye) / total_amt
            if share >= YEAR_END_MIN_SHARE:
                anomalies.append({
                    "id": f"ye-{agency}"[:60],
                    "type": "YEAR_END",
                    "type_label": "연말 예산 밀어내기",
                    "severity": "중",
                    "title": f"{agency} 12월 하순 집행 {share*100:.1f}%",
                    "vendor": "",
                    "agency": agency,
                    "department": "",
                    "amount": sum(r["amount"] for r in ye),
                    "count": len(ye),
                    "summary": (f"12/20~12/31 집행 {len(ye)}건 {won(sum(r['amount'] for r in ye))} "
                                f"= 수집분의 {share*100:.1f}%"),
                    "threshold": (f"12월 20일 이후 집행 {YEAR_END_MIN_COUNT}건 이상 & 금액 비중 "
                                  f"{int(YEAR_END_MIN_SHARE*100)}% 초과"),
                    "facts": {"연말 집행 건수": len(ye), "연말 집행 금액": sum(r["amount"] for r in ye),
                              "수집분 총액": total_amt},
                    "checks": [
                        "해당 예산의 편성·집행 계획 대비 12월 집행률",
                        "불용액 회피를 위한 긴급 발주 여부(계약 사유·발주일)",
                        "이월·변경 계약 내역",
                    ],
                    "contracts": [slim(r) for r in sorted(ye, key=lambda x: x["amount"], reverse=True)][:MAX_CONTRACTS_PER_CARD],
                })

    order = {"상": 0, "중": 1, "하": 2}
    anomalies.sort(key=lambda a: (order.get(a["severity"], 3), -a["amount"]))
    # 신생법인 후보는 금액 상위 NEWCOMER_MAX_CARDS 개만 남긴다(수집 기간이 짧을 때 과다 노출 방지)
    new_cards = [a for a in anomalies if a["type"] == "NEWCOMER"][:NEWCOMER_MAX_CARDS]
    anomalies = [a for a in anomalies if a["type"] != "NEWCOMER"] + new_cards
    anomalies.sort(key=lambda a: (order.get(a["severity"], 3), -a["amount"]))

    # 카드마다 기관 id를 붙인다(화면의 기관 버튼 필터가 바로 쓸 수 있게)
    for a in anomalies:
        if not a.get("agency_id"):
            a["agency_id"] = (a.get("contracts") or [{}])[0].get("agency_id", "")

    counts = defaultdict(int)
    for a in anomalies:
        counts[a["type"]] += 1
    amounts = defaultdict(int)
    for a in anomalies:
        amounts[a["type"]] += a["amount"]

    # 기관별 미리 계산한 요약(화면 버튼 배지가 즉시 뜨도록)
    meta = load(os.path.join(DATA, "meta.json"), {})
    reg = {r.get("id"): r for r in meta.get("registry", [])}
    status_by_id = {s.get("id"): s for s in meta.get("agencies", [])}
    order_ids = [r["id"] for r in meta.get("registry", [])]
    order_ids += sorted({c.get("agency_id", "") for c in contracts} - set(order_ids))
    by_agency = []
    for aid in order_ids:
        if not aid:
            continue
        rows = [c for c in contracts if c.get("agency_id") == aid]
        ants = [a for a in anomalies if a.get("agency_id") == aid]
        t_counts = defaultdict(int)
        for a in ants:
            t_counts[a["type"]] += 1
        by_agency.append({
            "id": aid,
            "name": reg.get(aid, {}).get("name") or (rows[0].get("agency", "") if rows else aid),
            "category": reg.get(aid, {}).get("category") or (rows[0].get("category", "") if rows else ""),
            "contracts": len(rows),
            "amount": sum(int(c.get("amount") or 0) for c in rows),
            "anomalies": len(ants),
            "by_type": dict(t_counts),
            "status": (status_by_id.get(aid) or {}).get("status", ""),
            "source": (status_by_id.get(aid) or {}).get("source", ""),
        })

    out = {
        "generated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
        "base_generated_at": payload.get("generated_at", ""),
        "source": payload.get("source", "live"),
        "thresholds": {
            "SPLIT_WINDOW_DAYS": SPLIT_WINDOW_DAYS,
            "SPLIT_MIN_COUNT": SPLIT_MIN_COUNT,
            "SPLIT_MAX_UNIT": SPLIT_MAX_UNIT,
            "SPLIT_MIN_TOTAL": SPLIT_MIN_TOTAL,
            "SPLIT_SIMILARITY_MIN": SPLIT_SIMILARITY_MIN,
            "CONCENTRATION_MIN_SHARE": CONCENTRATION_MIN_SHARE,
            "CONCENTRATION_MIN_COUNT": CONCENTRATION_MIN_COUNT,
            "CONCENTRATION_MIN_AMOUNT": CONCENTRATION_MIN_AMOUNT,
            "NEWCOMER_MIN_AMOUNT": NEWCOMER_MIN_AMOUNT,
            "NEWCOMER_MAX_APPEARANCES": NEWCOMER_MAX_APPEARANCES,
            "YEAR_END_START": "12-20",
            "YEAR_END_MIN_COUNT": YEAR_END_MIN_COUNT,
            "YEAR_END_MIN_SHARE": YEAR_END_MIN_SHARE,
        },
        "summary": {"total": len(anomalies), "by_type": dict(counts), "amount_by_type": dict(amounts),
                    "contracts": len(contracts),
                    "contract_amount": sum(int(c.get("amount") or 0) for c in contracts),
                    "agencies": len([b for b in by_agency if b["contracts"]]),
                    "by_agency": by_agency},
        "contract_stats": {
            "total": len(contracts),
            "bad_dates": bad_dates,
            "with_amount": sum(1 for c in contracts if c.get("amount")),
            "with_vendor": sum(1 for c in contracts if c.get("vendor")),
            "period": (
                min((c["contract_date"] for c in contracts), default=""),
                max((c["contract_date"] for c in contracts), default=""),
            ),
        },
        "anomalies": anomalies,
    }
    os.makedirs(DATA, exist_ok=True)
    path = os.path.join(DATA, "anomalies.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"[저장] {path}")
    print(f"  계약 {len(contracts)}건 → 이상징후 후보 {len(anomalies)}건")
    for t, label in (("SPLIT", "쪼개기"), ("CONCENTRATION", "몰아주기"), ("NEWCOMER", "신생법인"), ("YEAR_END", "연말집행")):
        print(f"   · {label:<6} {counts.get(t, 0):>3}건 / {won(amounts.get(t, 0))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

/* ── 카르텔워치 앱 (프레임워크 없이) ────────────────────────────────── */
(() => {
  "use strict";

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const won = n => (n >= 100000000
    ? (n / 100000000).toFixed(1) + "억 원"
    : n >= 10000 ? Math.round(n / 10000).toLocaleString("ko-KR") + "만 원"
      : (n || 0).toLocaleString("ko-KR") + "원");
  const comma = n => (n || 0).toLocaleString("ko-KR");
  const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const today = () => new Date().toISOString().slice(0, 10);
  // 원자료에 연도 오타(예: 2226-10-27)가 섞여 있어 통계·정렬에서 걸러낸다.
  const validDate = d => {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(d || "")) return false;
    const y = Number(d.slice(0, 4));
    return y >= 2000 && y <= 2035;
  };
  const uid = () => "c" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);

  /* ── 상태 ─────────────────────────────────────────────── */
  const STAGE_LABEL = { review: "검토 전", reporting: "취재 중", factcheck: "팩트체크", published: "발행" };
  const STAGE_SHORT = { review: "검토", reporting: "취재", factcheck: "팩트", published: "발행" };
  const STAGES = ["review", "reporting", "factcheck", "published"];

  const TYPE_ORDER = ["SPLIT", "CONCENTRATION", "NEWCOMER", "YEAR_END"];
  const TYPE_META = {
    SPLIT: { label: "쪼개기 수의계약", icon: "🧩", cls: "t-split", desc: "같은 부서·같은 업체가 짧은 기간에 소액으로 나눠 받은 계약" },
    CONCENTRATION: { label: "일감 몰아주기", icon: "🎯", cls: "t-conc", desc: "한 업체가 특정 부서(또는 기관) 일감을 과도하게 차지" },
    NEWCOMER: { label: "신생법인 특혜(의심)", icon: "🌱", cls: "t-new", desc: "수집 데이터에 처음 등장한 업체가 큰 금액을 수주(설립일 확인 필요)" },
    YEAR_END: { label: "연말 예산 밀어내기", icon: "🗓", cls: "t-year", desc: "12월 하순에 집중된 집행(예산 소진·불용액 회피 의심)" },
  };
  const TYPE_LABEL = Object.fromEntries(TYPE_ORDER.map(t => [t, TYPE_META[t].label]));
  const TYPE_DESC = Object.fromEntries(TYPE_ORDER.map(t => [t, TYPE_META[t].desc]));

  // 시군 버튼 순서(춘천에서 시계방향) — 레지스트리 순서와 무관하게 보기 좋게 고정
  const CITY_ORDER = ["chuncheon", "wonju", "gangneung", "donghae", "taebaek", "samcheok",
    "sokcho", "yangyang", "goseong", "inje", "yanggu", "hwacheon", "cheorwon", "hoengseong",
    "hongcheon", "pyeongchang", "jeongseon", "yeongwol"];
  const OFFICE_ORDER = ["gwd", "goe"];
  const CAT_LABEL = { 시군: "시군", 도청: "도청", 교육청: "교육청", 교육지원청: "교육지원청", 학교: "학교", 교육기관: "교육기관" };

  const FACT_TEMPLATE = [
    {
      group: "1. 계약 원문",
      items: [
        { id: "f1", t: "계약서·과업지시서 원문 확보", d: "전자계약 또는 문서 사본, 계약 상대자·금액·기간 대조" },
        { id: "f2", t: "계약번호·금액·계약일자 원문 대조", d: "공개 페이지 값과 계약 원장이 일치하는지" },
        { id: "f3", t: "분할계약 여부 확인(지방계약법 시행령 제77조)", d: "같은 사업을 나눈 근거가 있는지" },
        { id: "f4", t: "계약방법·견적 수 확인", d: "1인 견적/2인 이상 견적, 견적서 수취 내역" },
      ],
    },
    {
      group: "2. 예산 편성 근거",
      items: [
        { id: "f5", t: "예산 편성·집행 내역(본예산·추경)", d: "세부사업 단위 편성액과 집행률" },
        { id: "f6", t: "불용액·이월 내역", d: "연말 집행·이월 사유" },
        { id: "f7", t: "사업 필요성 근거 자료", d: "계획서·설명자료·의회 보고 자료" },
      ],
    },
    {
      group: "3. 업체 실체",
      items: [
        { id: "f8", t: "법인등기부(설립일·대표·주소)", d: "신생법인 의심 카드의 핵심 확인" },
        { id: "f9", t: "사업자등록 상태·업종 적합성", d: "휴·폐업 여부, 면허·등록 요건" },
        { id: "f10", t: "유사·동일 계약 이력(타 지자체 포함)", d: "수의계약 반복 수주 여부" },
        { id: "f11", t: "대표자·주소가 다른 수주업체와 중복", d: "페이퍼컴퍼니·가족회사 의심" },
      ],
    },
    {
      group: "4. 발주처 대응",
      items: [
        { id: "f12", t: "발주부서 해명 요청(공문·구두)", d: "선정 사유, 견적 요청 대상, 사업 분할 이유" },
        { id: "f13", t: "내부 결재문서 확보(정보공개청구)", d: "기안·협조·결재선" },
        { id: "f14", t: "담당자 이해충돌·전출입 관계 확인", d: "업체와의 관계, 가족·지인 여부" },
        { id: "f15", t: "감사·지적 사항 이력", d: "자체감사·상급기관 감사 결과" },
      ],
    },
    {
      group: "5. 데이터 검증",
      items: [
        { id: "f16", t: "원문 URL·조회 시각 박제", d: "화면 캡처와 URL, 수집 시각 함께 보관" },
        { id: "f17", t: "금액 단위·오기 확인", d: "원 단위/천원 단위, 오타(연도 2226 등)" },
        { id: "f18", t: "비교 기준(타 시군·전국) 확보", d: "같은 기간 유사 규모 지자체와 비교" },
      ],
    },
  ];

  const store = {
    get(k, d) { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } },
    set(k, v) { localStorage.setItem(k, JSON.stringify(v)); },
  };
  const K_BOARD = "cw:board:v1", K_FACT = "cw:fact:v1";

  let DATA = { contracts: [], anomalies: [], meta: {}, thresholds: {}, source: "live", sample: false, byAgency: [] };
  let board = store.get(K_BOARD, []);
  let fact = store.get(K_FACT, {});
  let filter = { q: "", agencyId: "", dept: "", vendor: "", from: "", to: "", amin: "", amax: "", sort: "date-desc" };
  let page = 1;
  const PAGE_SIZE = 40;
  let activeType = "";
  let anomalyAgency = "";
  const expandedTypes = new Set();
  let activeFact = null;

  /* ── 기관 목록 ────────────────────────────────────────── */
  function agencyList() {
    const reg = (DATA.meta && DATA.meta.registry) || [];
    if (reg.length) return reg.map(a => ({ id: a.id, name: a.name, category: a.category }));
    const seen = new Map();
    DATA.contracts.forEach(c => {
      if (!c.agency_id) return;
      if (!seen.has(c.agency_id)) seen.set(c.agency_id, { id: c.agency_id, name: c.agency, category: c.category });
    });
    return [...seen.values()];
  }
  function agencyName(id) {
    const a = agencyList().find(x => x.id === id);
    return a ? a.name : (id || "");
  }
  function agencyStats() {
    const map = new Map();
    agencyList().forEach(a => map.set(a.id, { count: 0, amount: 0, anomalies: 0, status: "" }));
    DATA.contracts.forEach(c => {
      const s = map.get(c.agency_id);
      if (!s) return;
      s.count++;
      s.amount += c.amount || 0;
    });
    DATA.anomalies.forEach(a => {
      const s = map.get(a.agency_id);
      if (s) s.anomalies++;
    });
    (DATA.meta.agencies || []).forEach(st => {
      const s = map.get(st.id);
      if (s) s.status = st.status || "";
    });
    return map;
  }

  /* ── 데이터 로드 ──────────────────────────────────────── */
  async function json(url) {
    // GitHub Pages CDN이 같은 파일을 10분간 캐시하므로, 배포 직후에도 최신 JSON을 보도록 쿼리로 구분한다.
    const sep = url.includes("?") ? "&" : "?";
    const r = await fetch(url + sep + "t=" + Math.floor(Date.now() / 60000), { cache: "no-store" });
    if (!r.ok) throw new Error(url + " " + r.status);
    return r.json();
  }

  async function loadData() {
    try {
      const [meta, c, a] = await Promise.all([
        json("data/meta.json").catch(() => ({})),
        json("data/contracts.json"),
        json("data/anomalies.json").catch(() => ({ anomalies: [] })),
      ]);
      DATA.meta = meta;
      DATA.contracts = (c && c.contracts) || [];
      DATA.anomalies = a.anomalies || [];
      DATA.thresholds = a.thresholds || {};
      DATA.byAgency = (a.summary && a.summary.by_agency) || [];
      DATA.summary = (a && a.summary) || {};
      DATA.source = (c && c.source) || meta.source || "live";
      DATA.generatedAt = (c && c.generated_at) || meta.generated_at || "";
      DATA.bySource = (c && c.by_source) || meta.by_source || {};
      DATA.sample = DATA.source === "sample";
    } catch (err) {
      // 실제 수집 파일이 없을 때만 샘플로 뜬다(화면에 '샘플' 배지)
      try {
        const s = await json("data/sample_contracts.json");
        DATA.contracts = s.contracts || [];
        DATA.anomalies = s.anomalies || [];
        DATA.meta = s.meta || {};
        DATA.source = "sample";
        DATA.sample = true;
        DATA.generatedAt = s.generated_at || "";
      } catch (e2) {
        console.error(err, e2);
        toast("데이터를 불러오지 못했습니다. data/ 폴더를 확인하세요.");
      }
    }
    renderAll();
  }

  /* ── 화면 전환 ────────────────────────────────────────── */
  const TITLES = { contracts: "계약 데이터", anomalies: "이상징후", board: "소재 보드", factcheck: "팩트체크" };
  function go(screen) {
    $$(".nav-item").forEach(b => b.classList.toggle("active", b.dataset.screen === screen));
    $$(".screen").forEach(s => s.classList.toggle("active", s.id === "screen-" + screen));
    $("#screen-title").textContent = TITLES[screen];
    if (screen === "board") renderBoard();
    if (screen === "factcheck") renderFactList();
    window.scrollTo({ top: 0 });
  }

  function toast(msg) {
    const t = $("#toast");
    t.textContent = msg;
    t.hidden = false;
    clearTimeout(t._h);
    t._h = setTimeout(() => { t.hidden = true; }, 2400);
  }

  /* ── 렌더: 전체 ───────────────────────────────────────── */
  function renderAll() {
    renderMeta();
    renderStats();
    renderTypeCards();
    renderAgencyChips();
    renderTable();
    renderStatus();
    renderAnomalies();
    renderAnomalyChips();
    renderBoard();
    renderFactList();
    updateNavCounts();
  }

  function renderMeta() {
    const src = DATA.sample ? "sample" : DATA.source;
    const badge = $("#badge-source");
    badge.className = "badge " + (src === "sample" ? "sample" : src === "mixed" ? "mixed" : "");
    badge.textContent = src === "sample" ? "샘플 데이터" : src === "mixed" ? "실데이터 + 샘플" : "실데이터";
    const bs = DATA.bySource || {};
    $("#source-badge").textContent = src === "sample"
      ? "샘플 데이터로 표시 중"
      : `홈페이지+API ${comma(DATA.contracts.length)}건`;

    $("#meta-total").innerHTML = `총 <strong>${comma(DATA.contracts.length)}</strong>건`;
    const g = DATA.generatedAt || DATA.meta.generated_at || "";
    $("#meta-collected").textContent = g ? `최근 수집 ${g}` : "수집 시각 없음";
    const dates = DATA.contracts.map(c => c.contract_date).filter(validDate).sort();
    const p = DATA.meta.period || {};
    const from = p.from || dates[0] || "–", to = p.to || dates[dates.length - 1] || "–";
    $("#meta-period").textContent = `데이터 기간 ${from} ~ ${to}`;
    $("#nav-count-contracts").textContent = comma(DATA.contracts.length);
    $("#nav-count-anomalies").textContent = DATA.anomalies.length;
    $("#list-scope").textContent = filter.agencyId ? `${agencyName(filter.agencyId)}만 보는 중` : "";
  }

  function renderStats() {
    const total = DATA.contracts.length;
    const amount = DATA.contracts.reduce((s, c) => s + (c.amount || 0), 0);
    const ids = new Set(agencyList().map(a => a.id));
    const ok = (DATA.meta.agencies || []).filter(s => ids.has(s.id) && s.status === "성공").length;
    const totalAgencies = ids.size || 20;
    $("#stat-total").textContent = comma(total) + "건";
    $("#stat-amount").textContent = won(amount);
    $("#stat-agencies").textContent = `${ok} / ${totalAgencies}`;
    $("#stat-anomalies").textContent = comma(DATA.anomalies.length) + "건";
    const bs = DATA.bySource || {};
    $("#stat-source").textContent = `홈페이지 ${comma(bs.crawl || 0)} · 나라장터 ${comma(bs.g2b_api || 0)} · 양쪽 ${comma(bs.both || 0)}`;
    const p = DATA.meta.period || {};
    $("#stat-period").textContent = p.from ? `${p.from} ~ ${p.to} 수집분` : "수집 기간 미상";
  }

  function typeCounts(list) {
    const c = {};
    TYPE_ORDER.forEach(t => (c[t] = 0));
    (list || DATA.anomalies).forEach(a => { c[a.type] = (c[a.type] || 0) + 1; });
    return c;
  }
  function typeAmounts(list) {
    const c = {};
    (list || DATA.anomalies).forEach(a => { c[a.type] = (c[a.type] || 0) + (a.amount || 0); });
    return c;
  }

  function renderTypeCards() {
    const counts = typeCounts();
    const amounts = typeAmounts();
    $("#type-cards").innerHTML = TYPE_ORDER.map(t => {
      const m = TYPE_META[t];
      return `<button class="type-card ${m.cls}" data-type="${t}" title="${esc(m.desc)}">
        <span class="tc-top"><span class="tc-ico">${m.icon}</span><span class="tc-name">${esc(m.label)}</span><span class="tc-go">보기 →</span></span>
        <span class="tc-count">${comma(counts[t] || 0)}</span>
        <span class="tc-amt">${(counts[t] || 0) ? won(amounts[t] || 0) : "임계치를 넘은 후보 없음"}</span>
      </button>`;
    }).join("");
  }

  /* ── 기관 버튼 ────────────────────────────────────────── */
  function chipHTML(a, stats, active, small) {
    const s = stats.get(a.id) || { count: 0, anomalies: 0, status: "" };
    const on = active === a.id ? " on" : "";
    const fail = s.status && s.status !== "성공" ? " st-fail" : "";
    return `<button class="achip${on}${fail}" data-agency="${a.id}" title="${esc(a.name)}${s.status ? " · " + esc(s.status) : ""}">
      <span class="n-name">${esc(a.name)}</span>
      <span class="n-meta"><span>${comma(s.count)}건</span><span class="b ${s.anomalies ? "" : "zero"}">${s.anomalies ? "이상 " + s.anomalies : "0"}</span></span>
    </button>`;
  }

  function renderAgencyChips() {
    const list = agencyList();
    const stats = agencyStats();
    const offices = OFFICE_ORDER.map(id => list.find(a => a.id === id)).filter(Boolean);
    const cities = CITY_ORDER.map(id => list.find(a => a.id === id)).filter(Boolean)
      .concat(list.filter(a => !CITY_ORDER.includes(a.id) && !OFFICE_ORDER.includes(a.id) && a.category === "시군"));
    const totalAll = { count: DATA.contracts.length, anomalies: DATA.anomalies.length, status: "성공" };

    $("#office-chips").innerHTML =
      `<button class="achip${filter.agencyId === "" ? " on" : ""}" data-agency="">
         <span class="n-name">전체</span>
         <span class="n-meta"><span>${comma(totalAll.count)}건</span><span class="b">이상 ${comma(totalAll.anomalies)}</span></span>
       </button>` +
      offices.map(a => {
        const s = stats.get(a.id) || { count: 0, anomalies: 0 };
        return `<button class="achip${filter.agencyId === a.id ? " on" : ""}" data-agency="${a.id}">
          <span class="n-name">${esc(a.name)}</span>
          <span class="n-meta"><span>${comma(s.count)}건</span><span class="b ${s.anomalies ? "" : "zero"}">${s.anomalies ? "이상 " + s.anomalies : "0"}</span></span>
        </button>`;
      }).join("");

    $("#agency-chips").innerHTML = cities.map(a => chipHTML(a, stats, filter.agencyId)).join("");
  }

  function renderAnomalyChips() {
    const list = agencyList();
    const stats = agencyStats();
    const offices = OFFICE_ORDER.map(id => list.find(a => a.id === id)).filter(Boolean);
    const cities = CITY_ORDER.map(id => list.find(a => a.id === id)).filter(Boolean)
      .concat(list.filter(a => !CITY_ORDER.includes(a.id) && !OFFICE_ORDER.includes(a.id) && a.category === "시군"));

    $("#anomaly-office-chips").innerHTML =
      `<button class="achip${anomalyAgency === "" ? " on" : ""}" data-af="">
         <span class="n-name">전체 기관</span>
         <span class="n-meta"><span>${comma(DATA.anomalies.length)}건</span></span>
       </button>` +
      offices.map(a => {
        const s = stats.get(a.id) || { anomalies: 0 };
        return `<button class="achip${anomalyAgency === a.id ? " on" : ""}" data-af="${a.id}">
          <span class="n-name">${esc(a.name)}</span>
          <span class="n-meta"><span class="b ${s.anomalies ? "" : "zero"}">${s.anomalies ? "이상 " + s.anomalies : "0"}</span></span>
        </button>`;
      }).join("");

    $("#anomaly-agency-chips").innerHTML = cities.map(a => {
      const s = stats.get(a.id) || { anomalies: 0 };
      return `<button class="achip${anomalyAgency === a.id ? " on" : ""}" data-af="${a.id}">
        <span class="n-name">${esc(a.name)}</span>
        <span class="n-meta"><span class="b ${s.anomalies ? "" : "zero"}">${s.anomalies ? "이상 " + s.anomalies : "0"}</span></span>
      </button>`;
    }).join("");

    $("#anomaly-scope-label").textContent = anomalyAgency ? `${agencyName(anomalyAgency)} 이상징후만` : "전체 기관";
  }

  /* ── 렌더: 계약 표 ────────────────────────────────────── */
  function filtered() {
    const q = filter.q.trim().toLowerCase();
    let rows = DATA.contracts.filter(c => {
      if (filter.agencyId && c.agency_id !== filter.agencyId) return false;
      if (filter.dept && !(c.department || "").toLowerCase().includes(filter.dept.toLowerCase())) return false;
      if (filter.vendor && !(c.vendor || "").toLowerCase().includes(filter.vendor.toLowerCase())) return false;
      if (filter.from && (c.contract_date || "") < filter.from) return false;
      if (filter.to && (c.contract_date || "") > filter.to) return false;
      if (filter.amin && (c.amount || 0) < Number(filter.amin)) return false;
      if (filter.amax && (c.amount || 0) > Number(filter.amax)) return false;
      if (q) {
        const hay = [c.title, c.vendor, c.contract_no, c.agency, c.department].join(" ").toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
    const s = filter.sort;
    rows.sort((a, b) => {
      const av = validDate(a.contract_date), bv = validDate(b.contract_date);
      if (av !== bv) return av ? -1 : 1;              // 날짜 이상치는 항상 맨 뒤로
      if (s === "amount-desc") return (b.amount || 0) - (a.amount || 0);
      if (s === "amount-asc") return (a.amount || 0) - (b.amount || 0);
      if (s === "date-asc") return (a.contract_date || "").localeCompare(b.contract_date || "");
      return (b.contract_date || "").localeCompare(a.contract_date || "") || (b.amount || 0) - (a.amount || 0);
    });
    return rows;
  }

  function renderTable() {
    const rows = filtered();
    const total = rows.reduce((s, c) => s + (c.amount || 0), 0);
    $("#list-count").textContent = comma(rows.length) + "건";
    $("#list-sum").textContent = "합계 " + won(total);
    $("#list-scope").textContent = filter.agencyId ? `${agencyName(filter.agencyId)}만 보는 중` : "";
    const maxPage = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
    if (page > maxPage) page = maxPage;
    const slice = rows.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);
    const tb = $("#tbl-contracts tbody");
    if (!slice.length) {
      tb.innerHTML = `<tr><td colspan="9" class="empty-row">조건에 맞는 계약이 없습니다. 필터를 풀어 보세요.</td></tr>`;
    } else {
      tb.innerHTML = slice.map(c => `
        <tr>
          <td><span class="tag ${esc(c.category)}">${esc(c.category || "-")}</span></td>
          <td>${esc(c.agency || "-")}</td>
          <td class="sub">${esc(c.department || "-")}</td>
          <td>${esc(c.contract_date || "-")}${validDate(c.contract_date) ? "" : ' <span class="pill" title="원자료 연도 오타로 보입니다">⚠</span>'}</td>
          <td class="title">
            <span class="t" title="${esc(c.title)}">${esc(c.title)}</span>
            ${c.method ? `<span class="sub">${esc(c.method)}</span>` : ""}
          </td>
          <td>${esc(c.vendor || "-")}${c.extra && c.extra["원문_계약상대자"] ? `<span class="sub">원문: ${esc(c.extra["원문_계약상대자"])}</span>` : ""}</td>
          <td class="num amt">${comma(c.amount)}</td>
          <td class="sub">${esc(c.contract_no || "-")}</td>
          <td>${c.detail_url ? `<a class="src" href="${esc(c.detail_url)}" target="_blank" rel="noopener">열기 ↗</a>` : '<span class="pill">-</span>'}</td>
        </tr>`).join("");
    }
    const pager = $("#pager-contracts");
    const btns = [];
    btns.push(`<button ${page === 1 ? "disabled" : ""} data-p="${page - 1}">‹ 이전</button>`);
    const start = Math.max(1, Math.min(page - 3, maxPage - 6));
    for (let p = start; p <= Math.min(maxPage, start + 6); p++) btns.push(`<button class="${p === page ? "on" : ""}" data-p="${p}">${p}</button>`);
    btns.push(`<button ${page === maxPage ? "disabled" : ""} data-p="${page + 1}">다음 ›</button>`);
    pager.innerHTML = btns.join("");
    pager.onclick = e => {
      const b = e.target.closest("button[data-p]");
      if (!b || b.disabled) return;
      page = Number(b.dataset.p);
      renderTable();
      $(".table-wrap").scrollTop = 0;
    };
  }

  /* ── 렌더: 기관별 상태 ────────────────────────────────── */
  function renderStatus() {
    const list = (DATA.meta.agencies || []).slice();
    // 교육지원청은 교육청 수집분에서 갈라서 표시
    const sub = {};
    DATA.contracts.filter(c => c.category === "교육지원청").forEach(c => {
      sub[c.agency] = sub[c.agency] || { count: 0, last: "" };
      sub[c.agency].count++;
      if ((c.contract_date || "") > sub[c.agency].last) sub[c.agency].last = c.contract_date;
    });
    Object.entries(sub).forEach(([name, v]) => {
      list.push({ id: "goe:" + name, name, category: "교육지원청", status: "성공", count: v.count, collected_at: DATA.generatedAt || "", note: "교육청 수의계약내역공개 + 나라장터에서 함께 수집" });
    });
    const ok = list.filter(s => s.status === "성공").length;
    $("#status-summary").textContent = `성공 ${ok} / 전체 ${list.length}곳`;
    $("#tbl-status tbody").innerHTML = list.map(s => `
      <tr>
        <td><span class="tag ${esc(s.category)}">${esc(s.category)}</span></td>
        <td>${esc(s.name)}</td>
        <td class="st-${esc(String(s.status).replace(/\s/g, ""))}">${esc(s.status)}</td>
        <td class="num">${comma(s.count)}</td>
        <td class="sub">${esc(s.collected_at || "-")}</td>
        <td class="sub">${esc(s.note || s.error || s.crawl_error || "")}</td>
      </tr>`).join("") || '<tr><td colspan="6" class="empty-row">수집 상태 정보가 없습니다. scripts/fetch_contracts.py를 실행하세요.</td></tr>';
  }

  /* ── 렌더: 이상징후 ───────────────────────────────────── */
  function anomalyInScope(a) {
    if (anomalyAgency && a.agency_id !== anomalyAgency) return false;
    return true;
  }

  function renderAnomalies() {
    const scoped = DATA.anomalies.filter(anomalyInScope);
    const byType = {};
    scoped.forEach(a => (byType[a.type] = byType[a.type] || []).push(a));
    const total = scoped.length;
    const scopeName = anomalyAgency ? agencyName(anomalyAgency) : "";
    $("#anomaly-headline").textContent = total
      ? `${scopeName ? scopeName + " " : ""}계약 ${comma(DATA.contracts.length)}건에서 소재 후보 ${comma(total)}건을 찾았습니다. 카드에서 바로 소재 보드로 담으세요.`
      : `${scopeName ? scopeName + "에서는 " : ""}이번 수집분에서 임계치를 넘는 후보가 없습니다.`;

    $("#type-chips").innerHTML =
      `<button class="type-chip ${activeType === "" ? "on" : ""}" data-type="">전체 <b>${comma(total)}</b></button>` +
      TYPE_ORDER.map(t => `
      <button class="type-chip ${activeType === t ? "on" : ""}" data-type="${t}">
        ${esc(TYPE_LABEL[t])} <b>${comma((byType[t] || []).length)}</b>
      </button>`).join("");

    const html = TYPE_ORDER.filter(t => !activeType || t === activeType).map(t => {
      const cards = (byType[t] || []);
      const shown = expandedTypes.has(t) ? cards : cards.slice(0, 12);
      const rest = cards.length - shown.length;
      return `
        <section class="group">
          <div class="group-head">
            <h3>${esc(TYPE_META[t].icon)} ${esc(TYPE_LABEL[t])}</h3>
            <span class="desc">${esc(TYPE_DESC[t])}</span>
            <span class="pill">${cards.length}건 · ${won(cards.reduce((s, a) => s + (a.amount || 0), 0))}</span>
          </div>
          <div class="acards">
            ${cards.length ? shown.map(a => anomalyCard(a)).join("")
        : `<div class="zero-state">이번 수집분에서 임계치를 넘는 후보가 없습니다. 기준: ${esc(thresholdText(t))}</div>`}
          </div>
          ${rest > 0 ? `<div class="group-more"><button data-more="${t}">${esc(TYPE_LABEL[t])} ${comma(rest)}건 더 보기</button></div>` : ""}
        </section>`;
    }).join("");
    $("#anomaly-groups").innerHTML = html;
  }

  function thresholdText(t) {
    const th = DATA.thresholds || {};
    if (t === "SPLIT") return `같은 부서·업체 / ${th.SPLIT_WINDOW_DAYS || 7}일 이내 / 건별 ${won(th.SPLIT_MAX_UNIT || 20000000)} 미만 & 합산 ${won(th.SPLIT_MIN_TOTAL || 20000000)} 이상`;
    if (t === "CONCENTRATION") return `부서(또는 기관) 내 점유율 ${((th.CONCENTRATION_MIN_SHARE || .35) * 100).toFixed(0)}% 초과 & ${th.CONCENTRATION_MIN_COUNT || 3}건 이상`;
    if (t === "NEWCOMER") return `수집 데이터 첫 등장 ${won(th.NEWCOMER_MIN_AMOUNT || 100000000)} 이상`;
    return `12월 20일 이후 집행 ${th.YEAR_END_MIN_COUNT || 3}건 이상 & 금액 비중 ${((th.YEAR_END_MIN_SHARE || .25) * 100).toFixed(0)}% 초과`;
  }

  function anomalyCard(a) {
    const cls = a.severity === "상" ? "상" : a.severity === "중" ? "중" : "하";
    const facts = Object.entries(a.facts || {}).map(([k, v]) => {
      let val = v;
      if (Array.isArray(v)) val = v.map(x => comma(x)).join(" / ");
      else if (typeof v === "number" && k.match(/금액|총액/)) val = won(v);
      else if (typeof v === "number") val = comma(v);
      return `<div class="fact">${esc(k)}<b>${esc(val)}</b></div>`;
    }).join("");
    return `
      <article class="acard card sev-${cls}" data-anomaly="${esc(a.id)}">
        <div class="acard-head">
          <span class="sev ${cls}">${esc(a.severity)}</span>
          <h4>${esc(a.title)}</h4>
          <span class="pill">${esc(a.agency || "")}${a.department ? " · " + esc(a.department) : ""}</span>
        </div>
        <p class="summary">${esc(a.summary)}</p>
        <div class="facts">${facts}</div>
        <div class="threshold"><b>판정 기준</b> · ${esc(a.threshold)}</div>
        <table class="grid">
          <thead><tr><th style="width:92px">계약일</th><th>계약명</th><th style="width:150px">업체</th><th style="width:104px" class="num">금액</th><th style="width:120px">계약번호</th><th style="width:54px">원문</th></tr></thead>
          <tbody>
            ${(a.contracts || []).map(c => `
              <tr>
                <td>${esc(c.contract_date)}</td>
                <td class="title"><span class="t" title="${esc(c.title)}">${esc(c.title)}</span></td>
                <td>${esc(c.vendor || "-")}</td>
                <td class="num amt">${comma(c.amount)}</td>
                <td class="sub">${esc(c.contract_no || "-")}</td>
                <td>${c.detail_url ? `<a class="src" href="${esc(c.detail_url)}" target="_blank" rel="noopener">열기</a>` : "-"}</td>
              </tr>`).join("")}
          </tbody>
        </table>
        ${(a.checks || []).length ? `<ul class="checks">${a.checks.map(x => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
        <div class="acard-foot">
          <button class="btn accent" data-add="${esc(a.id)}">소재로 담기</button>
          <button class="btn ghost" data-fact="${esc(a.id)}">팩트체크 시작</button>
        </div>
      </article>`;
  }

  /* ── 소재 보드 ────────────────────────────────────────── */
  function findBoard(id) { return board.find(b => b.id === id); }

  function addFromAnomaly(anomalyId, goFact) {
    const a = DATA.anomalies.find(x => x.id === anomalyId);
    if (!a) return;
    const exists = board.find(b => b.anomalyId === a.id);
    if (exists) {
      toast("이미 담긴 소재입니다. 소재 보드에서 확인하세요.");
      if (goFact) { activeFact = exists.id; go("factcheck"); }
      return;
    }
    const item = {
      id: uid(),
      anomalyId: a.id,
      title: a.title,
      agency: a.agency || "",
      department: a.department || "",
      vendor: a.vendor || "",
      amount: a.amount || 0,
      type: a.type,
      stage: goFact ? "factcheck" : "review",
      memo: a.summary || "",
      questions: (a.checks || []).map((c, i) => `${i + 1}. ${c}`).join("\n"),
      links: (a.contracts || []).filter(c => c.detail_url).length
        ? [`원문 계약 상세 | ${a.contracts.find(c => c.detail_url).detail_url}`] : [],
      contracts: (a.contracts || []).map(c => ({ contract_no: c.contract_no, contract_date: c.contract_date, title: c.title, amount: c.amount, vendor: c.vendor, detail_url: c.detail_url })),
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    };
    board.unshift(item);
    saveBoard();
    renderBoard();
    toast("소재 보드에 담았습니다.");
    if (goFact) { activeFact = item.id; go("factcheck"); }
  }

  function saveBoard() {
    store.set(K_BOARD, board);
    updateNavCounts();
  }

  function renderBoard() {
    const wrap = $("#kanban");
    wrap.innerHTML = STAGES.map(st => {
      const items = board.filter(b => (b.stage || "review") === st);
      return `
        <div class="col" data-stage="${st}">
          <div class="col-head">${STAGE_LABEL[st]}<b>${items.length}</b></div>
          ${items.length ? items.map(b => `
            <article class="kcard" draggable="true" data-id="${b.id}">
              <div class="k-top">
                <span class="tag ${b.type === "SPLIT" ? "도청" : b.type === "CONCENTRATION" ? "시군" : "교육청"}">${esc(TYPE_LABEL[b.type] || "직접 추가")}</span>
                ${b.amount ? `<span class="k-amt">${won(b.amount)}</span>` : ""}
              </div>
              <div class="k-title">${esc(b.title)}</div>
              <div class="k-meta">
                <span>${esc(b.agency || "")}</span>
                ${b.vendor ? `<span>· ${esc(b.vendor)}</span>` : ""}
                ${b.contracts && b.contracts.length ? `<span>· 근거 ${b.contracts.length}건</span>` : ""}
              </div>
              <div class="k-move">
                ${STAGES.filter(x => x !== st).map(s => `<button data-move="${b.id}:${s}" title="${STAGE_LABEL[s]}로 이동">→ ${STAGE_SHORT[s]}</button>`).join("")}
              </div>
            </article>`).join("")
        : `<div class="k-empty">카드를 여기로 끌어다 놓으세요</div>`}
        </div>`;
    }).join("");

    $$(".kcard", wrap).forEach(el => {
      el.addEventListener("dragstart", e => {
        e.dataTransfer.setData("text/plain", el.dataset.id);
        e.dataTransfer.effectAllowed = "move";
        el.style.opacity = ".5";
      });
      el.addEventListener("dragend", () => { el.style.opacity = ""; });
      el.addEventListener("click", e => {
        if (e.target.closest("[data-move]")) return;
        openCard(el.dataset.id);
      });
    });
    $$(".k-move button", wrap).forEach(b => b.addEventListener("click", () => {
      const [id, stage] = b.dataset.move.split(":");
      const it = findBoard(id);
      if (!it) return;
      it.stage = stage;
      it.updatedAt = new Date().toISOString();
      saveBoard(); renderBoard(); renderFactList();
      toast(STAGE_LABEL[stage] + "로 옮겼습니다.");
    }));
    $$(".col", wrap).forEach(col => {
      col.addEventListener("dragover", e => { e.preventDefault(); col.classList.add("drag"); });
      col.addEventListener("dragleave", () => col.classList.remove("drag"));
      col.addEventListener("drop", e => {
        e.preventDefault(); col.classList.remove("drag");
        const id = e.dataTransfer.getData("text/plain");
        const it = findBoard(id);
        if (!it) return;
        it.stage = col.dataset.stage;
        it.updatedAt = new Date().toISOString();
        saveBoard(); renderBoard(); renderFactList();
      });
    });
  }

  function openCard(id) {
    const b = findBoard(id);
    if (!b) return;
    $("#modal-title").textContent = "소재 카드";
    $("#modal-body").innerHTML = `
      <dl class="kv">
        <dt>구분</dt><dd>${esc(TYPE_LABEL[b.type] || "직접 추가")}</dd>
        <dt>기관</dt><dd>${esc(b.agency || "-")} ${b.department ? "· " + esc(b.department) : ""}</dd>
        <dt>업체</dt><dd>${esc(b.vendor || "-")}</dd>
        <dt>금액</dt><dd>${b.amount ? won(b.amount) : "-"}</dd>
      </dl>
      <div class="field"><label>제목</label><input id="m-title" value="${esc(b.title)}"></div>
      <div class="field"><label>단계</label>
        <select id="m-stage">${STAGES.map(s => `<option value="${s}" ${b.stage === s ? "selected" : ""}>${STAGE_LABEL[s]}</option>`).join("")}</select>
      </div>
      <div class="field"><label>메모 (취재 각도·확인 사항)</label><textarea id="m-memo" placeholder="왜 이 계약이 이상한지, 어떤 확인이 필요한지">${esc(b.memo || "")}</textarea></div>
      <div class="field"><label>취재 질문 (한 줄에 하나)</label><textarea id="m-q">${esc(b.questions || "")}</textarea></div>
      <div class="field"><label>관련 링크 (한 줄에 하나 · 형식: 이름 | URL)</label><textarea id="m-links">${esc((b.links || []).map(l => typeof l === "string" ? l : `${l.label} | ${l.url}`).join("\n"))}</textarea></div>
      ${(b.contracts || []).length ? `
        <div class="field"><label>근거 계약 ${b.contracts.length}건</label>
          <table class="grid small"><thead><tr><th>계약일</th><th>계약명</th><th class="num">금액</th><th>원문</th></tr></thead><tbody>
          ${b.contracts.map(c => `<tr><td>${esc(c.contract_date)}</td><td>${esc(c.title)}</td><td class="num">${comma(c.amount)}</td><td>${c.detail_url ? `<a class="src" href="${esc(c.detail_url)}" target="_blank" rel="noopener">열기</a>` : "-"}</td></tr>`).join("")}
          </tbody></table>
        </div>` : ""}
    `;
    $("#modal-foot").innerHTML = `
      <button class="btn ghost" id="m-del">삭제</button>
      <button class="btn ghost" id="m-fact">팩트체크 열기</button>
      <button class="btn" id="m-save">저장</button>`;
    $("#modal-backdrop").hidden = false;
    $("#modal-close").onclick = closeModal;
    $("#m-del").onclick = () => {
      if (!confirm("이 소재를 삭제할까요? 되돌릴 수 없습니다.")) return;
      board = board.filter(x => x.id !== id);
      delete fact[id];
      store.set(K_FACT, fact);
      saveBoard(); renderBoard(); renderFactList(); closeModal();
    };
    $("#m-save").onclick = () => {
      b.title = $("#m-title").value.trim() || b.title;
      b.stage = $("#m-stage").value;
      b.memo = $("#m-memo").value;
      b.questions = $("#m-q").value;
      b.links = $("#m-links").value.split("\n").map(s => s.trim()).filter(Boolean).map(line => {
        const [label, ...rest] = line.split("|");
        const url = rest.join("|").trim();
        return url ? { label: label.trim(), url } : { label: line, url: line };
      });
      b.updatedAt = new Date().toISOString();
      saveBoard(); renderBoard(); renderFactList(); closeModal();
      toast("저장했습니다.");
    };
    $("#m-fact").onclick = () => { activeFact = id; closeModal(); go("factcheck"); };
  }

  function closeModal() { $("#modal-backdrop").hidden = true; }

  /* ── 팩트체크 ─────────────────────────────────────────── */
  function factState(itemId) {
    fact[itemId] = fact[itemId] || { checked: [], memo: "" };
    return fact[itemId];
  }

  function renderFactList() {
    const list = $("#fc-items");
    if (!board.length) {
      list.innerHTML = `<li class="k-empty" style="border:0;background:none">소재 보드가 비어 있습니다.</li>`;
      $("#nav-count-fact").textContent = "0";
      return;
    }
    list.innerHTML = board.map(b => {
      const st = fact[b.id] || { checked: [] };
      const pct = Math.round((st.checked.length / totalChecks()) * 100);
      return `<li><button class="${activeFact === b.id ? "on" : ""}" data-fc="${b.id}">
        ${esc(b.title).slice(0, 60)}
        <span class="pct">${STAGE_LABEL[b.stage || "review"]} · 검증 ${st.checked.length}/${totalChecks()} (${pct}%)</span>
      </button></li>`;
    }).join("");
    $("#nav-count-fact").textContent = board.filter(b => (b.stage || "") === "factcheck").length;
    $$("[data-fc]", list).forEach(btn => btn.onclick = () => { activeFact = btn.dataset.fc; renderFactList(); renderFactMain(); });
    renderFactMain();
  }

  const totalChecks = () => FACT_TEMPLATE.reduce((s, g) => s + g.items.length, 0);

  function renderFactMain() {
    const main = $("#fc-main");
    if (!activeFact || !findBoard(activeFact)) {
      main.innerHTML = `<div class="empty"><div class="empty-ico">✅</div><p>왼쪽에서 소재를 고르세요.</p>
        <p class="muted small">체크 상태는 소재 카드별로 저장되고, JSON 내보내기에 함께 담깁니다.</p></div>`;
      return;
    }
    const b = findBoard(activeFact);
    const st = factState(b.id);
    const done = st.checked.length;
    const pct = Math.round((done / totalChecks()) * 100);
    main.innerHTML = `
      <div class="fc-head">
        <h3>${esc(b.title)}</h3>
        <span class="pill">${esc(b.agency || "")}${b.vendor ? " · " + esc(b.vendor) : ""}</span>
      </div>
      <div class="muted small">검증 ${done}/${totalChecks()} 항목 · ${pct}%</div>
      <div class="progress"><i style="width:${pct}%"></i></div>
      ${FACT_TEMPLATE.map(g => `
        <div class="fc-group">
          <h4>${esc(g.group)}</h4>
          ${g.items.map(it => `
            <label class="check ${st.checked.includes(it.id) ? "done" : ""}">
              <input type="checkbox" data-check="${it.id}" ${st.checked.includes(it.id) ? "checked" : ""}>
              <span><span class="t">${esc(it.t)}</span><span class="d">${esc(it.d)}</span></span>
            </label>`).join("")}
        </div>`).join("")}
      <div class="fc-memo">
        <label class="small muted">검증 메모 (확인한 자료·연락 결과)</label>
        <textarea id="fc-memo" placeholder="예: 11/25 회계과 유선 확인 — 분할 발주 아님, 예산 항목이 다름">${esc(st.memo || "")}</textarea>
      </div>`;
    $$("[data-check]", main).forEach(cb => cb.onchange = () => {
      const id = cb.dataset.check;
      const s = factState(b.id);
      s.checked = cb.checked ? [...new Set([...s.checked, id])] : s.checked.filter(x => x !== id);
      store.set(K_FACT, fact);
      renderFactMain();
      renderFactList();
    });
    const memo = $("#fc-memo", main);
    memo.onchange = () => { factState(b.id).memo = memo.value; store.set(K_FACT, fact); toast("메모를 저장했습니다."); };
  }

  /* ── 내보내기 / 불러오기 ──────────────────────────────── */
  function exportJson() {
    const payload = { app: "cartelwatch", version: 1, exportedAt: new Date().toISOString(), board, factcheck: fact };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `카르텔워치_소재보드_${today()}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
    toast("JSON으로 내보냈습니다.");
  }

  function importJson(file) {
    const r = new FileReader();
    r.onload = () => {
      try {
        const d = JSON.parse(r.result);
        const inc = d.board || d.items || d;
        if (!Array.isArray(inc)) throw new Error("형식 오류");
        const byId = new Map(board.map(b => [b.id, b]));
        inc.forEach(b => { if (b && b.id) byId.set(b.id, b); });
        board = [...byId.values()];
        if (d.factcheck) fact = { ...fact, ...d.factcheck };
        saveBoard(); store.set(K_FACT, fact);
        renderBoard(); renderFactList();
        toast(`${inc.length}건을 불러왔습니다(같은 id는 덮어씀).`);
      } catch (e) {
        alert("불러오기 실패: " + e.message);
      }
    };
    r.readAsText(file);
  }

  function exportCsv() {
    const rows = filtered();
    const head = ["구분", "기관", "부서", "계약일", "계약명", "업체", "금액", "계약방법", "계약번호", "출처", "상세URL"];
    const srcName = s => s === "both" ? "홈페이지+API" : s === "g2b_api" ? "나라장터" : "홈페이지";
    const lines = [head.join(",")].concat(rows.map(c => [
      c.category, c.agency, c.department, c.contract_date, c.title, c.vendor, c.amount, c.method, c.contract_no, srcName(c.source_type), c.detail_url,
    ].map(v => `"${String(v == null ? "" : v).replace(/"/g, '""')}"`).join(",")));
    const blob = new Blob(["\ufeff" + lines.join("\n")], { type: "text/csv;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `카르텔워치_계약_${today()}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
  }

  function updateNavCounts() {
    $("#nav-count-board").textContent = board.length;
    const fc = board.filter(b => (b.stage || "") === "factcheck").length;
    $("#nav-count-fact").textContent = fc;
  }

  /* ── 이벤트 ───────────────────────────────────────────── */
  function setAgency(id) {
    filter.agencyId = id || "";
    page = 1;
    renderAgencyChips();
    renderTable();
    $("#list-scope").textContent = filter.agencyId ? `${agencyName(filter.agencyId)}만 보는 중` : "";
  }

  function bind() {
    $$(".nav-item").forEach(b => b.onclick = () => go(b.dataset.screen));

    const applyFilter = () => { page = 1; renderTable(); };
    $("#f-dept").oninput = e => { filter.dept = e.target.value; applyFilter(); };
    $("#f-vendor").oninput = e => { filter.vendor = e.target.value; applyFilter(); };
    $("#f-from").onchange = e => { filter.from = e.target.value; applyFilter(); };
    $("#f-to").onchange = e => { filter.to = e.target.value; applyFilter(); };
    $("#f-amin").oninput = e => { filter.amin = e.target.value; applyFilter(); };
    $("#f-amax").oninput = e => { filter.amax = e.target.value; applyFilter(); };
    $("#f-q").oninput = e => { filter.q = e.target.value; applyFilter(); };
    $("#btn-reset-filter").onclick = () => {
      filter = { q: "", agencyId: filter.agencyId, dept: "", vendor: "", from: "", to: "", amin: "", amax: "", sort: filter.sort };
      ["f-dept", "f-vendor", "f-from", "f-to", "f-amin", "f-amax", "f-q"].forEach(id => { $("#" + id).value = ""; });
      page = 1; renderTable();
    };
    $("#btn-csv").onclick = exportCsv;
    $("#btn-reload").onclick = () => { loadData(); toast("data/*.json을 다시 읽었습니다."); };

    // 정렬 세그먼트
    $("#sort-seg").addEventListener("click", e => {
      const b = e.target.closest("[data-sort]");
      if (!b) return;
      filter.sort = b.dataset.sort;
      $$("#sort-seg .seg-btn").forEach(x => x.classList.toggle("on", x === b));
      renderTable();
    });

    // 기관 칩(계약 화면)
    const onAgencyClick = e => {
      const b = e.target.closest("[data-agency]");
      if (!b) return;
      setAgency(b.dataset.agency);
    };
    $("#office-chips").addEventListener("click", onAgencyClick);
    $("#agency-chips").addEventListener("click", onAgencyClick);

    // 유형 카드 → 이상징후 화면으로
    $("#type-cards").addEventListener("click", e => {
      const b = e.target.closest("[data-type]");
      if (!b) return;
      activeType = b.dataset.type;
      anomalyAgency = filter.agencyId;
      expandedTypes.clear();
      go("anomalies");
      renderAnomalies();
      renderAnomalyChips();
    });

    $("#anomaly-groups").addEventListener("click", e => {
      const add = e.target.closest("[data-add]");
      const fc = e.target.closest("[data-fact]");
      const more = e.target.closest("[data-more]");
      if (add) addFromAnomaly(add.dataset.add, false);
      if (fc) addFromAnomaly(fc.dataset.fact, true);
      if (more) { expandedTypes.add(more.dataset.more); renderAnomalies(); }
    });
    $("#type-chips").addEventListener("click", e => {
      const b = e.target.closest("[data-type]");
      if (!b) return;
      const t = b.dataset.type;
      activeType = activeType === t ? "" : t;
      expandedTypes.clear();
      renderAnomalies();
    });

    // 기관 칩(이상징후 화면)
    const onAfClick = e => {
      const b = e.target.closest("[data-af]");
      if (!b) return;
      anomalyAgency = b.dataset.af || "";
      expandedTypes.clear();
      renderAnomalies();
      renderAnomalyChips();
    };
    $("#anomaly-office-chips").addEventListener("click", onAfClick);
    $("#anomaly-agency-chips").addEventListener("click", onAfClick);

    $("#btn-export").onclick = exportJson;
    $("#file-import").onchange = e => { if (e.target.files[0]) importJson(e.target.files[0]); e.target.value = ""; };
    $("#btn-add-idea").onclick = () => {
      const it = {
        id: uid(), title: "새 소재", agency: "", department: "", vendor: "", amount: 0,
        type: "", stage: "review", memo: "", questions: "", links: [], contracts: [],
        createdAt: new Date().toISOString(), updatedAt: new Date().toISOString(),
      };
      board.unshift(it); saveBoard(); renderBoard(); openCard(it.id);
    };

    $("#modal-close").onclick = closeModal;
    $("#modal-backdrop").onclick = e => { if (e.target.id === "modal-backdrop") closeModal(); };
    document.addEventListener("keydown", e => { if (e.key === "Escape" && !$("#modal-backdrop").hidden) closeModal(); });
  }

  bind();
  loadData();
})();

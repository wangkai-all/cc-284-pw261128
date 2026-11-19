/* 去标识数据发布审查台前端（原生 JS，无第三方依赖） */
"use strict";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));
const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const state = {
  stats: null,
  candidates: [],
  releases: [],
  policy: null,
  audit: [],
  currentCandidateId: null,
  analysisBundle: null,   // 预设方案分析结果
  customResult: null,     // 自定义方案分析结果
};

const QI_LEVEL_HINTS = {
  age: ["0=原始年龄", "1=5岁段", "2=10岁段", "3=粗年龄段(18-29/30-44/45-59/60+)", "4=*"],
  gender: ["0=原始性别", "1=*"],
  zip: ["0=完整邮编", "1=前3位", "2=前2位", "3=*"],
  occupation: ["0=原始职业", "1=职业大类", "2=*"],
};
const QI_LABELS = { age: "年龄", gender: "性别", zip: "邮编", occupation: "职业" };
const QI_FIELDS = ["age", "gender", "zip", "occupation"];

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.message || `HTTP ${res.status}`);
  return data;
}

function toast(msg, kind = "info") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = msg;
  $("#toast-root").appendChild(el);
  setTimeout(() => el.remove(), 4200);
}

function pct(x) { return (x * 100).toFixed(1) + "%"; }
function statusTag(s) {
  const map = {
    released: "已发布", withdrawn: "已撤回", rejected: "已拒发",
    pending: "待审查", published: "已发布",
  };
  return `<span class="tag tag-status-${esc(s)}">${esc(map[s] || s)}</span>`;
}

/* ---------------- 初始化与导航 ---------------- */
function initTabs() {
  $$(".tab").forEach((btn) => btn.addEventListener("click", () => {
    $$(".tab").forEach((b) => b.classList.toggle("active", b === btn));
    $$(".panel").forEach((p) =>
      p.classList.toggle("active", p.id === `panel-${btn.dataset.tab}`));
    if (btn.dataset.tab === "downloads") renderDownloads();
    if (btn.dataset.tab === "ledger") renderLedger();
    if (btn.dataset.tab === "overview") refreshAll();
  }));
}

async function refreshAll() {
  const [stats, candidates, releases, policy, audit] = await Promise.all([
    api("/api/stats"), api("/api/candidates"),
    api("/api/releases"), api("/api/policy"), api("/api/audit"),
  ]);
  state.stats = stats; state.candidates = candidates;
  state.releases = releases; state.policy = policy; state.audit = audit;
  renderOverview();
  renderCandidates();
  $("#policy-chip").textContent =
    `策略 v${policy.version} · 小群体审查线 ≤ ${policy.small_threshold}（仅触发审查，非匿名保证）`;
}

/* ---------------- 总览 ---------------- */
function renderOverview() {
  const s = state.stats;
  const cards = [
    ["待审查候选", s.candidate_pending, ""],
    ["已发布（可下载）", s.released_total, "ok"],
    ["已撤回（仍计入已暴露）", s.withdrawn_total, "warn"],
    ["已拒发", s.rejected_total, ""],
    ["累计已暴露行数", s.exposed_rows_total, "warn"],
    ["审计事件", s.audit_events, ""],
  ];
  $("#stats-grid").innerHTML = cards.map(([lbl, num, cls]) =>
    `<div class="stat-card ${cls}"><div class="num">${num}</div><div class="lbl">${lbl}</div></div>`
  ).join("");

  $("#recipient-table").innerHTML = table(
    ["接收方", "已暴露行数", "覆盖时间段", "发布数", "撤回数", "拒发数"],
    Object.entries(s.per_recipient).map(([rid, v]) => [
      esc(rid), v.exposed_rows, v.exposed_periods.map(esc).join("、"),
      v.release_count, v.withdrawn_count, v.rejected_count,
    ]));

  const ss = s.seed_summary;
  $("#seed-story").innerHTML = `
    <div class="table-wrap"><table><tbody>
      <tr><th>虚构人员池</th><td>${ss.persons} 人（含跨时间段重复出现的稳定假名编号）</td></tr>
      ${ss.rare_links.map((r) =>
        `<tr><th>${esc(r.person_id)}</th><td>${esc(r.story)}</td></tr>`).join("")}
      <tr><th>${esc(ss.late_arrival.candidate_id)}</th><td>${esc(ss.late_arrival.story)}</td></tr>
      <tr><th>数据声明</th><td>${esc(ss.disclaimer)}</td></tr>
    </tbody></table></div>`;

  $("#audit-mini").innerHTML = table(
    ["时间(UTC)", "动作", "对象", "附加信息"],
    state.audit.slice(-8).reverse().map((e) => [
      esc(e.at), esc(e.action),
      esc(e.release_id || e.candidate_id || "—"),
      esc(e.reason || (e.rows != null ? `${e.rows} 行` : "") || (e.version != null ? `v${e.version}` : "")),
    ]));
}

function table(headers, rows) {
  if (!rows.length) return `<div class="empty-hint">暂无数据</div>`;
  return `<div class="table-wrap"><table><thead><tr>${
    headers.map((h) => `<th>${esc(h)}</th>`).join("")}</tr></thead><tbody>${
    rows.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")
  }</tbody></table></div>`;
}

/* ---------------- 候选任务 ---------------- */
function renderCandidates() {
  $("#candidate-table").innerHTML = table(
    ["候选 ID", "标题", "接收方", "数据期", "送达时间(UTC)", "行数", "送达时策略", "状态", "操作"],
    state.candidates.map((c) => [
      `<code>${esc(c.candidate_id)}</code>`, esc(c.title), esc(c.recipient_id),
      esc(c.period), esc(c.arrived_at), c.rows.length,
      `v${c.policy_version_at_arrival}`, statusTag(c.status),
      c.status === "pending"
        ? `<button class="btn primary" data-review="${esc(c.candidate_id)}">进入审查</button>`
        : `<span class="muted small">已由 ${esc(c.resolved_by || "—")} 处置</span>`,
    ]));
  $$("[data-review]").forEach((b) => b.addEventListener("click",
    () => openReview(b.dataset.review)));
}

$("#btn-new-candidate").addEventListener("click", () => {
  openModal("新建候选任务（虚构数据）", `
    <p class="muted small">粘贴 JSON 数组或对象。对象格式：
      <code>{period, recipient_id, title, source_file?, note?, rows:[{person_id?,age,gender,zip,occupation,salary,diagnosis}]}</code></p>
    <textarea id="new-cand-json" placeholder='[{"person_id":"X1","age":40,"gender":"男","zip":"10009","occupation":"软件工程师","salary":9000,"diagnosis":"高血压"}]'></textarea>
    <div style="margin-top:12px;display:flex;gap:10px;justify-content:flex-end">
      <button class="btn" id="new-cand-cancel">取消</button>
      <button class="btn primary" id="new-cand-save">创建并审查</button>
    </div>`);
  $("#new-cand-cancel").onclick = closeModal;
  $("#new-cand-save").onclick = async () => {
    let payload;
    try {
      const parsed = JSON.parse($("#new-cand-json").value);
      payload = Array.isArray(parsed)
        ? { period: prompt("数据期（如 2026H2）：", "2026H2"),
            recipient_id: prompt("接收方 ID：", "RC-医保研究合作方"),
            title: "手工上传候选", rows: parsed }
        : parsed;
      if (!payload.period || !payload.recipient_id) throw new Error("数据期与接收方必填");
    } catch (e) { toast("JSON 解析失败：" + e.message, "err"); return; }
    try {
      const cand = await api("/api/candidates", { method: "POST", body: payload });
      closeModal(); toast("候选已创建：" + cand.candidate_id, "ok");
      await refreshAll(); openReview(cand.candidate_id);
    } catch (e) { toast(e.message, "err"); }
  };
});

/* ---------------- 发布审查 ---------------- */
async function openReview(candidateId) {
  state.currentCandidateId = candidateId;
  state.customResult = null;
  $$(".tab").forEach((b) =>
    b.classList.toggle("active", b.dataset.tab === "review"));
  $$(".panel").forEach((p) =>
    p.classList.toggle("active", p.id === "panel-review"));
  $("#review-empty").hidden = true;
  $("#review-body").hidden = false;
  await loadAnalysis(candidateId);
}

async function loadAnalysis(candidateId) {
  const bundle = await api(`/api/analysis/${candidateId}`);
  state.analysisBundle = bundle;
  const c = bundle.candidate;
  $("#review-title").textContent = `${c.candidate_id} · ${c.title}`;
  $("#review-meta").innerHTML =
    `接收方 <b>${esc(c.recipient_id)}</b> ｜ 数据期 <b>${esc(c.period)}</b> ｜ ` +
    `${c.rows.length} 行 ｜ 送达时策略 v${c.policy_version_at_arrival}，当前策略 v${bundle.policy_version} ｜ ` +
    `来源文件 ${esc(c.source_file)}` + (c.note ? `<br>备注：${esc(c.note)}` : "");

  $("#late-banner").hidden = !bundle.late_arrival;
  if (bundle.late_arrival) {
    $("#late-text").textContent =
      `该任务数据期 ${c.period} 早于该接收方账面最新发布期，送达时策略 v${c.policy_version_at_arrival}。`;
  }
  renderPresets(bundle.presets);
  renderClasses(bundle.presets[0]);      // 基线 = 原样发布
  renderDiffs(bundle.presets[0]);
  renderPlanEditor(bundle.presets[0].plan);
  $("#custom-result").innerHTML = "";
}

function signalLine(res) {
  const s = res.summary;
  const tags = [];
  if (s.singleton_classes) tags.push(`<span class="tag tag-singleton">singleton ×${s.singleton_classes}</span>`);
  if (s.small_classes) tags.push(`<span class="tag tag-small">small ×${s.small_classes}</span>`);
  if (s.shrunk_classes) tags.push(`<span class="tag tag-shrunk">shrunk ×${s.shrunk_classes}</span>`);
  if (!tags.length) tags.push('<span class="muted small">未触发信号（不等于无风险）</span>');
  return tags.join("");
}

function renderPresets(presets) {
  $("#preset-grid").innerHTML = presets.map((p, i) => {
    const l = p.loss;
    const high = l.overall_loss >= 0.99 ? " loss-high" : "";
    return `<div class="preset-card">
      <h4>${esc(p.preset_label)}</h4>
      <div>${signalLine(p)}</div>
      <div class="metric-row"><span>发布 / 抑制行数</span><b>${l.released_count} / ${l.suppressed_count}</b></div>
      <div class="metric-row"><span>行抑制率</span><b>${pct(l.row_suppression_rate)}</b></div>
      <div class="metric-row"><span>泛化损失</span><b>${pct(l.generalization_loss)}</b></div>
      <div class="metric-row"><span>薪资均值偏移</span><b>${pct(l.salary_mean_shift)}</b></div>
      <div class="metric-row"><span>诊断分布 TVD</span><b>${pct(l.diagnosis_tvd)}</b></div>
      <div class="metric-row"><span>综合信息损失</span><b>${pct(l.overall_loss)}</b></div>
      <div class="meter${high}"><i style="width:${(l.overall_loss * 100).toFixed(1)}%"></i></div>
      <div class="metric-row"><span>效用保留（1−综合损失）</span><b>${pct(l.utility_score)}</b></div>
      <div class="actions">
        <button class="btn" data-preset-idx="${i}">查看该方案的等价类与差异</button>
        <button class="btn primary" data-preset-publish="${i}"
          ${state.analysisBundle.candidate.status !== "pending" ? "disabled" : ""}>按此方案发布</button>
      </div>
    </div>`;
  }).join("");

  $$("[data-preset-idx]").forEach((b) => b.addEventListener("click", () => {
    const p = presets[Number(b.dataset.presetIdx)];
    renderClasses(p); renderDiffs(p); renderPlanEditor(p.plan);
    toast(`已切换到「${p.preset_label}」视图`, "info");
  }));
  $$("[data-preset-publish]").forEach((b) => b.addEventListener("click", () => {
    const p = presets[Number(b.dataset.presetPublish)];
    confirmPublish(p.plan, p.preset_label);
  }));
}

function renderClasses(res) {
  const rows = res.classes.map((cl) => {
    const keyText = QI_FIELDS.map((f) =>
      `${QI_LABELS[f]}=<b>${esc(cl.key[f])}</b>`).join(" ｜ ");
    const prior = cl.prior_matches.length
      ? cl.prior_matches.map((m) =>
          `<div class="small">${esc(m.release_id)}（${esc(m.period)}${
            m.status === "withdrawn" ? "，已撤回仍计入" : ""}）桶大小 ${m.prior_group_size}
           → 本次 ${cl.candidate_size} 人</div>`).join("")
      : '<span class="muted small">无历史匹配（首次出现）</span>';
    const tags = cl.flags.map((f) => {
      const label = { singleton: "singleton", small: "small", shrunk: "shrunk" }[f];
      return `<span class="tag tag-${f}">${label}</span>`;
    }).join("");
    return `<tr>
      <td>${keyText}<div class="small muted">人员：${cl.candidate_person_ids.map(esc).join("、")}</div></td>
      <td>${cl.candidate_size}</td>
      <td>${prior}</td>
      <td>${cl.cumulative_person_count}</td>
      <td>${tags}<div class="small muted">${cl.flag_labels.map(esc).join("<br>")}</div></td>
    </tr>`;
  });
  $("#class-table").innerHTML = table(
    ["QI 组合（处置后展示值）", "本次人数", "历史匹配与群体变化", "累计涉及人数", "信号"], rows);
}

function renderDiffs(res) {
  const rows = res.row_diffs.map((d) => {
    let detail;
    if (d.action === "suppressed" || d.action === "rejected") {
      detail = `<span class="cell-suppressed">${d.action === "rejected" ? "整份拒发：行不发布" : "整行抑制：不发布"}</span>`;
    } else if (d.action === "unchanged") {
      detail = '<span class="muted small">无变化</span>';
    } else {
      detail = Object.entries(d.after).map(([f, after]) =>
        `<span class="small"><b>${QI_LABELS[f]}</b>
          <span class="diff-before">${esc(d.before[f])}</span>
          <span class="diff-arrow">→</span>
          <span class="diff-after">${esc(after)}</span></span>`).join("　");
    }
    return [`<code>${esc(d.person_id)}</code>`, detail];
  });
  $("#diff-table").innerHTML = table(["人员（内部假名）", "处置前后差异"], rows);
}

function renderPlanEditor(plan) {
  $("#plan-reject").checked = !!plan.rejected;
  $("#plan-auto-suppress").checked =
    !plan.rejected && plan.suppressed_person_ids.length > 0;
  $("#plan-levels").innerHTML = QI_FIELDS.map((f) => {
    const maxLvl = { age: 4, gender: 1, zip: 3, occupation: 2 }[f];
    const opts = Array.from({ length: maxLvl + 1 }, (_, lvl) =>
      `<option value="${lvl}" ${plan.levels[f] === lvl ? "selected" : ""}>层级 ${lvl}</option>`).join("");
    return `<tr>
      <td><b>${QI_LABELS[f]}</b> (<code>${f}</code>)</td>
      <td><select data-level="${f}" ${plan.rejected ? "disabled" : ""}>${opts}</select></td>
      <td class="small muted">${QI_LEVEL_HINTS[f].map(esc).join(" ｜ ")}</td>
    </tr>`;
  }).join("");
}

function readPlanEditor() {
  const rejected = $("#plan-reject").checked;
  const levels = {};
  $$("[data-level]").forEach((sel) => { levels[sel.dataset.level] = Number(sel.value); });
  let suppressed = [];
  if ($("#plan-auto-suppress").checked && state.analysisBundle) {
    suppressed = state.analysisBundle.presets[0].summary.flagged_person_ids;
  }
  return { rejected, levels, suppressed_person_ids: suppressed };
}

$("#plan-reject").addEventListener("change", () => {
  $$("[data-level]").forEach((s) => { s.disabled = $("#plan-reject").checked; });
});

$("#btn-apply-custom").addEventListener("click", async () => {
  if (!state.currentCandidateId) return;
  try {
    const plan = readPlanEditor();
    const res = await api(`/api/analysis/${state.currentCandidateId}`, {
      method: "POST", body: { plan },
    });
    state.customResult = res;
    renderClasses(res); renderDiffs(res);
    const l = res.loss;
    $("#custom-result").innerHTML = `
      <div class="preset-card" style="box-shadow:none;border-color:var(--primary)">
        <h4>自定义方案分析结果</h4>
        <div>${signalLine(res)}</div>
        <div class="metric-row"><span>发布 / 抑制</span><b>${l.released_count} / ${l.suppressed_count}</b></div>
        <div class="metric-row"><span>综合信息损失</span><b>${pct(l.overall_loss)}</b></div>
        <div class="metric-row"><span>效用保留</span><b>${pct(l.utility_score)}</b></div>
        <div class="small muted">${esc(l.note)}</div>
      </div>`;
    toast("自定义方案分析完成（基于当前账本实时计算）", "ok");
  } catch (e) { toast(e.message, "err"); }
});

$("#btn-publish").addEventListener("click", () => {
  const plan = readPlanEditor();
  confirmPublish(plan, plan.rejected ? "整份拒发" : "当前自定义方案");
});

$("#btn-refresh-analysis").addEventListener("click", async () => {
  if (!state.currentCandidateId) return;
  await loadAnalysis(state.currentCandidateId);
  toast("已按当前账本与策略重新分析", "ok");
});

async function confirmPublish(plan, label) {
  const c = state.analysisBundle.candidate;
  if (c.status !== "pending") { toast("该候选已处置，不能重复发布", "err"); return; }
  openModal(`确认执行：${label}`, `
    <p>候选 <code>${esc(c.candidate_id)}</code>（${esc(c.period)} → ${esc(c.recipient_id)}）</p>
    <p class="muted small">发布瞬间服务端会按<strong>当前账本</strong>重算风险与损失，
      并把结果写入账本。若有其他任务在你审查期间先行发布，你看到的信号可能变化。</p>
    <pre style="background:#f6f8fc;padding:10px;border-radius:8px;overflow:auto;max-height:220px">${
      esc(JSON.stringify(plan, null, 2))}</pre>
    <div style="display:flex;gap:10px;justify-content:flex-end;margin-top:12px">
      <button class="btn" id="publish-cancel">取消</button>
      <button class="btn primary" id="publish-confirm">确认</button>
    </div>`);
  $("#publish-cancel").onclick = closeModal;
  $("#publish-confirm").onclick = async () => {
    try {
      const rel = await api(`/api/publish/${c.candidate_id}`, {
        method: "POST", body: { plan },
      });
      closeModal();
      toast(`${plan.rejected ? "拒发" : "发布"}成功：${rel.release_id}（策略版本已推进）`, "ok");
      state.currentCandidateId = null;
      $("#review-body").hidden = true;
      $("#review-empty").hidden = false;
      await refreshAll();
    } catch (e) { toast(e.message, "err"); }
  };
}

/* ---------------- 账本 ---------------- */
function renderLedger() {
  $("#ledger-table").innerHTML = table(
    ["发布 ID", "接收方", "数据期", "状态", "创建时间(UTC)", "策略版本",
     "行数", "处置", "撤回信息", "操作"],
    state.releases.map((r) => [
      `<code>${esc(r.release_id)}</code><div class="small muted">候选 ${esc(r.candidate_id)}</div>`,
      esc(r.recipient_id), esc(r.period), statusTag(r.status),
      esc(r.created_at), `v${r.policy_version}`, r.row_count,
      describePlan(r.plan),
      r.status === "withdrawn"
        ? `<span class="small">${esc(r.withdrawn_at)}<br>${esc(r.withdraw_reason || "")}</span>`
        : "—",
      r.status === "released"
        ? `<button class="btn danger" data-withdraw="${esc(r.release_id)}">撤回</button>`
        : '<span class="muted small">—</span>',
    ]));
  $$("[data-withdraw]").forEach((b) => b.addEventListener("click",
    () => openWithdraw(b.dataset.withdraw)));
}

function describePlan(plan) {
  if (!plan) return "—";
  if (plan.rejected) return '<span class="tag tag-status-rejected">整份拒发</span>';
  const parts = [];
  const lv = plan.levels || {};
  if (Object.values(lv).some((v) => v > 0))
    parts.push("分桶：" + QI_FIELDS.filter((f) => lv[f] > 0)
      .map((f) => `${QI_LABELS[f]} L${lv[f]}`).join("、"));
  if (plan.suppressed_person_ids?.length)
    parts.push(`抑制 ${plan.suppressed_person_ids.length} 行`);
  return parts.length ? parts.map(esc).join("<br>") : '<span class="small muted">原样发布</span>';
}

function openWithdraw(releaseId) {
  openModal(`撤回发布 ${releaseId}`, `
    <p class="banner banner-warn" style="margin-top:0">撤回只阻止继续下载；
      接收方已经拿到的数据无法追回，<b>仍保留在累计暴露知识库中</b>，后续发布审查继续计入。</p>
    <label class="small muted">撤回原因（必填）</label>
    <input type="text" id="withdraw-reason" placeholder="例如：发布后发现新的外部数据集可与该文件组合">
    <div style="display:flex;gap:10px;justify-content:flex-end;margin-top:14px">
      <button class="btn" id="withdraw-cancel">取消</button>
      <button class="btn danger" id="withdraw-confirm">确认撤回</button>
    </div>`);
  $("#withdraw-cancel").onclick = closeModal;
  $("#withdraw-confirm").onclick = async () => {
    const reason = $("#withdraw-reason").value.trim();
    if (!reason) { toast("必须填写撤回原因", "err"); return; }
    try {
      await api(`/api/withdraw/${releaseId}`, { method: "PATCH", body: { reason } });
      closeModal(); toast("已撤回，下载入口同步关闭；历史暴露仍计入知识库", "ok");
      await refreshAll(); renderLedger();
    } catch (e) { toast(e.message, "err"); }
  };
}

/* ---------------- 下载中心 ---------------- */
function renderDownloads() {
  $("#download-table").innerHTML = table(
    ["发布 ID", "接收方", "数据期", "状态", "行数", "泛化层级", "下载"],
    state.releases.map((r) => {
      const lv = r.plan?.levels || {};
      const lvText = r.plan?.rejected
        ? "—" : QI_FIELDS.filter((f) => lv[f] > 0)
          .map((f) => `${QI_LABELS[f]} L${lv[f]}`).join("、") || "无（原值）";
      const dl = r.status === "released"
        ? `<a class="btn primary" href="/api/download/${encodeURIComponent(r.release_id)}"
             download>下载 JSON</a>`
        : `<span class="muted small">${r.status === "withdrawn" ? "已撤回：停止分发" : "已拒发：无制品"}</span>`;
      return [`<code>${esc(r.release_id)}</code>`, esc(r.recipient_id),
        esc(r.period), statusTag(r.status), r.row_count, lvText, dl];
    }));
}

/* ---------------- 方法与边界 ---------------- */
function renderMethod() {
  $("#method-doc").innerHTML = `
  <h3>1. 审查对象与准标识符</h3>
  <p>每条候选记录包含四个准标识符 <code>age / gender / zip / occupation</code>
     与两个敏感字段 <code>salary / diagnosis</code>。内部稳定假名
     <code>person_id</code> 仅用于台内跨时间段 linkage 分析，<b>不会进入对外下载制品</b>。</p>

  <h3>2. 泛化层级（分桶）</h3>
  <ul>
    <li>年龄：原值 → 5 岁段 → 10 岁段 → 粗年龄段（18-29/30-44/45-59/60+）→ *</li>
    <li>性别：原值 → *；邮编：完整 → 前 3 位 → 前 2 位 → *；职业：原值 → 职业大类 → *</li>
    <li>每个泛化值记录其覆盖的原始值集合，用于判断新文件的细粒度组合是否落入历史粗桶。</li>
  </ul>

  <h3>3. 三类描述性信号</h3>
  <ul>
    <li><span class="tag tag-singleton">singleton</span> 新文件内该等价类仅 1 人；</li>
    <li><span class="tag tag-small">small</span> 等价类不超过策略审查线（默认 2）。
      审查线<b>只决定是否提示人工复核</b>，调高调低都不构成匿名保证；</li>
    <li><span class="tag tag-shrunk">shrunk</span> 新组合与历史发布的粗桶相容但人数更少，
      即新文件缩小了接收方原本掌握的群体范围。明细中给出具体发布 ID、时期与 2→1 之类的变化。</li>
  </ul>

  <h3>4. 累计暴露知识库</h3>
  <p>按接收方分别构建：该接收方所有发布制品中的 QI 展示值都并入知识库。
  <b>已撤回的发布不删除</b>——撤回只停止继续下载，数据一旦发出即视为已暴露。
  重新发布时审查自动包含接收方拿过的全部历史信息。</p>

  <h3>5. 三种处置与信息损失</h3>
  <ul>
    <li><b>抑制</b>：被标记人员的整行不发布；</li>
    <li><b>分桶</b>：提高指定 QI 字段的泛化层级，可与抑制组合；</li>
    <li><b>拒发</b>：整份文件不发布，信息损失 100%、效用为 0。</li>
  </ul>
  <p>损失指标：行抑制率、泛化覆盖损失、薪资均值相对偏移、诊断分布总变差（TVD），
     按固定权重合成综合损失与效用保留分。权重是<b>人为设定的效用排序参数</b>，不是隐私阈值。</p>

  <h3>6. 迟到任务与处置同步</h3>
  <p>数据期早于账面最新发布期的候选标记为迟到任务。所有分析在打开审查与点击发布时
     <b>都由服务端按当前策略版本与完整账本实时重算</b>，前端只提交处置参数，
     无法携带旧结论绕过新处置。每次发布、拒发、撤回、策略修改都会推进策略版本并写审计日志。</p>

  <h3>7. 能力边界（务必阅读）</h3>
  <ul>
    <li>本台只掌握账内发布与候选数据，无法获知接收方持有的外部数据集与背景知识；</li>
    <li>群体大小、k-匿名式计数等简单阈值<b>不能保证绝对匿名</b>，同质攻击、
      背景知识攻击、差分攻击等不依赖唯一组合；</li>
    <li>本台不提供差分隐私形式化保证，信号与损失均为辅助人工判断的描述性度量；</li>
    <li>撤回不具备消息撤回能力：已分发副本的实际流转不可控；</li>
    <li>全部样例为程序生成的虚构数据，不对应任何真实个人。</li>
  </ul>`;
}

/* ---------------- 弹窗 ---------------- */
function openModal(title, html) {
  $("#modal-title").textContent = title;
  $("#modal-body").innerHTML = html;
  $("#modal-backdrop").hidden = false;
}
function closeModal() { $("#modal-backdrop").hidden = true; }
$("#modal-close").addEventListener("click", closeModal);
$("#modal-backdrop").addEventListener("click", (e) => {
  if (e.target === $("#modal-backdrop")) closeModal();
});

/* ---------------- 启动 ---------------- */
initTabs();
renderMethod();
refreshAll().catch((e) => toast("初始化失败：" + e.message, "err"));

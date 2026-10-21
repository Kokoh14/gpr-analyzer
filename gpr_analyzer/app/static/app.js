"use strict";
// GPR Analyzer review page. Talks only to the local server (server.py).

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const TYPE = {
  void_like: ["คล้ายโพรง", "--t-void"], pipe_point: ["ท่อ / วัตถุจุด", "--t-pipe"], layer: ["ชั้นดิน / หิน", "--t-layer"],
  scattered: ["สะท้อนกระจัดกระจาย", "--t-scatter"], artifact: ["Artifact", "--t-unknown"], unknown: ["ไม่ระบุ", "--t-unknown"],
};
const CONF = { high: "สูง", medium: "ปานกลาง", low: "ต่ำ", confirmed: "ยืนยันแล้ว" };
const ACT = { E: "E ฉุกเฉิน", 1: "1 เร่งด่วน", 2: "2 ตรวจสอบเพิ่ม", 3: "3 เฝ้าระวัง", none: "ไม่ต้องดำเนินการ" };
const REV = { auto: "ยังไม่ตรวจ", accepted: "ยอมรับแล้ว", edited: "แก้ไขแล้ว", rejected: "ปฏิเสธ" };
const ART = { vertical_stripe: "แถบแนวตั้ง", bottom_noise: "noise ก้นภาพ", abnormal_trace: "trace ผิดปกติ", dead_trace: "trace ว่าง", stationary: "หยุดเดิน" };
const STEP = { center_frequency: "ความถี่กลาง", dewow: "Dewow", time_zero: "Time-zero", bandpass: "Bandpass", background_removal: "Background removal",
  gain: "Gain (SEC)", velocity: "ความเร็วคลื่น", migration: "Migration", envelope: "Envelope / energy", agc_display: "AGC (แสดงผล)" };
const VIEWS = [
  ["grey", "ขาวดำ (รักษาแอมพลิจูด ใช้ให้คะแนน)", ""],
  ["high_gain", "Gain สูง (AGC ใช้ดูเท่านั้น)", "สี = ขั้วสัญญาณ แอมพลิจูดเทียบกันข้ามภาพไม่ได้"],
  ["envelope", "พลังงานสะท้อน (Hilbert envelope)", "สีแดง = สะท้อนแรง ไม่ได้แปลว่าเป็นโพรง"],
];
const FAMILY = { iprh: "header", iprb: "data", rad: "header", rd3: "data", rd7: "data", hd: "header", dt1: "data", dzt: "data", sgy: "data", segy: "data" };
const COMPANION = ["txt", "cor", "gps", "csv", "xml", "ini", "json", "log", "tmf", "mrk"];

const S = { files: [], job: null, line: null, sel: null, editing: null, drawFor: null };

function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(toast.h); toast.h = setTimeout(() => (t.hidden = true), 3500);
}
function busy(on, text) { $("#busy").hidden = !on; if (text) $("#busyText").textContent = text; }

async function api(path, opt = {}) {
  const r = await fetch(path, opt);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch (e) { /* not json */ }
    throw new Error(msg);
  }
  return r.json();
}
const post = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

// ------------------------------------------------------------------ upload
function role(name) {
  const ext = name.split(".").pop().toLowerCase();
  if (FAMILY[ext] === "data") return ["ข้อมูล GPR", "data"];
  if (FAMILY[ext] === "header") return ["header (คู่กับไฟล์ข้อมูล)", "data"];
  if (COMPANION.includes(ext)) return ["ไฟล์ประกอบ", ""];
  return ["ยังไม่รู้จัก (เช่น SubScan) จะลองแล้วแจ้งผล", "bad"];
}
function renderFiles() {
  const tb = $("#fileList tbody");
  tb.innerHTML = S.files.map((f, i) => {
    const [r, c] = role(f.name);
    return `<tr><td>${esc(f.name)}</td><td>${(f.size / 1024).toFixed(0)} KB</td><td><span class="role ${c}">${r}</span></td>
      <td><button class="btn ghost" data-rm="${i}">ลบ</button></td></tr>`;
  }).join("");
  $("#fileList").hidden = !S.files.length;
  $("#runBtn").disabled = !S.files.length;
}
function addFiles(list) {
  for (const f of list) if (!S.files.some((g) => g.name === f.name)) S.files.push(f);
  renderFiles();
}
function infoValues(form) {
  const d = {};
  for (const el of form.elements) if (el.name) d[el.name] = el.value;
  return d;
}
async function runUpload() {
  const fd = new FormData();
  S.files.forEach((f) => fd.append("files", f, f.name));
  busy(true, `กำลังอ่านและประมวลผล ${S.files.length} ไฟล์…`);
  try {
    const job = await api("/api/jobs", { method: "POST", body: fd });
    S.job = await post(`/api/jobs/${job.job_id}/info`, infoValues($("#infoForm")));
    openReview();
  } catch (e) { toast("ผิดพลาด: " + e.message); } finally { busy(false); }
}
async function runDemo() {
  busy(true, "กำลังสร้างข้อมูลสังเคราะห์และประมวลผล…");
  try { S.job = await post("/api/demo", {}); openReview(); } catch (e) { toast("ผิดพลาด: " + e.message); } finally { busy(false); }
}

// ------------------------------------------------------------------ review
function openReview() {
  $("#uploadView").hidden = true; $("#reviewView").hidden = false; $("#exports").hidden = false;
  history.replaceState(null, "", "#" + S.job.job_id);
  S.line = S.job.lines[0]?.line_id || null;
  buildInfoForm();
  render();
}
const line = () => S.job.lines.find((l) => l.line_id === S.line);
const zoneOf = (id) => line()?.zones.find((z) => z.zone_id === id);

function render() {
  renderTop(); renderLines(); renderLine();
}
function renderTop() {
  const j = S.job, r = j.review, n = r.auto + r.accepted + r.edited + r.rejected;
  $("#jobMeta").innerHTML = `<b>${esc(j.info.project || "งานสำรวจ")}</b> <span class="muted">${esc(j.info.site || "")}</span>
    <span class="badge ${j.draft ? "draft" : "done"}">${j.draft ? "ฉบับร่าง: ยังไม่ได้ตรวจทุกโซน" : "ตรวจครบทุกโซน"}</span>
    <span class="muted small">ตรวจแล้ว ${n - r.auto}/${n} โซน</span>`;
  $$("[data-export]").forEach((a) => (a.href = `/api/jobs/${j.job_id}/export/${a.dataset.export}`));
  $("#labelsLink").href = `/api/jobs/${j.job_id}/labels.jsonl`;
}
function renderLines() {
  $("#lineList").innerHTML = S.job.lines.map((l) => {
    const r = l.review, todo = r.auto;
    return `<li data-line="${l.line_id}" class="${l.line_id === S.line ? "active" : ""}"><b>${l.line_id}</b> ${esc(l.source_file)}
      <span class="small muted">${l.zones.length} โซน${todo ? ` · รอตรวจ ${todo}` : " · ตรวจครบ"}</span></li>`;
  }).join("") || `<li class="muted">ไม่มีเส้นที่อ่านได้</li>`;
  const probs = [...S.job.problems.map((p) => p.error === "unknown_format"
      ? `${esc(p.file)}: ยังอ่านรูปแบบนี้ไม่ได้ ใช้คำสั่ง gpr-sniff ช่วยถอดรูปแบบ (เช่น --traces 555)`
      : `${esc(p.file)}: ${esc(p.error)}`),
    ...S.job.unassigned.map((u) => `${esc(u.name)}: ไม่มีไฟล์ข้อมูลชื่อเดียวกัน (ไม่ได้ใช้)`)];
  $("#problems").innerHTML = probs.map((p) => `<div class="problem">⚠ ${p}</div>`).join("");
}

function fmt(v, d = 2) { return v == null ? "–" : Number(v).toFixed(d); }
function renderLine() {
  const l = line();
  if (!l) { $("#panes").innerHTML = ""; $("#zoneCards").innerHTML = ""; return; }
  $("#lineTitle").textContent = `${l.line_id} · ${l.source_file}`;
  $("#lineFacts").innerHTML = `${l.n_traces} traces · ${l.n_samples} samples · dt ${fmt(l.dt_ns, 3)} ns · time window ${fmt(l.time_window_ns, 0)} ns
    · ระยะ trace ${l.dx_m ? fmt(l.dx_m, 3) + " m" : "ไม่ทราบ"} · ความเร็ว ${fmt(l.velocity_m_per_ns, 3)} m/ns (${esc(l.velocity_source)})
    ${l.companions.length ? "· ไฟล์ประกอบ: " + l.companions.map((c) => `<span title="${esc(c.text || "")}">${esc(c.name)}</span>`).join(", ") : ""}`;
  const stamp = Date.now();
  $("#panes").innerHTML = VIEWS.map(([v, title, note], i) => `
    <div class="pane" data-view="${v}">
      <div class="pane-cap"><b>${title}</b><span class="muted">${note}
        ${v === "envelope" ? `<span class="legend">-30 dB <span class="bar" style="background:linear-gradient(90deg,#30123b,#4686fb,#1ae4b6,#a2fc3c,#faba39,#e4460a,#7a0403)"></span> 0 dB</span>` : ""}</span></div>
      <div class="plot">
        <div class="yaxis left">${yTicks(l, "t")}<span class="ttl">เวลา (ns)</span></div>
        <div class="imgwrap" data-view="${v}"><img alt="${title}" src="/api/jobs/${S.job.job_id}/lines/${l.line_id}/${v}.png?${stamp}">${overlay(l)}</div>
        <div class="yaxis right">${yTicks(l, "d")}<span class="ttl">ความลึก (m)</span></div>
      </div>
      ${i === VIEWS.length - 1 ? `<div class="xaxis">${xTicks(l)}<span class="ttl">${l.dx_m ? "trace / ระยะ (m)" : "trace"}</span></div>` : ""}
    </div>`).join("");
  renderRecipe(l);
  renderCards(l);
}
function nice(span, n) {
  const raw = span / n, p = 10 ** Math.floor(Math.log10(raw)), m = raw / p;
  return (m < 1.5 ? 1 : m < 3 ? 2 : m < 7 ? 5 : 10) * p;
}
function yTicks(l, kind) {
  const T = l.time_window_ns, v = l.velocity_m_per_ns, out = [];
  if (kind === "t") { const s = nice(T, 5); for (let t = 0; t <= T; t += s) out.push([t / T, t]); }
  else { const D = T * v / 2, s = nice(D, 5); for (let d = 0; d <= D; d += s) out.push([d / D, +d.toFixed(2)]); }
  return out.map(([f, lab]) => `<span style="top:${f * 100}%">${lab}</span>`).join("");
}
function xTicks(l) {
  const N = l.n_traces, s = nice(N, 8), out = [];
  for (let t = 0; t < N; t += s) out.push(`<span style="left:${(t / N) * 100}%">${t}${l.dx_m ? `<br>${(t * l.dx_m).toFixed(1)}` : ""}</span>`);
  return out.join("");
}
function overlay(l) {
  const T = l.time_window_ns, N = l.n_traces;
  const showArt = $("#showArtifacts").checked, showRej = $("#showRejected").checked;
  let h = "";
  if (showArt) for (const a of l.artifact_regions) {
    const top = a.time_top_ns != null ? (a.time_top_ns / T) * 100 : 0, bot = a.time_bottom_ns != null ? (a.time_bottom_ns / T) * 100 : 100;
    h += `<div class="art" title="artifact: ${esc(ART[a.reason] || a.reason)} trace ${a.trace_start}–${a.trace_end}"
      style="left:${(a.trace_start / N) * 100}%;width:${Math.max(((a.trace_end - a.trace_start + 1) / N) * 100, 0.25)}%;top:${top}%;bottom:${100 - bot}%"></div>`;
  }
  for (const z of l.zones) {
    if (z.review === "rejected" && !showRej) continue;
    if (z.suspected_type === "artifact" && !showArt) continue;
    const c = (TYPE[z.suspected_type] || TYPE.unknown)[1];
    h += `<div class="zbox ${z.review} ${z.suspected_type === "artifact" ? "artifactz" : ""} ${z.zone_id === S.sel ? "sel" : ""}" data-zone="${z.zone_id}"
      style="--c:var(${c});left:${(z.trace_start / N) * 100}%;width:${((z.trace_end - z.trace_start + 1) / N) * 100}%;
      top:${(z.time_top_ns / T) * 100}%;height:${Math.max(((z.time_bottom_ns - z.time_top_ns) / T) * 100, 0.8)}%">
      <span class="lbl">${z.zone_id}</span></div>`;
  }
  return h;
}
function refreshOverlays() {
  const l = line();
  $$(".imgwrap").forEach((w) => { $$(".zbox,.art", w).forEach((e) => e.remove()); w.insertAdjacentHTML("beforeend", overlay(l)); });
}

function renderCards(l) {
  const f = $("#zoneFilter").value;
  const zs = l.zones.filter((z) => f === "all" || (f === "todo" ? z.review === "auto" : z.suspected_type !== "artifact"));
  $("#zoneCount").textContent = `(${l.zones.length})`;
  $("#zoneCards").innerHTML = zs.map(card).join("") || `<p class="muted">ไม่มีโซนในตัวกรองนี้</p>`;
}
function card(z) {
  const [tname, c] = TYPE[z.suspected_type] || TYPE.unknown;
  const act = z.suggested_action;
  const unc = z.velocity_uncertainty_m_per_ns;
  const ev = z.evidence.map((e) => `<li class="${e.kind}" title="${esc(e.params?.text_en || e.code)}">${esc(e.text || e.params?.text_en || e.code)}</li>`).join("");
  const dist = z.distance_start_m != null ? ` · ระยะ ${fmt(z.distance_start_m, 1)}–${fmt(z.distance_end_m, 1)} m` : "";
  const editing = S.editing === z.zone_id;
  return `<div class="zcard ${z.review} ${z.zone_id === S.sel ? "sel" : ""}" data-card="${z.zone_id}" style="--c:var(${c})">
    <header><span class="zid">${z.zone_id}</span><b>${tname}</b>
      <span class="chip conf-${z.confidence}" title="ระดับความเชื่อมั่นจากกฎ ไม่ใช่ความน่าจะเป็น">เชื่อมั่น ${CONF[z.confidence]}</span>
      <span class="chip act-${act}" title="ระดับการดำเนินการที่แนะนำ">${ACT[act] || act}</span>
      <span class="rev ${z.review}">${REV[z.review]}${z.manual ? " · วาดเพิ่ม" : ""}</span></header>
    <div class="zloc">trace ${z.trace_start}–${z.trace_end}${dist}<br>
      เวลา ${fmt(z.time_top_ns, 1)}–${fmt(z.time_bottom_ns, 1)} ns · ลึก ${fmt(z.depth_top_m)}–${fmt(z.depth_bottom_m)} m
      (v ${fmt(z.velocity_m_per_ns, 3)}${unc ? " ± " + fmt(unc, 3) : ""} m/ns, ${esc(z.velocity_source)})</div>
    <ul class="ev">${ev}</ul>
    ${z.artifact_flags.length ? `<div class="small" style="color:var(--warn)">⚠ artifact ใกล้เคียง: ${esc(z.artifact_flags.join(", "))}</div>` : ""}
    ${z.note ? `<div class="small muted">หมายเหตุ: ${esc(z.note)}</div>` : ""}
    <div class="zbtns">
      <button class="btn ok" data-act="accept" data-z="${z.zone_id}">✓ ยอมรับ</button>
      <button class="btn" data-act="edit" data-z="${z.zone_id}">✎ แก้ไข</button>
      <button class="btn bad" data-act="reject" data-z="${z.zone_id}">✗ ปฏิเสธ</button>
      ${z.manual ? `<button class="btn ghost" data-act="delete" data-z="${z.zone_id}">ลบ</button>`
        : z.review !== "auto" ? `<button class="btn ghost" data-act="reset" data-z="${z.zone_id}" title="กลับเป็นผลอัตโนมัติ">↺</button>` : ""}
    </div>
    ${editing ? editForm(z) : ""}
  </div>`;
}
function opts(list, cur, labels) {
  return list.map((v) => `<option value="${v}" ${String(v) === String(cur) ? "selected" : ""}>${esc(labels?.[v] || v)}</option>`).join("");
}
function editForm(z) {
  const ch = S.job.choices;
  return `<form class="zedit" data-editform="${z.zone_id}">
    <label>ประเภท<select name="suspected_type">${opts(ch.suspected_type, z.suspected_type, Object.fromEntries(Object.entries(TYPE).map(([k, v]) => [k, v[0]])))}</select></label>
    <label>ความเชื่อมั่น<select name="confidence">${opts(ch.confidence, z.confidence, CONF)}</select></label>
    <label>trace เริ่ม<input name="trace_start" type="number" value="${z.trace_start}"></label>
    <label>trace สิ้นสุด<input name="trace_end" type="number" value="${z.trace_end}"></label>
    <label>เวลาบน (ns)<input name="time_top_ns" type="number" step="0.1" value="${fmt(z.time_top_ns, 1)}"></label>
    <label>เวลาล่าง (ns)<input name="time_bottom_ns" type="number" step="0.1" value="${fmt(z.time_bottom_ns, 1)}"></label>
    <label class="full">ระดับการดำเนินการ<select name="action"><option value="auto">ตามกฎ (${esc(ACT[z.suggested_action] || "")})</option>${opts(ch.action, z.action ?? "", ACT)}</select></label>
    <label class="full">หมายเหตุ<textarea name="note" rows="2">${esc(z.note)}</textarea></label>
    <div class="full zbtns"><button class="btn primary" type="submit">บันทึก</button>
      <button class="btn" type="button" data-redraw="${z.zone_id}">วาดกรอบใหม่บนภาพ</button>
      <button class="btn ghost" type="button" data-cancel>ยกเลิก</button></div>
  </form>`;
}

function renderRecipe(l) {
  $("#stepRows").innerHTML = l.steps.map((s) => `<tr><td>${esc(STEP[s.step] || s.step)}</td>
    <td class="small">${esc(Object.entries(s.params).map(([k, v]) => `${k}=${Array.isArray(v) ? v.join("–") : v}`).join(", "))}</td>
    <td class="small muted">${esc(s.reason)}</td><td>${s.auto ? "โปรแกรม" : "<b>ผู้ใช้</b>"}</td></tr>`).join("");
  const o = l.options, f = $("#optForm");
  $("#recipeNote").textContent = Object.keys(o).length ? "(มีค่าที่ผู้ใช้ตั้ง)" : "(อัตโนมัติทั้งหมด)";
  for (const el of f.elements) {
    if (!el.name) continue;
    if (el.name === "band_lo") el.value = o.band_mhz ? o.band_mhz[0] : "";
    else if (el.name === "band_hi") el.value = o.band_mhz ? o.band_mhz[1] : "";
    else if (el.type === "checkbox") el.checked = o[el.name] ?? true;
    else el.value = o[el.name] ?? "";
  }
  $("#sgyLink").href = `/api/jobs/${S.job.job_id}/lines/${l.line_id}/export/sgy`;
  $("#csvLink").href = `/api/jobs/${S.job.job_id}/lines/${l.line_id}/export/csv`;
}

function buildInfoForm() {
  const src = $("#infoForm"), dst = $("#infoForm2");
  dst.innerHTML = src.innerHTML + `<button class="btn" type="submit">บันทึกข้อมูลงาน</button>`;
  const vals = { ...S.job.info, lang: S.job.lang, reviewer: S.job.reviewer };
  for (const el of dst.elements) if (el.name && vals[el.name] != null) el.value = vals[el.name];
}

// ------------------------------------------------------------------ actions
function select(id, scroll = true) {
  S.sel = id;
  $$(".zbox").forEach((b) => b.classList.toggle("sel", b.dataset.zone === id));
  $$(".zcard").forEach((c) => c.classList.toggle("sel", c.dataset.card === id));
  if (scroll) $(`[data-card="${id}"]`)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
}
async function update(promise, msg) {
  try { S.job = await promise; render(); if (S.sel) select(S.sel, false); if (msg) toast(msg); }
  catch (e) { toast("ผิดพลาด: " + e.message); }
}
function review(zid, action, changes, note) {
  const body = { action }; if (changes) body.changes = changes; if (note != null) body.note = note;
  S.editing = null;
  return update(post(`/api/jobs/${S.job.job_id}/lines/${S.line}/zones/${zid}/review`, body),
    { accept: `ยอมรับโซน ${zid}`, reject: `ปฏิเสธโซน ${zid}`, edit: `บันทึกการแก้ไขโซน ${zid}`, reset: `คืนค่าโซน ${zid}` }[action]);
}
function startDraw(forZone) {
  S.drawFor = forZone; document.body.classList.add("drawing");
  toast(forZone ? `ลากกรอบใหม่ของโซน ${forZone} บนภาพใดก็ได้` : "ลากกรอบบนภาพเพื่อเพิ่มโซน (Esc เพื่อยกเลิก)");
}
function stopDraw() { S.drawFor = null; document.body.classList.remove("drawing"); $$(".drawbox").forEach((d) => d.remove()); }

function pos(e, wrap) {
  const r = wrap.getBoundingClientRect(), l = line();
  const fx = Math.min(Math.max((e.clientX - r.left) / r.width, 0), 1), fy = Math.min(Math.max((e.clientY - r.top) / r.height, 0), 1);
  return { fx, fy, trace: Math.min(Math.floor(fx * l.n_traces), l.n_traces - 1), t: fy * l.time_window_ns };
}

document.addEventListener("mousemove", (e) => {
  const w = e.target.closest?.(".imgwrap");
  if (w && line()) {
    const p = pos(e, w), l = line();
    $("#readout").textContent = `trace ${p.trace}${l.dx_m ? ` · ระยะ ${(p.trace * l.dx_m).toFixed(2)} m` : ""} · เวลา ${p.t.toFixed(1)} ns · ลึก ${(p.t * l.velocity_m_per_ns / 2).toFixed(2)} m (v ${l.velocity_m_per_ns.toFixed(3)})`;
  }
  if (S.drag) {
    const p = pos(e, S.drag.wrap), a = S.drag.start;
    Object.assign(S.drag.box.style, { left: Math.min(a.fx, p.fx) * 100 + "%", width: Math.abs(p.fx - a.fx) * 100 + "%",
      top: Math.min(a.fy, p.fy) * 100 + "%", height: Math.abs(p.fy - a.fy) * 100 + "%" });
    S.drag.end = p;
  }
});
document.addEventListener("mousedown", (e) => {
  const w = e.target.closest?.(".imgwrap");
  if (!w || S.drawFor === null || e.button !== 0) return;
  e.preventDefault();
  const box = document.createElement("div"); box.className = "drawbox"; w.appendChild(box);
  S.drag = { wrap: w, start: pos(e, w), box };
});
document.addEventListener("mouseup", async () => {
  if (!S.drag) return;
  const { start: a, end: b } = S.drag; const target = S.drawFor;
  S.drag = null; stopDraw();
  if (!b || Math.abs(b.trace - a.trace) < 2) { toast("กรอบเล็กเกินไป"); return; }
  const vals = { trace_start: Math.min(a.trace, b.trace), trace_end: Math.max(a.trace, b.trace),
    time_top_ns: +Math.min(a.t, b.t).toFixed(2), time_bottom_ns: +Math.max(a.t, b.t).toFixed(2) };
  if (target === "") {
    await update(post(`/api/jobs/${S.job.job_id}/lines/${S.line}/zones`, vals), "เพิ่มโซนแล้ว เลือกประเภทในการ์ดได้");
  } else {
    S.editing = target;
    const f = $(`[data-editform="${target}"]`);
    if (f) for (const [k, v] of Object.entries(vals)) f.elements[k].value = v;
    toast("ใส่ตำแหน่งใหม่ในฟอร์มแล้ว กดบันทึกเพื่อยืนยัน");
  }
});
S.drawFor = null;

document.addEventListener("click", (e) => {
  const t = e.target;
  const rm = t.closest("[data-rm]"); if (rm) { S.files.splice(+rm.dataset.rm, 1); renderFiles(); return; }
  const li = t.closest("[data-line]"); if (li) { S.line = li.dataset.line; S.sel = null; S.editing = null; render(); return; }
  const zb = t.closest(".zbox"); if (zb && S.drawFor === null) { select(zb.dataset.zone); return; }
  const btn = t.closest("[data-act]");
  if (btn) {
    const z = btn.dataset.z, a = btn.dataset.act; select(z, false);
    if (a === "edit") { S.editing = S.editing === z ? null : z; renderCards(line()); select(z, false); }
    else if (a === "delete") { if (confirm(`ลบโซน ${z} ที่วาดเพิ่ม?`)) update(api(`/api/jobs/${S.job.job_id}/lines/${S.line}/zones/${z}`, { method: "DELETE" }), `ลบโซน ${z}`); }
    else review(z, a);
    return;
  }
  const crd = t.closest("[data-card]"); if (crd && !t.closest("form")) select(crd.dataset.card, false);
  if (t.closest("[data-cancel]")) { S.editing = null; renderCards(line()); }
  const rd = t.closest("[data-redraw]"); if (rd) startDraw(rd.dataset.redraw);
});
document.addEventListener("submit", (e) => {
  const f = e.target.closest("[data-editform]");
  if (f) {
    e.preventDefault();
    const d = Object.fromEntries(new FormData(f));
    const note = d.note; delete d.note;
    review(f.dataset.editform, "edit", d, note);
  }
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") { stopDraw(); S.drag = null; }
  if ($("#reviewView").hidden || e.target.closest("input,textarea,select")) return;
  const l = line(); if (!l || !l.zones.length) return;
  const ids = l.zones.map((z) => z.zone_id), i = ids.indexOf(S.sel);
  const k = e.key.toLowerCase();
  if (k === "j") select(ids[Math.min(i + 1, ids.length - 1)]);
  else if (k === "k") select(ids[Math.max(i - 1, 0)]);
  else if (S.sel && k === "a") review(S.sel, "accept");
  else if (S.sel && k === "r") review(S.sel, "reject");
  else if (S.sel && k === "e") { S.editing = S.sel; renderCards(l); select(S.sel); }
});

// ------------------------------------------------------------------ wiring
const drop = $("#drop");
drop.addEventListener("click", () => $("#fileInput").click());
drop.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") $("#fileInput").click(); });
$("#fileInput").addEventListener("change", (e) => addFiles(e.target.files));
["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", (e) => addFiles(e.dataTransfer.files));
$("#runBtn").addEventListener("click", runUpload);
$("#demoBtn").addEventListener("click", runDemo);
$("#newJob").addEventListener("click", () => { if (confirm("เริ่มงานใหม่? งานนี้ยังเปิดได้จากโฟลเดอร์งานบนเครื่อง")) { location.hash = ""; location.reload(); } });
$("#drawBtn").addEventListener("click", () => startDraw(""));
$("#showArtifacts").addEventListener("change", refreshOverlays);
$("#showRejected").addEventListener("change", refreshOverlays);
$("#zoneFilter").addEventListener("change", () => renderCards(line()));
$("#infoForm2").addEventListener("submit", (e) => { e.preventDefault(); update(post(`/api/jobs/${S.job.job_id}/info`, infoValues(e.target)), "บันทึกข้อมูลงานแล้ว"); });

function optValues() {
  const f = $("#optForm"), o = {};
  for (const el of f.elements) {
    if (!el.name || el.name.startsWith("band_")) continue;
    if (el.type === "checkbox") { if (!el.checked) o[el.name] = false; }
    else if (el.value !== "") o[el.name] = el.value;
  }
  const lo = f.elements.band_lo.value, hi = f.elements.band_hi.value;
  if (lo && hi) o.band_mhz = [+lo, +hi];
  return o;
}
async function reprocess(options) {
  const l = line(), done = l.zones.filter((z) => z.review !== "auto").length;
  if (done && !confirm(`ประมวลผลใหม่จะหาโซนใหม่ และผลตรวจทาน ${done} โซนของเส้นนี้จะถูกล้าง (ยังเก็บไว้ใน labels) ทำต่อ?`)) return;
  busy(true, "กำลังประมวลผลเส้นนี้ใหม่…");
  S.sel = null; S.editing = null;
  await update(post(`/api/jobs/${S.job.job_id}/lines/${S.line}/reprocess`, { options }), "ประมวลผลใหม่แล้ว");
  busy(false);
}
$("#reprocessBtn").addEventListener("click", () => reprocess(optValues()));
$("#autoBtn").addEventListener("click", () => reprocess({}));

(async function boot() {
  const id = location.hash.slice(1);
  if (!id) return;
  try { S.job = await api(`/api/jobs/${id}`); openReview(); } catch (e) { history.replaceState(null, "", "/"); }
})();

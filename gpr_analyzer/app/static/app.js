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
  gain: "Gain (SEC)", velocity: "ความเร็วคลื่น", migration: "Migration", envelope: "Envelope / energy", agc_display: "AGC (แสดงผล)",
  display_qc: "ตรวจ trace (แสดงผล)", display_bandpass: "Bandpass อัตโนมัติ (แสดงผล)", display_background: "ลบพื้นหลังอัตโนมัติ (แสดงผล)",
  display_gain: "Gain อัตโนมัติ (แสดงผล)", display_contrast: "Contrast อัตโนมัติ (แสดงผล)" };
const VIEWS = [
  ["grey", "ขาวดำ (รักษาแอมพลิจูด ใช้ให้คะแนน)", ""],
  ["high_gain", "Gain สูง (AGC ใช้ดูเท่านั้น)", "สี = ขั้วสัญญาณ แอมพลิจูดเทียบกันข้ามภาพไม่ได้"],
  ["envelope", "พลังงานสะท้อน (Hilbert envelope)", "สีแดง = สะท้อนแรง ไม่ได้แปลว่าเป็นโพรง"],
  ["viewpoint", "แบบหน้าจอ ViewPoint (ImpulseRadar)", "เลียนแบบภาพของ ViewPoint ใช้เทียบกับโปรแกรมของเครื่อง ใช้ดูเท่านั้น"],
  ["autodisplay", "ฟิลเตอร์อัตโนมัติ (ใช้ดูเท่านั้น)", "โปรแกรมเลือก bandpass, ลบพื้นหลัง, gain และ contrast จากข้อมูลเส้นนี้เอง ดูค่าที่เลือกในตารางขั้นตอนประมวลผล gain เท่ากันทุก trace จึงเทียบความแรงตามแนวยาวได้"],
];
const FAMILY = { iprh: "header", iprb: "data", rad: "header", rd3: "data", rd7: "data", hd: "header", dt1: "data", dzt: "data", sgy: "data", segy: "data" };
const COMPANION = ["txt", "cor", "gps", "csv", "xml", "ini", "json", "log", "tmf", "mrk"];

const REF_DRAW = "__ref__";  // S.drawFor value while drawing a reference pick
const VERDICT = { match: "ตรงกัน", partial: "ตรงบางส่วน", missed: "เราไม่พบ", unsure: "ไม่แน่ใจ",
  extra: "เราพบเกิน (ยังไม่ตัดสิน)", extra_real: "เราพบเพิ่ม น่าจะจริง", extra_false: "เราพบเกิน ผิด" };
const RATING = { better: "ดีกว่า", same: "พอ ๆ กัน", worse: "แย่กว่า", missing: "ยังไม่มี", untested: "ยังไม่ได้ลอง" };
const META = { n_traces: "จำนวน trace", time_window_ns: "Time window (ns)", length_m: "ความยาวเส้น (m)",
  velocity_m_per_ns: "ความเร็วคลื่น (m/ns)", max_depth_m: "ความลึกสูงสุด (m)" };

const ZOOM_DRAW = "__zoom__";  // S.drawFor value while dragging a zoom box
const SECTION_DRAW = "__section__";  // ... while marking a section whose results are not used
const LSTATUS = { pending: ["⏳", "รอตรวจ"], approved: ["✓", "ผ่าน ใช้ในรายงาน"], redo: ["↻", "ต้องประมวลผลใหม่"] };
const S = { files: [], job: null, line: null, sel: null, editing: null, drawFor: null,
  view: { x0: 0, x1: 1, y0: 0, y1: 1 }, cursor: null, hidden: new Set(store("hiddenViews", ["viewpoint"])) };

function store(key, dflt) {  // per-browser preferences; the page works without them
  try { const v = localStorage.getItem("gpr." + key); return v == null ? dflt : JSON.parse(v); } catch (e) { return dflt; }
}
function keep(key, v) { try { localStorage.setItem("gpr." + key, JSON.stringify(v)); } catch (e) { /* private mode */ } }

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
  return ["ยังไม่รู้จักรูปแบบ จะลองแล้วแจ้งผล", "bad"];
}
function renderFiles() {
  const tb = $("#fileList tbody");
  tb.innerHTML = S.files.map((f, i) => {
    const [r, c] = role(f.name);
    return `<tr><td>${esc(f.name)}</td><td>${(f.size / 1024).toFixed(0)} KB</td><td><span class="role ${c}">${r}</span></td>
      <td><button class="btn ghost" data-rm="${i}">ลบ</button></td></tr>`;
  }).join("");
  $("#fileList").hidden = !S.files.length;
  updateRunBtn();
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
  if (S.scan && selectedLines().length) return runFolder();
  const fd = new FormData();
  S.files.forEach((f) => fd.append("files", f, f.name));
  busy(true, `กำลังส่ง ${S.files.length} ไฟล์เข้าโปรแกรม…`);
  try {
    const job = await api("/api/jobs", { method: "POST", body: fd });
    S.job = await post(`/api/jobs/${job.job_id}/info`, infoValues($("#infoForm")));
    openProgress();
  } catch (e) { toast("ผิดพลาด: " + e.message); } finally { busy(false); }
}
async function runDemo() {
  busy(true, "กำลังสร้างข้อมูลสังเคราะห์…");
  try { S.job = await post("/api/demo", {}); openProgress(); } catch (e) { toast("ผิดพลาด: " + e.message); } finally { busy(false); }
}

// ------------------------------------------------------------------ data folder: pick, browse, check
const LEVEL = { ok: ["✓ ครบ", "ok"], warn: ["⚠ ควรตรวจ", "warn"], error: ["✗ ใช้ไม่ได้", "bad"] };
async function pickFolder() {
  try {
    const r = await post("/api/pick-folder", { start: $("#folderPath").value });
    if (r.path) { $("#folderPath").value = r.path; scanFolder(); }
  } catch (e) { toast("เปิดหน้าต่างเลือกโฟลเดอร์ไม่ได้ ใช้ปุ่ม 'เรียกดู…' แทน"); browse($("#folderPath").value); }
}
async function browse(path) {
  let r;
  try { r = await api(`/api/fs?path=${encodeURIComponent(path || "")}`); } catch (e) { toast(e.message); return; }
  const b = $("#browser"); b.hidden = false;
  b.innerHTML = `<div class="browser-head"><b>${esc(r.path || "เลือกไดรฟ์หรือโฟลเดอร์")}</b>
      ${r.path ? `<span class="muted small">ไฟล์ GPR ${r.gpr_files} ไฟล์</span>` : ""}
      <span class="spacer"></span>${r.path ? `<button class="btn primary small" data-usefolder="${esc(r.path)}">ใช้โฟลเดอร์นี้</button>` : ""}
      <button class="btn ghost small" data-closebrowser>ปิด</button></div>
    <ul>${r.parent != null ? `<li data-browse="${esc(r.parent)}">⬆ ขึ้นไป</li>` : ""}
      ${r.dirs.map((d) => `<li data-browse="${esc(d.path)}">📁 ${esc(d.name)}</li>`).join("") || `<li class="muted">ไม่มีโฟลเดอร์ย่อย</li>`}</ul>`;
}
async function scanFolder() {
  const path = $("#folderPath").value.trim();
  if (!path) { toast("เลือกหรือพิมพ์ที่อยู่โฟลเดอร์ก่อน"); return; }
  busy(true, "กำลังตรวจไฟล์ในโฟลเดอร์…");
  try {
    S.scan = await post("/api/scan", { path, recursive: $("#recursive").checked });
    for (const ln of S.scan.lines) ln.include = ln.status !== "error";
    keep("lastFolder", path);
    $("#browser").hidden = true;
    renderScan();
  } catch (e) { toast("ตรวจโฟลเดอร์ไม่ได้: " + e.message); } finally { busy(false); }
}
const selectedLines = () => (S.scan?.lines || []).filter((l) => l.include);
function renderScan() {
  const r = S.scan, sm = r.summary;
  $("#scanResult").hidden = false;
  $("#scanSummary").innerHTML = `<b>${esc(r.path)}</b> · ${r.n_files} ไฟล์ ·
    <span class="chip">${sm.lines} เส้น</span><span class="chip ok-y">ครบ ${sm.ok}</span>
    <span class="chip warn">ควรตรวจ ${sm.warn}</span><span class="chip ok-n">ใช้ไม่ได้ ${sm.error}</span>
    <span class="chip">ไม่มี GPS ${sm.no_gps}</span><span class="chip">รวม ${fmt(sm.length_m, 1)} m</span>
    ${r.unassigned.length ? `<span class="small muted" title="${esc(r.unassigned.join("\n"))}">ไฟล์อื่น ${r.unassigned.length} ไฟล์ (ไม่ได้ใช้)</span>` : ""}`;
  $("#scanRows").innerHTML = r.lines.map((l, i) => {
    const [lab, cls] = LEVEL[l.status];
    const iss = l.issues.filter((x) => x.level !== "info").map((x) => esc(x.text)).join("<br>")
      || `<span class="muted">${l.issues.map((x) => esc(x.text)).join("<br>") || "–"}</span>`;
    return `<tr data-scanrow="${i}" class="${l.include ? "" : "off"}">
      <td><input type="checkbox" data-include="${i}" ${l.include ? "checked" : ""} ${l.status === "error" ? "disabled" : ""}></td>
      <td><span class="role ${cls}">${lab}</span></td>
      <td><b>${esc(l.name)}</b><br><span class="small muted">${esc(l.antenna || l.family)}${l.date ? " · " + esc(l.date) : ""}</span></td>
      <td>${l.n_traces ?? "–"}</td><td>${l.length_m != null ? fmt(l.length_m, 1) + " m" : "–"}</td>
      <td>${l.gps_fixes ? `${l.gps_fixes} จุด` + (l.length_gps_m != null ? `<br><span class="small muted">${fmt(l.length_gps_m, 1)} m</span>` : "") : `<span class="muted">ไม่มี</span>`}</td>
      <td>${l.markers || 0}</td>
      <td class="range"><input type="number" step="0.1" min="0" data-range="${i}:0" placeholder="0" value="${l.range?.[0] ?? ""}" ${l.dx_m ? "" : "disabled"}>–
        <input type="number" step="0.1" min="0" data-range="${i}:1" placeholder="${l.length_m != null ? fmt(l.length_m, 1) : ""}" value="${l.range?.[1] ?? ""}" ${l.dx_m ? "" : "disabled"}></td>
      <td class="small">${iss}</td></tr>`;
  }).join("") || `<tr><td colspan="9" class="muted">ไม่พบไฟล์ GPR ในโฟลเดอร์นี้</td></tr>`;
  updateRunBtn();
  drawScanMap();
}
function updateRunBtn() {
  const n = selectedLines().length;
  $("#runBtn").disabled = !(n || S.files.length);
  $("#runBtn").textContent = n ? `เริ่มประมวลผล ${n} เส้นจากโฟลเดอร์` : "เริ่มวิเคราะห์";
}
function drawScanMap() {
  const lines = S.scan.lines.filter((l) => l.track);
  $("#scanMap").hidden = !lines.length || typeof L === "undefined";
  if ($("#scanMap").hidden) return;
  if (!S.scanMap) {
    S.scanMap = L.map("scanMap", { preferCanvas: true, maxZoom: 22 });
    const [, url, attr] = BASEMAPS[store("scanBase", "satellite")] || BASEMAPS.satellite;
    L.tileLayer(url, { attribution: attr, maxNativeZoom: 19, maxZoom: 22 }).addTo(S.scanMap);
    L.control.scale({ imperial: false }).addTo(S.scanMap);
    S.scanLayer = L.layerGroup().addTo(S.scanMap);
  }
  S.scanLayer.clearLayers();
  const col = { ok: "#2f9e44", warn: "#e8590c", error: "#c92a2a" };
  S.scan.lines.forEach((l, i) => {
    if (!l.track) return;
    L.polyline(l.track, { color: "#fff", weight: 6, opacity: l.include ? 0.9 : 0.3 }).addTo(S.scanLayer);
    const pl = L.polyline(l.track, { color: col[l.status], weight: 3, opacity: l.include ? 1 : 0.4, dashArray: l.include ? null : "4 4" })
      .addTo(S.scanLayer).bindTooltip(`${esc(l.name)} · ${l.length_m != null ? fmt(l.length_m, 1) + " m" : ""} · ${LEVEL[l.status][0]}`, { sticky: true });
    pl.on("click", () => $(`[data-scanrow="${i}"]`)?.scrollIntoView({ block: "center", behavior: "smooth" }));
    L.circleMarker(l.track[0], { radius: 3, color: col[l.status], fillOpacity: 1 }).addTo(S.scanLayer);
  });
  S.scanMap.fitBounds(lines.flatMap((l) => l.track), { padding: [20, 20], maxZoom: 20 });
  setTimeout(() => S.scanMap.invalidateSize(), 50);
}
async function runFolder() {
  const ranges = {}, sel = selectedLines();
  for (const l of sel) if (l.range && (l.range[0] !== "" || l.range[1] !== "")) ranges[l.primary] = l.range;
  busy(true, "กำลังเริ่มงาน…");
  try {
    const info = infoValues($("#infoForm"));
    S.job = await post("/api/jobs/from-folder", { path: S.scan.path, recursive: S.scan.recursive, include: sel.map((l) => l.primary), ranges, info });
    S.job = await post(`/api/jobs/${S.job.job_id}/info`, info);
    openProgress();
  } catch (e) { toast("ผิดพลาด: " + e.message); } finally { busy(false); }
}

// ------------------------------------------------------------------ processing progress (polls the server)
const ESTATE = { waiting: "รอคิว", reading: "กำลังอ่าน", processing: "กำลังประมวลผล", saving: "กำลังบันทึก", done: "✓ เสร็จ", error: "✗ ผิดพลาด" };
const STEP_TH = { read: "อ่านไฟล์", center_frequency: "หาความถี่", dewow: "Dewow", time_zero: "Time-zero", bandpass: "Bandpass",
  background_removal: "ลบพื้นหลัง", gain: "Gain", velocity: "หาความเร็วคลื่น", migration: "Migration", envelope: "Envelope",
  agc: "AGC", viewpoint_display: "ภาพแบบ ViewPoint", display_qc: "ตรวจ trace", display_bandpass: "Bandpass อัตโนมัติ",
  display_background: "ลบพื้นหลังอัตโนมัติ", display_gain: "Gain อัตโนมัติ", display_contrast: "Contrast อัตโนมัติ",
  detect: "ค้นหาโซนผิดปกติ", checkpoint: "บันทึก checkpoint" };
function openProgress() {
  $("#uploadView").hidden = true; $("#reviewView").hidden = true; $("#progressView").hidden = false; $("#exports").hidden = true;
  history.replaceState(null, "", "#" + S.job.job_id);
  $("#logLink").href = jobUrl("/log.txt");
  S.autoOpen = true;
  renderProgress(S.job.progress);
  pollProgress();
}
function renderProgress(p) {
  const state = { processing: "กำลังประมวลผล", waiting: "รอเริ่ม", done: "ประมวลผลเสร็จแล้ว", interrupted: "งานค้าง (ทำต่อได้)" }[p.state] || p.state;
  $("#progTitle").textContent = `${state} · ${p.done}/${p.total} ไฟล์`;
  $("#progBar").style.width = `${Math.round(p.fraction * 100)}%`;
  $("#progNote").textContent = p.started ? `เริ่ม ${new Date(p.started).toLocaleTimeString("th-TH")}` + (p.finished ? ` · เสร็จ ${new Date(p.finished).toLocaleTimeString("th-TH")}` : "") : "";
  $("#progRows").innerHTML = p.entries.map((e) => `<tr class="st-${e.state}"><td>${e.index + 1}</td><td>${esc(e.name)}</td>
    <td>${e.range_m ? `${e.range_m[0] ?? 0}–${e.range_m[1] ?? "จบ"}` : "ทั้งเส้น"}</td><td>${ESTATE[e.state] || e.state}</td>
    <td class="small">${e.error ? `<span class="bad">${esc(e.error)}</span>` : esc(STEP_TH[e.step] || e.step || "")}</td>
    <td>${e.line_ids.map((id) => `<a href="#" data-openline="${esc(id)}">${esc(id)}</a>`).join(" ")}</td></tr>`).join("");
  const box = $("#logBox"), atEnd = box.scrollTop + box.clientHeight >= box.scrollHeight - 10;
  box.textContent = p.log.join("\n");
  if (atEnd) box.scrollTop = box.scrollHeight;
  $("#toReview").disabled = !p.done;
  $("#resumeBtn").hidden = p.state !== "interrupted";
}
async function pollProgress() {
  clearTimeout(S.pollH);
  if (!S.job) return;
  let p;
  try { p = await api(jobUrl("/progress")); } catch (e) { S.pollH = setTimeout(pollProgress, 3000); return; }
  if (!$("#progressView").hidden) renderProgress(p);
  if (!$("#reviewView").hidden) {
    renderBanner(p);
    if (p.n_lines !== S.job.lines.length && !S.editing && !S.drag && !S.pan) {
      S.job = await api(jobUrl("")); renderTop(); renderLines(); M.load();
      window.dispatchEvent(new CustomEvent("gpr:job", { detail: { job: S.job, line: S.line } }));
    }
  }
  if (p.state === "processing" || p.state === "waiting") S.pollH = setTimeout(pollProgress, 1000);
  else if (p.state === "done" && !$("#progressView").hidden && S.autoOpen) {
    S.job = await api(jobUrl("")); openReview(); toast("ประมวลผลเสร็จแล้ว");
  } else if (!$("#reviewView").hidden && p.n_lines !== S.job.lines.length) { S.job = await api(jobUrl("")); render(); M.load(); }
}
function renderBanner(p) {
  const b = $("#runBanner");
  b.hidden = p.state === "done";
  if (p.state === "interrupted") b.innerHTML = `⚠ งานนี้ค้างอยู่: เสร็จ ${p.done}/${p.total} ไฟล์ <button class="btn small" data-resume>ทำต่อ</button> <a href="#" data-showprog>ดูรายละเอียด</a>`;
  else if (!b.hidden) {
    const cur = p.entries.find((e) => !["waiting", "done", "error"].includes(e.state));
    b.innerHTML = `<div class="pbar"><span style="width:${Math.round(p.fraction * 100)}%"></span></div>
      กำลังประมวลผล ${p.done}/${p.total} ไฟล์${cur ? ` · ${esc(cur.name)}: ${esc(STEP_TH[cur.step] || cur.step)}` : ""}
      · เส้นใหม่จะขึ้นในรายการเมื่อเสร็จ <a href="#" data-showprog>ดูความคืบหน้า / log</a>`;
  }
}
async function resumeJob() {
  try { S.job = await post(jobUrl("/resume"), {}); toast("ทำต่อจากเส้นที่ค้าง"); pollProgress(); if (!$("#progressView").hidden) renderProgress(S.job.progress); }
  catch (e) { toast("ผิดพลาด: " + e.message); }
}

async function loadPastJobs() {
  let jobs = [];
  try { jobs = await api("/api/jobs"); } catch (e) { return; }
  $("#pastJobs").hidden = !jobs.length;
  $("#pastJobs tbody").innerHTML = jobs.map((j) => {
    const r = j.review || {}, n = (r.auto || 0) + (r.accepted || 0) + (r.edited || 0) + (r.rejected || 0), c = j.compare;
    const cmp = c && c.n_ref ? `อ้างอิง ${c.n_ref} จุด · ตรง ${c.match + c.partial} · เราไม่พบ ${c.missed} · เราพบเกิน ${c.extra}` : `<span class="muted">ยังไม่ได้เทียบ</span>`;
    return `<tr><td><b>${esc(j.project || "งานสำรวจ")}</b><br><span class="small muted">${esc(j.job_id)}</span></td>
      <td class="small">${(j.files || []).map(esc).join("<br>")}</td>
      <td class="small">${j.state === "interrupted" ? `<span class="badge draft">ค้าง ${j.n_done ?? "?"}/${j.n_planned ?? "?"} ทำต่อได้</span>`
        : j.state === "processing" ? "กำลังประมวลผล" : "เสร็จ"}</td>
      <td>${n - (r.auto || 0)}/${n}</td><td class="small">${cmp}</td>
      <td><a class="btn" href="#${esc(j.job_id)}" data-open="${esc(j.job_id)}">เปิด</a></td></tr>`;
  }).join("");
}
async function openJob(id) {
  busy(true, "กำลังเปิดงาน…");
  try {
    S.job = await api(`/api/jobs/${id}`);
    if (!S.job.lines.length && S.job.progress.total) openProgress(); else { openReview(); pollProgress(); }
  } catch (e) { toast("เปิดงานไม่ได้: " + e.message); history.replaceState(null, "", "/"); }
  finally { busy(false); }
}

// ------------------------------------------------------------------ review
function openReview() {
  S.autoOpen = false;
  $("#uploadView").hidden = true; $("#progressView").hidden = true; $("#reviewView").hidden = false; $("#exports").hidden = false;
  renderBanner(S.job.progress);
  history.replaceState(null, "", "#" + S.job.job_id);
  S.line = S.job.lines[0]?.line_id || null;
  buildInfoForm();
  render();
  M.load();
}
const line = () => S.job.lines.find((l) => l.line_id === S.line);
const zoneOf = (id) => line()?.zones.find((z) => z.zone_id === id);

function render() {
  renderTop(); renderLines(); renderLine();
  window.dispatchEvent(new CustomEvent("gpr:job", { detail: { job: S.job, line: S.line } }));
}
window.getGprSceneContext = () => ({ job: S.job, line: S.line });
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
    return `<li data-line="${l.line_id}" class="${l.line_id === S.line ? "active" : ""}"><span class="lst ${l.status}" title="${LSTATUS[l.status][1]}">${LSTATUS[l.status][0]}</span> <b>${l.line_id}</b> ${esc(l.source_file)}
      <span class="small muted">${l.zones.length} โซน${todo ? ` · รอตรวจ ${todo}` : " · ตรวจครบ"}</span></li>`;
  }).join("") || `<li class="muted">ไม่มีเส้นที่อ่านได้</li>`;
  const probs = [...S.job.problems.map((p) => p.error === "unknown_format"
      ? `${esc(p.file)}: ยังอ่านรูปแบบนี้ไม่ได้ (โปรแกรมรับไฟล์ ImpulseRadar .iprh + .iprb เป็นหลัก)`
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
    · ระยะ trace ${l.dx_m ? fmt(l.dx_m, 3) + " m" : "ไม่ทราบ"}
    ${l.crop ? `· <b>ประมวลผลเฉพาะช่วง ${fmt(l.crop.start_m, 1)}–${fmt(l.crop.end_m, 1)} m</b> (trace ${l.crop.trace_start}–${l.crop.trace_end} จาก ${l.crop.n_traces_file} ของไฟล์; เลข trace บนภาพนับจากต้นช่วง ระยะ m นับจากต้นเส้น)` : ""} · ความเร็ว ${fmt(l.velocity_m_per_ns, 3)} m/ns (${esc(l.velocity_source)})
    ${l.companions.length ? "· ไฟล์ประกอบ: " + l.companions.map((c) => `<span title="${esc(c.text || "")}">${esc(c.name)}</span>`).join(", ") : ""}`;
  const stamp = Date.now();
  const views = VIEWS.filter(([v]) => (l.views || ["grey", "high_gain", "envelope"]).includes(v));
  $("#viewToggles").innerHTML = views.map(([v, title]) => `<label class="toggle small" title="${esc(title)}">
    <input type="checkbox" data-viewtoggle="${v}" ${S.hidden.has(v) ? "" : "checked"}> ${esc(title.split(" (")[0])}</label>`).join("");
  $("#panes").innerHTML = views.filter(([v]) => !S.hidden.has(v)).map(([v, title, note]) => `
    <div class="pane" data-view="${v}">
      <div class="pane-cap"><b>${title}</b><span class="muted">${note}
        ${v === "envelope" ? `<span class="legend">-30 dB <span class="bar" style="background:linear-gradient(90deg,#30123b,#4686fb,#1ae4b6,#a2fc3c,#faba39,#e4460a,#7a0403)"></span> 0 dB</span>` : ""}</span></div>
      <div class="plot">
        <div class="yaxis left" data-axis="t"></div>
        <div class="imgwrap" data-view="${v}"><div class="zoomer"><img alt="${title}" draggable="false" src="/api/jobs/${S.job.job_id}/lines/${l.line_id}/${v}.png?${stamp}">${overlay(l)}</div></div>
        <div class="yaxis right" data-axis="d"></div>
      </div>
      <div class="xaxis" data-axis="x"></div>
    </div>`).join("") || `<p class="muted">เลือกภาพที่จะแสดงด้านบน</p>`;
  applyZoom();
  renderLineStatus(l);
  renderRecipe(l);
  renderCards(l);
  renderCompare(l);
}
function nice(span, n) {
  const raw = span / n, p = 10 ** Math.floor(Math.log10(raw)), m = raw / p;
  return (m < 1.5 ? 1 : m < 3 ? 2 : m < 7 ? 5 : 10) * p;
}
// Ticks for the zoom window S.view (fractions of the whole line, 0..1 on both axes).
function ticks(lo, hi, n, place) {
  const s = nice(hi - lo || 1, n), out = [];
  for (let v = Math.ceil(lo / s) * s; v <= hi + 1e-9; v += s) out.push(place(v));
  return out.join("");
}
function depthAt(l, t) {
  const h = l.velocity_m_per_ns * t / 2, s = (l.metadata?.antenna_separation_m || 0) / 2;
  return h > 0 ? Math.sqrt(Math.max((h + s) ** 2 - s * s, 0)) : h;
}
function timeAtDepth(l, d) {
  const s = (l.metadata?.antenna_separation_m || 0) / 2;
  return 2 * (d > 0 ? Math.sqrt(d * d + s * s) - s : d) / l.velocity_m_per_ns;
}
function yTicks(l, kind) {
  const T = l.time_window_ns, { y0, y1 } = S.view, f = (t) => ((t / T - y0) / (y1 - y0)) * 100;
  if (kind === "t") return ticks(y0 * T, y1 * T, 5, (t) => `<span style="top:${f(t)}%">${+t.toFixed(2)}</span>`) + `<span class="ttl">เวลา (ns)</span>`;
  return ticks(depthAt(l, y0 * T), depthAt(l, y1 * T), 5, (d) => `<span style="top:${f(timeAtDepth(l, d))}%">${+d.toFixed(2)}</span>`) + `<span class="ttl">ความลึก (m)</span>`;
}
function xTicks(l) {
  const N = l.n_traces, { x0, x1 } = S.view, f = (t) => ((t / N - x0) / (x1 - x0)) * 100;
  return ticks(x0 * N, x1 * N, 8, (t) => `<span style="left:${f(t)}%">${Math.round(t)}${l.dx_m ? `<br>${((t + (l.trace_offset || 0)) * l.dx_m).toFixed(1)}` : ""}</span>`)
    + `<span class="ttl">${l.dx_m ? "trace / ระยะ (m)" : "trace"}</span>`;
}

// ------------------------------------------------------------------ zoom (all panes share one window)
function applyZoom() {
  const l = line(); if (!l) return;
  const { x0, x1, y0, y1 } = S.view, w = x1 - x0, h = y1 - y0;
  $$(".zoomer").forEach((z) => Object.assign(z.style, { left: `${(-x0 / w) * 100}%`, width: `${100 / w}%`, top: `${(-y0 / h) * 100}%`, height: `${100 / h}%` }));
  $$('[data-axis="t"]').forEach((a) => (a.innerHTML = yTicks(l, "t")));
  $$('[data-axis="d"]').forEach((a) => (a.innerHTML = yTicks(l, "d")));
  $$('[data-axis="x"]').forEach((a) => (a.innerHTML = xTicks(l)));
  const zoomed = w < 0.999 || h < 0.999;
  $("#zoomInfo").textContent = zoomed ? `แสดง trace ${Math.round(x0 * l.n_traces)}–${Math.round(x1 * l.n_traces)}`
    + (l.dx_m ? ` (${((x0 * l.n_traces + (l.trace_offset || 0)) * l.dx_m).toFixed(1)}–${((x1 * l.n_traces + (l.trace_offset || 0)) * l.dx_m).toFixed(1)} m)` : "")
    + ` · ${(y0 * l.time_window_ns).toFixed(0)}–${(y1 * l.time_window_ns).toFixed(0)} ns` : "เต็มเส้น";
  drawGuides();
}
function setView(x0, x1, y0, y1) {
  const l = line(), minW = Math.min(1, 8 / Math.max(l.n_traces, 1)), minH = Math.min(1, 4 / Math.max(l.n_samples, 1));
  const fit = (a, b, min) => {
    let w = Math.min(Math.max(b - a, min), 1);
    a = Math.min(Math.max(a, 0), 1 - w);
    return [a, a + w];
  };
  [x0, x1] = fit(x0, x1, minW); [y0, y1] = fit(y0, y1, minH);
  S.view = { x0, x1, y0, y1 };
  applyZoom();
}
function zoomAt(fx, fy, factor, xOnly) {  // fx, fy: point that stays put (fractions of the whole line)
  const v = S.view;
  const nx = (a) => fx + (a - fx) * factor, ny = (a) => fy + (a - fy) * factor;
  setView(nx(v.x0), nx(v.x1), xOnly ? v.y0 : ny(v.y0), xOnly ? v.y1 : ny(v.y1));
}
function resetZoom() { S.view = { x0: 0, x1: 1, y0: 0, y1: 1 }; applyZoom(); }
function centreOn(trace) {  // keep the zoom, move the window so the trace is in the middle
  const l = line(), v = S.view, w = v.x1 - v.x0, c = (trace + 0.5) / l.n_traces;
  setView(c - w / 2, c + w / 2, v.y0, v.y1);
}
// Cursor trace (from the map) and the depth band shown on the map, drawn on every pane.
function drawGuides() {
  const l = line(); if (!l) return;
  $$(".guide").forEach((g) => g.remove());
  const band = M.data?.slice && M.band(), T = l.time_window_ns;
  $$(".zoomer").forEach((z) => {
    if (S.cursor != null) z.insertAdjacentHTML("beforeend", `<div class="guide vline" style="left:${((S.cursor + 0.5) / l.n_traces) * 100}%"></div>`);
    if (band && !$("#mapCard").hidden) {
      const t0 = timeAtDepth(l, band[0]) / T, t1 = timeAtDepth(l, band[1]) / T;
      z.insertAdjacentHTML("beforeend", `<div class="guide hband" style="top:${t0 * 100}%;height:${Math.max((t1 - t0) * 100, 0.3)}%"></div>`);
    }
  });
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
  for (const s of l.sections) h += `<div class="sbox" title="ไม่ใช้ผล ${esc(s.section_id)}: ${esc(s.reason)}"
      style="left:${(s.trace_start / N) * 100}%;width:${((s.trace_end - s.trace_start + 1) / N) * 100}%"><span class="lbl">ไม่ใช้ ${esc(s.section_id)}</span></div>`;
  const c = S.job.comparison?.lines[l.line_id];
  if (c && $("#showRef").checked) for (const r of c.rows) {
    if (r.kind === "ours_only") continue;
    const left = Math.max(r.ref_trace_start / N, 0) * 100, right = Math.min((r.ref_trace_end + 1) / N, 1) * 100;
    const top = Math.max(r.ref_time_top_ns / T, 0) * 100, bot = Math.min(r.ref_time_bottom_ns / T, 1) * 100;
    if (right <= left || bot <= top) continue;
    h += `<div class="rbox" title="อ้างอิง ${esc(r.ref_id)} ${esc(r.label)} (${esc(S.job.choices.ref_source[r.source])})"
      style="left:${left}%;width:${right - left}%;top:${top}%;height:${Math.max(bot - top, 0.8)}%"><span class="lbl">${esc(r.ref_id)}</span></div>`;
  }
  return h;
}
function refreshOverlays() {
  const l = line();
  $$(".zoomer").forEach((w) => { $$(".zbox,.art,.rbox,.sbox", w).forEach((e) => e.remove()); w.insertAdjacentHTML("beforeend", overlay(l)); });
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
      <span class="rev ${z.review}">${REV[z.review]}${z.manual ? " · วาดเพิ่ม" : ""}</span>
      ${z.excluded ? `<span class="chip sec" title="อยู่ในช่วงที่ไม่ใช้ผล จึงไม่อยู่ในรายงาน">ไม่ใช้ (${esc(z.excluded)})</span>` : ""}</header>
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

function renderLineStatus(l) {
  const secs = l.sections.map((s) => `<span class="chip sec" title="${esc(s.reason)}">✂ ${esc(s.section_id)}
      trace ${s.trace_start}–${s.trace_end}${s.distance_start_m != null ? ` (${fmt(s.distance_start_m, 1)}–${fmt(s.distance_end_m, 1)} m)` : ""}
      ${s.reason ? "· " + esc(s.reason) : ""} <a href="#" data-delsection="${esc(s.section_id)}" title="ใช้ผลช่วงนี้อีกครั้ง">✗</a></span>`).join("");
  $("#lineStatus").innerHTML = `<span class="small muted">สถานะเส้นนี้:</span>
    ${Object.entries(LSTATUS).map(([k, [ic, lab]]) => `<button class="btn small ${l.status === k ? "on st-" + k : "ghost"}" data-lstatus="${k}">${ic} ${lab}</button>`).join("")}
    <input class="small" id="statusNote" placeholder="หมายเหตุของเส้น (เช่น เหตุผลที่ต้องทำใหม่)" value="${esc(l.status_note)}">
    ${secs ? `<div class="secs"><span class="small muted">ช่วงที่ไม่ใช้ผล:</span> ${secs}</div>` : ""}`;
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

// ------------------------------------------------------------------ map (top view, after IDS AiMaps)
// Basemaps load from the internet; without it the tracks still show on a blank background with a scale.
const BASEMAPS = {
  satellite: ["ภาพดาวเทียม", "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    "ภาพดาวเทียม &copy; Esri, Maxar, Earthstar Geographics และ GIS User Community"],
  street: ["แผนที่ถนน", "https://tile.openstreetmap.org/{z}/{x}/{y}.png", "&copy; ผู้ร่วมพัฒนา <a href=\"https://www.openstreetmap.org/copyright\">OpenStreetMap</a>"],
};
function cmap(name, f) {  // f in 0..1 -> css colour
  f = Math.min(Math.max(f, 0), 1);
  if (name === "grey") { const g = Math.round(30 + 225 * f); return `rgb(${g},${g},${g})`; }
  if (name === "hot") return `rgb(${Math.round(255 * Math.min(f * 2.5, 1))},${Math.round(255 * Math.min(Math.max(f * 2.5 - 1, 0), 1))},${Math.round(255 * Math.max(f * 2.5 - 2, 0) * 2)})`;
  const c = (x) => Math.round(255 * Math.min(Math.max(1.5 - Math.abs(4 * f - x), 0), 1));  // jet
  return `rgb(${c(3)},${c(2)},${c(1)})`;
}
const M = {
  map: null, data: null, overview: [], diel: [], layers: {}, offline: false,
  init() {
    if (this.map || typeof L === "undefined") return !!this.map;
    this.map = L.map("map", { preferCanvas: true, zoomSnap: 0.25, maxZoom: 22 });
    const base = {};
    for (const [k, [name, url, attr]] of Object.entries(BASEMAPS)) {
      base[name] = L.tileLayer(url, { attribution: attr, maxNativeZoom: 19, maxZoom: 22 });
      base[name].on("tileerror", () => this.setOffline(true));
      base[name].on("tileload", () => this.setOffline(false));
    }
    base["ไม่มีแผนที่ฐาน (ออฟไลน์)"] = L.layerGroup();
    const pick = store("basemap", "ภาพดาวเทียม");
    (base[pick] || base["ภาพดาวเทียม"]).addTo(this.map);
    L.control.layers(base, null, { position: "topright" }).addTo(this.map);
    L.control.scale({ metric: true, imperial: false }).addTo(this.map);
    this.map.on("baselayerchange", (e) => { keep("basemap", e.name); this.setOffline(false); });
    for (const k of ["all", "slice", "diel", "zones", "marks", "hover"]) this.layers[k] = L.layerGroup().addTo(this.map);
    this.map.setView([13.7563, 100.5018], 5);
    $("#sliceDepth").value = store("sliceDepth", 6); $("#sliceThr").value = store("sliceThr", -40);
    $("#sliceCmap").value = store("sliceCmap", "jet"); $("#mapMode").value = store("mapMode", "energy");
    return true;
  },
  setOffline(on) {
    if (on === this.offline) return;
    this.offline = on;
    $("#mapNote").textContent = on ? "(โหลดแผนที่ฐานไม่ได้: ไม่มีอินเทอร์เน็ต แสดงเฉพาะแนวสแกน)" : "";
  },
  async load() {  // job overview + the selected line's track and depth slices
    if (!S.job || !this.init()) return;
    const l = line();
    try {
      this.overview = await api(jobUrl("/map.json"));
      this.data = l ? await api(lineUrl("/map.json")) : null;
      this.diel = await api(jobUrl("/dielectric.json"));
    } catch (e) { this.data = null; this.diel = []; }
    const sl = this.data?.slice;
    if (sl) $("#sliceDepth").max = sl.db.length - 1;
    const anyGps = this.overview.some((o) => o.has_gps);
    $("#mapCard").hidden = !anyGps;
    if (!anyGps) return;
    this.draw(true);
    setTimeout(() => this.map.invalidateSize(), 50);
  },
  band() {
    const sl = this.data?.slice; if (!sl) return null;
    const i = Math.min(+$("#sliceDepth").value, sl.db.length - 1);
    return [sl.depth_edges_m[i], sl.depth_edges_m[i + 1]];
  },
  pickDepth(depth) {  // from a click on the radargram
    const sl = this.data?.slice; if (!sl) return;
    let i = sl.depth_edges_m.findIndex((d, k) => k > 0 && depth < d) - 1;
    if (i < 0) i = sl.db.length - 1;
    $("#sliceDepth").value = i; keep("sliceDepth", i);
    this.drawSlice();
  },
  latlon(trace) {
    const d = this.data, g = d.slice.trace_groups, k = g[0][1] - g[0][0] + 1;
    return d.points[Math.min(Math.max(Math.floor(trace / k), 0), d.points.length - 1)];
  },
  draw(fit) {
    const ly = this.layers;
    ly.all.clearLayers(); ly.marks.clearLayers(); ly.hover.clearLayers();
    if ($("#mapAll").checked || !this.data?.has_gps) for (const o of this.overview) {
      if (!o.has_gps || o.line_id === S.line) continue;
      L.polyline(o.points, { color: "#fff", weight: 4, opacity: 0.9 }).addTo(ly.all);
      L.polyline(o.points, { color: "#1f6feb", weight: 2 }).addTo(ly.all)
        .bindTooltip(`${esc(o.line_id)} · ${esc(o.source_file)} (คลิกเพื่อเปิด)`, { sticky: true })
        .on("click", () => switchLine(o.line_id));
    }
    const d = this.data;
    $("#mapMsg").hidden = !!d?.has_gps;
    if (!d?.has_gps) {
      $("#mapMsg").textContent = `เส้น ${S.line} ไม่มีไฟล์ GPS (.cor) จึงวางบนแผนที่ไม่ได้` + (this.overview.some((o) => o.has_gps) ? " (แสดงเส้นอื่นที่มี GPS)" : "");
      ly.slice.clearLayers(); ly.zones.clearLayers(); this.drawSlice();
      const pts = this.overview.filter((o) => o.has_gps).flatMap((o) => o.points);
      if (fit && pts.length) this.map.fitBounds(pts, { padding: [20, 20] });
      return;
    }
    const [s, e] = d.ends;
    L.circleMarker(s, { radius: 5, color: "#fff", fillColor: "#2f9e44", fillOpacity: 1, weight: 2 }).addTo(ly.marks).bindTooltip(`${S.line} เริ่ม (trace 0)`);
    L.circleMarker(e, { radius: 5, color: "#fff", fillColor: "#c92a2a", fillOpacity: 1, weight: 2 }).addTo(ly.marks).bindTooltip(`${S.line} จบ (trace ${d.n_traces - 1})`);
    for (const m of d.markers) L.circleMarker([m.lat, m.lon], { radius: 4, color: "#000", fillColor: "#ffd43b", fillOpacity: 1, weight: 1 })
      .addTo(ly.marks).bindTooltip(`มาร์กเกอร์ trace ${m.trace}${m.type ? " · " + esc(m.type) : ""}`);
    $("#mapNote").textContent = this.offline ? $("#mapNote").textContent
      : `(GPS ${d.gps_fixes} จุด · ยาวตาม GPS ${fmt(d.length_gps_m, 1)} m${d.length_wheel_m ? ` · ตามล้อวัดระยะ ${fmt(d.length_wheel_m, 1)} m` : ""})`;
    this.drawSlice(); this.drawZones();
    if (fit) this.fit();
  },
  fit() { if (this.data?.has_gps) this.map.fitBounds(this.data.points, { padding: [30, 30], maxZoom: 21 }); },
  drawSlice() {
    const d = this.data, ly = this.layers.slice; ly.clearLayers();
    const mode = $("#mapMode").value; keep("mapMode", mode);
    $("#sliceThrBox").hidden = mode !== "energy";
    if (mode === "dielectric") { this.drawDiel(); return; }
    this.layers.diel.clearLayers();
    $("#mapHelp").textContent = "สี = พลังงานสะท้อนที่ความลึกที่เลือก เทียบกับค่าสูงสุดของเส้น (dB) ใช้ดูตำแหน่ง ไม่ใช่ความน่าจะเป็น";
    if (!d?.has_gps) return;
    const sl = d.slice, i = Math.min(+$("#sliceDepth").value, sl.db.length - 1), thr = +$("#sliceThr").value, name = $("#sliceCmap").value;
    const row = sl.db[i], [d0, d1] = this.band();
    $("#sliceDepthLbl").textContent = `${d0.toFixed(2)}–${d1.toFixed(2)} m`;
    $("#sliceThrLbl").textContent = thr <= sl.floor_db ? "ไม่ซ่อน" : `${thr} dB`;
    $("#sliceLegend").innerHTML = `<span class="legend">${sl.floor_db} dB <span class="bar" style="background:linear-gradient(90deg,${[0, .25, .5, .75, 1].map((f) => cmap(name, f)).join(",")})"></span> 0 dB</span>`;
    for (let k = 0; k < d.points.length - 1; k++) {
      const v = row[k], hide = v < thr;
      L.polyline([d.points[k], d.points[k + 1]], { color: hide ? "#0b1a5c" : cmap(name, (v - sl.floor_db) / -sl.floor_db), weight: 14, opacity: hide ? 0.55 : 0.95, lineCap: "butt" })
        .addTo(ly).on("click", () => this.goTo(sl.trace_groups[k][0]))
        .bindTooltip(`trace ${sl.trace_groups[k][0]}–${sl.trace_groups[k][1]} · ${v} dB ที่ลึก ${d0.toFixed(2)}–${d1.toFixed(2)} m`, { sticky: true });
    }
    drawGuides();
    keep("sliceDepth", i); keep("sliceThr", thr); keep("sliceCmap", name);
  },
  // Dielectric: εr = (c/v)² at each fitted hyperbola, the average from the ground surface down to its apex.
  // Points shallower than the slider show faded, points in the selected band show large, deeper ones are hidden.
  drawDiel() {
    const ly = this.layers.diel; ly.clearLayers();
    const name = $("#sliceCmap").value, band = this.band();
    const col = (e) => cmap(name, Math.log(e) / Math.log(81));  // log scale: air 1 … water 81
    $("#sliceDepthLbl").textContent = band ? `${band[0].toFixed(2)}–${band[1].toFixed(2)} m` : "ทุกความลึก";
    $("#sliceLegend").innerHTML = `<span class="legend">εr 1 <span class="bar" style="background:linear-gradient(90deg,${[0, .25, .5, .75, 1].map((f) => cmap(name, f)).join(",")})"></span> 81 (กลาง = 9)</span>`;
    const lines = this.diel.filter((x) => x.has_gps && ($("#mapAll").checked || x.line_id === S.line));
    const n = lines.reduce((a, x) => a + x.points.length, 0);
    $("#mapHelp").textContent = `สี = ค่า dielectric (εr) จากการฟิตไฮเพอร์โบลา ${n} จุด เป็นค่าเฉลี่ยจากผิวดินถึงความลึกของจุดนั้น ` +
      "จุดใหญ่ = อยู่ในช่วงความลึกที่เลือก จุดจาง = ตื้นกว่า · ไม่มีการเดาค่าระหว่างจุด · แนวสีเทาประ = ใช้ความเร็วค่าตั้งต้น/จากไฟล์ ไม่ได้วัด";
    for (const x of lines) {
      const o = this.overview.find((v) => v.line_id === x.line_id);
      if (o?.has_gps) {  // the velocity the line was processed with
        const measured = x.velocity_source === "hyperbola_fit";
        L.polyline(o.points, { color: measured ? col(x.eps_r) : "#adb5bd", weight: x.line_id === S.line ? 5 : 3, opacity: 0.9, dashArray: measured ? null : "4 6" })
          .addTo(ly).bindTooltip(`${esc(x.line_id)} · ใช้ v ${x.velocity_m_per_ns} m/ns (εr ${x.eps_r}) · ${measured ? "ค่ากลางจากไฮเพอร์โบลา" : "ไม่ได้วัด"}`, { sticky: true });
      }
      for (const p of x.points) {
        if (band && p.depth_m > band[1]) continue;
        const inBand = !band || p.depth_m >= band[0];
        L.circleMarker([p.lat, p.lon], { radius: inBand ? 9 : 5, color: "#fff", weight: inBand ? 2 : 1, fillColor: col(p.eps_r), fillOpacity: inBand ? 1 : 0.45 })
          .addTo(ly).on("click", () => (x.line_id === S.line ? this.goTo(p.trace) : switchLine(x.line_id)))
          .bindTooltip(`<b>εr ${p.eps_r}</b> · v ${p.velocity_m_per_ns} m/ns<br>${esc(x.line_id)} trace ${p.trace} · ลึก ${p.depth_m.toFixed(2)} m · R² ${p.r2}`, { sticky: true });
      }
    }
    drawGuides();
  },
  drawZones() {
    const ly = this.layers.zones; ly.clearLayers();
    const l = line(), d = this.data;
    if (!d?.has_gps || !l) return;
    for (const sec of l.sections) {  // sections whose results are not used: dark band over the slice
      const path = [];
      for (let t = sec.trace_start; t <= sec.trace_end; t += Math.max(1, Math.round((sec.trace_end - sec.trace_start) / 20))) path.push(this.latlon(t));
      path.push(this.latlon(sec.trace_end));
      L.polyline(path, { color: "#343a40", weight: 16, opacity: 0.85, lineCap: "butt" }).addTo(ly)
        .bindTooltip(`ไม่ใช้ผล ${esc(sec.section_id)}${sec.reason ? ": " + esc(sec.reason) : ""}`, { sticky: true });
    }
    if (!$("#mapZones").checked) return;
    for (const z of l.zones) {
      if (z.review === "rejected" && !$("#showRejected").checked) continue;
      if (z.suspected_type === "artifact" && !$("#showArtifacts").checked) continue;
      const col = getComputedStyle(document.documentElement).getPropertyValue((TYPE[z.suspected_type] || TYPE.unknown)[1]).trim();
      const path = [z.trace_start, Math.round((z.trace_start + z.trace_end) / 2), z.trace_end].map((t) => this.latlon(t));
      const sel = z.zone_id === S.sel;
      // thin line on top of the slice so the colours underneath stay visible
      L.polyline(path, { color: "#fff", weight: sel ? 7 : 5, opacity: 0.9, lineCap: "round" }).addTo(ly);
      L.polyline(path, { color: col, weight: sel ? 5 : 3, opacity: z.review === "rejected" ? 0.4 : 1, lineCap: "round", dashArray: z.review === "auto" ? "6 4" : null })
        .addTo(ly).on("click", () => { select(z.zone_id); centreOn(Math.round((z.trace_start + z.trace_end) / 2)); })
        .bindTooltip(`<b>${esc(z.zone_id)}</b> ${esc((TYPE[z.suspected_type] || TYPE.unknown)[0])} · เชื่อมั่น${CONF[z.confidence]}<br>ลึก ${fmt(z.depth_top_m)}–${fmt(z.depth_bottom_m)} m · ${REV[z.review]}`, { sticky: true });
      L.marker(this.latlon(Math.round((z.trace_start + z.trace_end) / 2)), { interactive: false,
        icon: L.divIcon({ className: "zlabel", html: esc(z.zone_id), iconSize: null }) }).addTo(ly);
    }
  },
  hover(trace, pin) {
    const ly = this.layers.hover;
    if (!this.data?.has_gps || !ly) return;
    ly.clearLayers();
    const p = this.latlon(trace);
    L.circleMarker(p, { radius: 7, color: "#fff", weight: 3, fillColor: "#e8590c", fillOpacity: 1 }).addTo(ly);
    if (pin && !this.map.getBounds().contains(p)) this.map.panTo(p);
  },
  goTo(trace) {  // click on the map -> show that trace on the radargrams
    S.cursor = trace; centreOn(trace); this.hover(trace);
    $("#panes").scrollIntoView({ block: "nearest", behavior: "smooth" });
  },
};
async function switchLine(id) {
  S.line = id; S.sel = null; S.editing = null; S.cursor = null; S.view = { x0: 0, x1: 1, y0: 0, y1: 1 };
  render(); M.load();
}

window.addEventListener("gpr:3d-pick", async (event) => {
  if (!S.job?.lines.some((ln) => ln.line_id === event.detail.line)) return;
  await switchLine(event.detail.line);
  S.cursor = event.detail.trace; centreOn(event.detail.trace); M.hover(event.detail.trace);
});

$("#demo3dBtn").addEventListener("click", async () => {
  busy(true, "กำลังสร้างแนวสแกนสังเคราะห์สำหรับ 3D…");
  try { S.job = await post("/api/demo3d", {}); openProgress(); }
  catch (e) { toast("ผิดพลาด: " + e.message); } finally { busy(false); }
});
// ------------------------------------------------------------------ summary before the report
const WARN = {
  unreviewed: (w) => `${w.line_id}: ยังไม่ได้ตรวจ ${w.n} โซน`, not_approved: (w) => `${w.line_id}: ยังไม่ได้กด "ผ่าน"`,
  redo: (w) => `${w.line_id}: ทำเครื่องหมายว่าต้องประมวลผลใหม่`, default_velocity: (w) => `${w.line_id}: ใช้ความเร็วคลื่นค่าตั้งต้น ความลึกไม่แน่นอน`,
  no_gps: (w) => `${w.line_id}: ไม่มี GPS โซนจะไม่มีพิกัด`, missing_info: (w) => `ข้อมูลงานยังไม่กรอก: ${{ project: "โครงการ", site: "สถานที่", survey_date: "วันที่สำรวจ" }[w.field]}`,
  unreadable_files: (w) => `มีไฟล์ที่อ่านไม่ได้ ${w.n} ไฟล์`, processing_processing: (w) => `ยังประมวลผลไม่เสร็จ (เหลือ ${w.n} ไฟล์)`,
  processing_interrupted: (w) => `งานค้าง เหลือ ${w.n} ไฟล์ (กด "ทำต่อ")`, processing_waiting: (w) => `ยังไม่เริ่มประมวลผล`,
};
function showReview() { $("#summaryView").hidden = true; $("#reviewView").hidden = false; render(); setTimeout(() => M.map?.invalidateSize(), 50); }
async function openSummary() {
  let s;
  try { s = await api(jobUrl("/summary")); } catch (e) { toast(e.message); return; }
  $("#reviewView").hidden = true; $("#progressView").hidden = true; $("#summaryView").hidden = false;
  const t = s.totals, info = s.info, ch = S.job.choices;
  const surf = { unknown: "ไม่ระบุ", road: "ถนน", building: "อาคาร", open: "พื้นที่โล่ง" };
  const types = (o) => Object.entries(o).map(([k, v]) => `${(TYPE[k] || TYPE.unknown)[0]} ${v}`).join(", ") || "–";
  $("#summaryBody").innerHTML = `
    <section class="card"><h3>${s.ready ? "✓ พร้อมออกรายงาน" : "⚠ ตรวจรายการด้านล่างก่อนออกรายงาน"}</h3>
      ${s.warnings.length ? `<ul class="warns">${s.warnings.map((w) => `<li>${w.line_id ? `<a href="#" data-goline="${esc(w.line_id)}">` : ""}${esc((WARN[w.code] || ((x) => x.code))(w))}${w.line_id ? "</a>" : ""}</li>`).join("")}</ul>`
        : `<p class="muted">ทุกเส้นผ่านการตรวจ ตรวจทุกโซนแล้ว และกรอกข้อมูลงานครบ</p>`}
      <p class="small muted">รายงานยังออกได้แม้มีคำเตือน แต่จะมีแถบ "ฉบับร่าง" ถ้ายังมีโซนที่ไม่ได้ตรวจ</p></section>
    <section class="card"><h3>ข้อมูลงาน</h3>
      <div class="kv"><span>โครงการ</span><b>${esc(info.project || "–")}</b><span>สถานที่</span><b>${esc(info.site || "–")}</b>
        <span>ผู้ว่าจ้าง</span><b>${esc(info.client || "–")}</b><span>วันที่สำรวจ</span><b>${esc(info.survey_date || "–")}</b>
        <span>ผู้สำรวจ</span><b>${esc(info.operator || "–")}</b><span>ผู้ตรวจทาน</span><b>${esc(s.reviewer || "–")}</b>
        <span>การใช้พื้นผิว</span><b>${surf[info.surface_use] || info.surface_use}</b><span>ภาษารายงาน</span><b>${{ bi: "ไทย + อังกฤษ", th: "ไทย", en: "English" }[s.lang]}</b></div>
      <div class="chips"><span class="chip">${t.lines} เส้น · ผ่าน ${t.approved}</span><span class="chip">รวม ${fmt(t.length_m, 1)} m</span>
        <span class="chip">${t.zones} โซน · ตรวจแล้ว ${t.zones - t.review.auto}</span><span class="chip">ปฏิเสธ ${t.review.rejected}</span>
        <span class="chip">ช่วงที่ไม่ใช้ ${t.sections}</span></div></section>
    <section class="card"><h3>จุดที่ต้องดำเนินการ <span class="muted small">(ระดับ E / 1 / 2 ตามกฎ ไม่รวมที่ปฏิเสธและช่วงที่ไม่ใช้)</span></h3>
      ${s.findings.length ? `<table class="scan"><thead><tr><th>ระดับ</th><th>เส้น / โซน</th><th>ประเภท</th><th>เชื่อมั่น</th><th>ระยะ (m)</th><th>ลึก (m)</th><th>พิกัด</th><th>ตรวจ</th></tr></thead><tbody>
        ${s.findings.map((f) => `<tr><td><span class="chip act-${f.action}">${ACT[f.action]}</span></td>
          <td><a href="#" data-goline="${esc(f.line_id)}" data-gozone2="${esc(f.zone_id)}">${esc(f.line_id)} · ${esc(f.zone_id)}</a></td>
          <td>${(TYPE[f.suspected_type] || TYPE.unknown)[0]}</td><td>${CONF[f.confidence]}</td>
          <td>${f.distance_start_m != null ? `${fmt(f.distance_start_m, 1)}–${fmt(f.distance_end_m, 1)}` : "–"}</td>
          <td>${fmt(f.depth_top_m)}–${fmt(f.depth_bottom_m)}</td>
          <td class="small">${f.coord ? `${f.coord[1].toFixed(6)}, ${f.coord[0].toFixed(6)}` : "–"}</td><td>${REV[f.review]}</td></tr>`).join("")}</tbody></table>`
        : `<p class="muted">ไม่มีโซนที่ต้องดำเนินการเร่งด่วน</p>`}</section>
    <section class="card"><h3>รายเส้น</h3><table class="scan"><thead><tr><th>เส้น</th><th>สถานะ</th><th>ช่วง / ความยาว</th><th>GPS</th>
      <th>ความเร็วคลื่น</th><th>ค่าที่ผู้ใช้ตั้ง</th><th>โซน</th><th>ตรวจแล้ว</th><th>ช่วงที่ไม่ใช้</th></tr></thead><tbody>
      ${s.lines.map((l) => `<tr><td><a href="#" data-goline="${esc(l.line_id)}"><b>${esc(l.line_id)}</b></a><br><span class="small muted">${esc(l.source_file)}</span></td>
        <td><span class="lst ${l.status}">${LSTATUS[l.status][0]}</span> ${LSTATUS[l.status][1]}${l.status_note ? `<br><span class="small muted">${esc(l.status_note)}</span>` : ""}</td>
        <td>${l.crop ? `${fmt(l.crop.start_m, 1)}–${fmt(l.crop.end_m, 1)} m` : "ทั้งเส้น"}<br><span class="small muted">${l.length_m != null ? fmt(l.length_m, 1) + " m" : ""}</span></td>
        <td>${l.has_gps ? "✓" : "–"}</td><td>${fmt(l.velocity_m_per_ns, 3)} <span class="small muted">${esc(l.velocity_source)}</span></td>
        <td class="small">${Object.keys(l.options).length ? esc(Object.entries(l.options).map(([k, v]) => `${k}=${v}`).join(", ")) : "อัตโนมัติ"}</td>
        <td class="small">${l.zones} · ${types(l.by_type)}</td><td>${l.zones - l.review.auto}/${l.zones}</td>
        <td class="small">${l.sections.map((x) => `${esc(x.section_id)} ${esc(x.reason)}`).join("<br>") || "–"}</td></tr>`).join("")}
      </tbody></table></section>`;
  $$("#summaryView [data-export]").forEach((a) => (a.href = jobUrl(`/export/${a.dataset.export}`)));
}


// ------------------------------------------------------------------ comparison with reference software
const jobUrl = (p) => `/api/jobs/${S.job.job_id}${p}`;
const lineUrl = (p) => jobUrl(`/lines/${S.line}${p}`);
function pct(v) { return v == null ? "–" : Math.round(v * 100) + "%"; }
function renderCompare(l) {
  const ch = S.job.choices, c = S.job.comparison.lines[l.line_id];
  const src = $("#refSource"), cur = src.value || "viewpoint";
  src.innerHTML = opts(Object.keys(ch.ref_source), cur, ch.ref_source);
  const s = c.summary;
  $("#cmpNote").textContent = c.picks.length ? `(${c.picks.length} จุดอ้างอิง)` : "(ยังไม่มีข้อมูลอ้างอิง)";
  $("#cmpSummary").innerHTML = c.picks.length ? `
    <span class="chip">อ้างอิง <b>${s.n_ref}</b> จุด</span><span class="chip">ตรงกัน <b>${s.match}</b></span>
    <span class="chip">ตรงบางส่วน <b>${s.partial}</b></span><span class="chip">เราไม่พบ <b>${s.missed}</b></span>
    <span class="chip">เราพบเกิน <b>${s.extra}</b> (จริง ${s.extra_real} · ผิด ${s.extra_false})</span>
    <span class="chip" title="สัดส่วนจุดอ้างอิงที่เราพบ">พบครบ <b>${pct(s.recall)}</b></span>
    <span class="chip" title="โซนที่ยังไม่ตัดสินนับเป็นผิดไว้ก่อน">แม่นยำ <b>${pct(s.precision)}</b></span>
    <span class="chip">ระยะต่างเฉลี่ย <b>${fmt(s.mean_abs_dx_m)}</b> m</span><span class="chip">ลึกต่างเฉลี่ย <b>${fmt(s.mean_abs_ddepth_m)}</b> m</span>` : "";
  $("#refImages").innerHTML = c.images.map((im) => `<div class="ref-img"><div class="cap"><span>${esc(ch.ref_source[im.source])} · ${esc(im.name)}</span>
      <span><a href="${lineUrl(`/ref/images/${encodeURIComponent(im.file)}`)}" target="_blank">เปิดเต็ม</a> ·
      <a href="#" data-delimg="${esc(im.file)}">ลบ</a></span></div>
      <img loading="lazy" src="${lineUrl(`/ref/images/${encodeURIComponent(im.file)}`)}" alt="${esc(im.name)}"></div>`).join("");
  const zoneOpts = (r) => `<select data-pair="${esc(r.ref_id)}" title="จับคู่เอง"><option value="">อัตโนมัติ</option><option value="none" ${r.forced && !r.zone_id ? "selected" : ""}>ไม่มีโซนตรงกัน</option>
      ${l.zones.map((z) => `<option value="${z.zone_id}" ${r.forced && r.zone_id === z.zone_id ? "selected" : ""}>${z.zone_id} ${esc((TYPE[z.suspected_type] || TYPE.unknown)[0])}</option>`).join("")}</select>`;
  $("#cmpRows").innerHTML = c.rows.map((r) => {
    const isRef = r.kind !== "ours_only", key = isRef ? `ref:${r.ref_id}` : `zone:${r.zone_id}`;
    const choices = isRef ? ch.ref_verdict : ch.zone_verdict;
    const where = isRef ? `${r.ref_x_start_m != null ? `${fmt(r.ref_x_start_m)}–${fmt(r.ref_x_end_m)} m` : `trace ${r.ref_trace_start}–${r.ref_trace_end}`}<br>ลึก ${fmt(r.ref_depth_top_m)}–${fmt(r.ref_depth_bottom_m)} m` : "";
    const diff = r.kind === "pair" ? `${r.dx_center_m != null ? fmt(r.dx_center_m) + " m" : r.dx_center_traces + " tr"} / ${fmt(r.ddepth_top_m)} m` : "";
    return `<tr class="v-${r.verdict}">
      <td>${isRef ? `<span class="refid">${esc(r.ref_id)}</span> ${esc(r.label)}<br><span class="small muted">${esc(ch.ref_source[r.source])}${r.ref_type ? " · " + esc(r.ref_type) : ""}</span>` : `<span class="muted">–</span>`}</td>
      <td class="small">${where}</td>
      <td>${r.zone_id ? `<a href="#" data-gozone="${esc(r.zone_id)}">${esc(r.zone_id)}</a> <span class="small muted">${esc((TYPE[r.our_type] || TYPE.unknown)[0])}</span>` : `<span class="muted">ไม่มี</span>`}
        ${isRef ? "<br>" + zoneOpts(r) : ""}</td>
      <td class="small">${diff}</td>
      <td class="small">${VERDICT[r.auto] || r.auto}</td>
      <td><select data-verdict="${esc(key)}"><option value="">ตามผลจับคู่</option>${opts(choices, r.verdict_by_user ? r.verdict : "", VERDICT)}</select></td>
      <td><input data-vnote="${esc(key)}" value="${esc(r.note)}" placeholder="หมายเหตุ"></td>
      <td>${isRef ? `<button class="btn ghost" data-delpick="${esc(r.ref_id)}" title="ลบจุดอ้างอิง">✗</button>` : ""}</td></tr>`;
  }).join("") || `<tr><td colspan="8" class="muted">ยังไม่มีจุดอ้างอิงสำหรับเส้นนี้</td></tr>`;
  const meta = c.meta[cur] || {}, mf = $("#metaForm");
  for (const el of mf.elements) if (el.name) el.value = meta[el.name] ?? "";
  $("#metaRows").innerHTML = c.meta_check.map((m) => `<tr><td>${esc(ch.ref_source[m.source])}</td><td>${META[m.field] || m.field}</td>
      <td>อ้างอิง ${m.ref}</td><td>เรา ${m.ours ?? "–"}</td><td class="${m.ok ? "ok-y" : m.ok === false ? "ok-n" : ""}">${m.ok ? "✓ ตรง" : m.ok === false ? "✗ ไม่ตรง" : "?"}</td></tr>`).join("");
  const tol = S.job.comparison.tolerance, tf = $("#tolForm");
  for (const el of tf.elements) if (el.name) el.value = tol[el.name];
  renderFeatures();
}
function renderFeatures() {
  const ch = S.job.choices;
  $("#featRows").innerHTML = S.job.comparison.features.map((f, i) => `<tr data-feat="${i}">
    <td><input name="feature" value="${esc(f.feature)}"></td>
    <td><select name="source">${opts(Object.keys(ch.ref_source), f.source, ch.ref_source)}</select></td>
    <td><select name="rating">${opts(ch.feature_rating, f.rating, RATING)}</select></td>
    <td><input name="note" value="${esc(f.note)}"></td>
    <td><button class="btn ghost" data-delfeat="${i}">✗</button></td></tr>`).join("");
}
function featureValues() {
  return $$("#featRows tr").map((tr) => Object.fromEntries($$("input,select", tr).map((el) => [el.name, el.value])));
}
async function upload(url, files, fields) {
  const fd = new FormData();
  for (const f of files) fd.append(fields.fileField || "files", f, f.name);
  for (const [k, v] of Object.entries(fields)) if (k !== "fileField") fd.append(k, v);
  return api(url, { method: "POST", body: fd });
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
  M.drawZones();
  $$(".zbox").forEach((b) => b.classList.toggle("sel", b.dataset.zone === id));
  $$(".zcard").forEach((c) => c.classList.toggle("sel", c.dataset.card === id));
  if (scroll) $(`[data-card="${id}"]`)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
}
async function update(promise, msg) {
  try { S.job = await promise; render(); M.drawZones(); if (S.sel) select(S.sel, false); if (msg) toast(msg); }
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
  if (forZone === SECTION_DRAW) { toast("ลากบนภาพครอบช่วงระยะที่จะไม่ใช้ผล (Esc เพื่อยกเลิก)"); return; }
  if (forZone === ZOOM_DRAW) { toast("ลากกรอบบนภาพบริเวณที่ต้องการซูม (Esc เพื่อยกเลิก)"); return; }
  toast(forZone === REF_DRAW ? "ลากกรอบบนภาพตรงตำแหน่งที่ซอฟต์แวร์อ้างอิงมาร์กไว้ (Esc เพื่อยกเลิก)" : forZone ? `ลากกรอบใหม่ของโซน ${forZone} บนภาพใดก็ได้` : "ลากกรอบบนภาพเพื่อเพิ่มโซน (Esc เพื่อยกเลิก)");
}
function stopDraw() { S.drawFor = null; document.body.classList.remove("drawing"); $$(".drawbox").forEach((d) => d.remove()); }

function pos(e, wrap) {
  const r = ($(".zoomer", wrap) || wrap).getBoundingClientRect(), l = line();  // the zoomer spans the whole line
  const fx = Math.min(Math.max((e.clientX - r.left) / r.width, 0), 1), fy = Math.min(Math.max((e.clientY - r.top) / r.height, 0), 1);
  return { fx, fy, trace: Math.min(Math.floor(fx * l.n_traces), l.n_traces - 1), t: fy * l.time_window_ns };
}

document.addEventListener("mousemove", (e) => {
  const w = e.target.closest?.(".imgwrap");
  if (w && line()) {
    const p = pos(e, w), l = line();
    $("#readout").textContent = `trace ${p.trace}${l.dx_m ? ` · ระยะ ${((p.trace + (l.trace_offset || 0)) * l.dx_m).toFixed(2)} m` : ""} · เวลา ${p.t.toFixed(1)} ns · ลึก ${depthAt(l, p.t).toFixed(2)} m (v ${l.velocity_m_per_ns.toFixed(3)})`
      + (M.data?.has_gps ? " · ดูจุดนี้บนแผนที่ได้" : "");
    M.hover(p.trace);
  }
  if (S.pan) {
    const r = S.pan.wrap.getBoundingClientRect(), v = S.pan.view;
    const dx = (e.clientX - S.pan.x) / r.width * (v.x1 - v.x0), dy = (e.clientY - S.pan.y) / r.height * (v.y1 - v.y0);
    if (Math.abs(e.clientX - S.pan.x) + Math.abs(e.clientY - S.pan.y) > 3) S.pan.moved = true;
    if (S.pan.moved) setView(v.x0 - dx, v.x1 - dx, v.y0 - dy, v.y1 - dy);
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
  if (!w || e.button !== 0) return;
  e.preventDefault();
  if (S.drawFor === null) {  // pan; a click without moving picks the trace and the map depth
    S.pan = { wrap: w, x: e.clientX, y: e.clientY, view: { ...S.view }, start: pos(e, w), moved: false };
    return;
  }
  const box = document.createElement("div"); box.className = "drawbox"; $(".zoomer", w).appendChild(box);
  S.drag = { wrap: w, start: pos(e, w), box };
});
document.addEventListener("mouseup", async () => {
  if (S.pan) {
    const { moved, start } = S.pan; S.pan = null;
    if (!moved) { S.cursor = start.trace; M.pickDepth(depthAt(line(), start.t)); M.hover(start.trace, true); drawGuides(); }
    return;
  }
  if (!S.drag) return;
  const { start: a, end: b } = S.drag; const target = S.drawFor;
  S.drag = null; stopDraw();
  if (target === SECTION_DRAW) {
    if (!b || Math.abs(b.trace - a.trace) < 2) { toast("ช่วงสั้นเกินไป"); return; }
    const reason = prompt("เหตุผลที่ไม่ใช้ผลช่วงนี้ (เช่น ข้อมูลเสีย รถจอดทับ นอกพื้นที่สำรวจ)", "");
    if (reason === null) return;
    await update(post(lineUrl("/sections"), { trace_start: Math.min(a.trace, b.trace), trace_end: Math.max(a.trace, b.trace), reason }),
      "บันทึกช่วงที่ไม่ใช้ผลแล้ว โซนในช่วงนี้จะไม่อยู่ในรายงาน");
    return;
  }
  if (target === ZOOM_DRAW) {
    if (b && Math.abs(b.fx - a.fx) > 0.002) setView(Math.min(a.fx, b.fx), Math.max(a.fx, b.fx), Math.min(a.fy, b.fy), Math.max(a.fy, b.fy));
    return;
  }
  if (!b || Math.abs(b.trace - a.trace) < 2) { toast("กรอบเล็กเกินไป"); return; }
  const vals = { trace_start: Math.min(a.trace, b.trace), trace_end: Math.max(a.trace, b.trace),
    time_top_ns: +Math.min(a.t, b.t).toFixed(2), time_bottom_ns: +Math.max(a.t, b.t).toFixed(2) };
  if (target === REF_DRAW) {
    await update(post(lineUrl("/ref/picks"), { source: $("#refSource").value, x_unit: "trace", y_unit: "ns",
      x_start: vals.trace_start, x_end: vals.trace_end, top: vals.time_top_ns, bottom: vals.time_bottom_ns }), "เพิ่มจุดอ้างอิงแล้ว");
  } else if (target === "") {
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
  const zb2 = t.closest("[data-zoom]");
  if (zb2) {
    const v = S.view, cx = (v.x0 + v.x1) / 2, cy = (v.y0 + v.y1) / 2, a = zb2.dataset.zoom;
    if (a === "in") zoomAt(cx, cy, 1 / 1.5); else if (a === "out") zoomAt(cx, cy, 1.5);
    else if (a === "box") startDraw(ZOOM_DRAW); else resetZoom();
    return;
  }
  const ls = t.closest("[data-lstatus]");
  if (ls) { update(post(lineUrl("/status"), { status: ls.dataset.lstatus, note: $("#statusNote").value }), `สถานะ ${S.line}: ${LSTATUS[ls.dataset.lstatus][1]}`); return; }
  const ds = t.closest("[data-delsection]");
  if (ds) { e.preventDefault(); update(api(lineUrl(`/sections/${ds.dataset.delsection}`), { method: "DELETE" }), "ใช้ผลช่วงนี้อีกครั้ง"); return; }
  const gl = t.closest("[data-goline]");
  if (gl) { e.preventDefault(); showReview(); switchLine(gl.dataset.goline); if (gl.dataset.gozone2) setTimeout(() => select(gl.dataset.gozone2), 300); return; }
  const br = t.closest("[data-browse]"); if (br) { browse(br.dataset.browse); return; }
  const uf = t.closest("[data-usefolder]"); if (uf) { $("#folderPath").value = uf.dataset.usefolder; scanFolder(); return; }
  if (t.closest("[data-closebrowser]")) { $("#browser").hidden = true; return; }
  const sl = t.closest("[data-sel]");
  if (sl && S.scan) {
    for (const l of S.scan.lines) l.include = l.status !== "error" && (sl.dataset.sel === "all" || (sl.dataset.sel === "ok" && l.status === "ok"));
    renderScan(); return;
  }
  if (t.closest("[data-resume]")) { resumeJob(); return; }
  if (t.closest("[data-showprog]")) { e.preventDefault(); $("#reviewView").hidden = true; $("#progressView").hidden = false; S.autoOpen = false; pollProgress(); return; }
  const ol = t.closest("[data-openline]");
  if (ol) { e.preventDefault(); api(jobUrl("")).then((j) => { S.job = j; openReview(); switchLine(ol.dataset.openline); }); return; }
  const rm = t.closest("[data-rm]"); if (rm) { S.files.splice(+rm.dataset.rm, 1); renderFiles(); return; }
  const li = t.closest("[data-line]"); if (li) { switchLine(li.dataset.line); return; }
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
  const op = t.closest("[data-open]"); if (op) { e.preventDefault(); openJob(op.dataset.open); return; }
  const gz = t.closest("[data-gozone]"); if (gz) { e.preventDefault(); select(gz.dataset.gozone); return; }
  const dp = t.closest("[data-delpick]");
  if (dp) { if (confirm(`ลบจุดอ้างอิง ${dp.dataset.delpick}?`)) update(api(lineUrl(`/ref/picks/${dp.dataset.delpick}`), { method: "DELETE" }), "ลบจุดอ้างอิงแล้ว"); return; }
  const di = t.closest("[data-delimg]");
  if (di) { e.preventDefault(); if (confirm("ลบภาพอ้างอิงนี้?")) update(api(lineUrl(`/ref/images/${encodeURIComponent(di.dataset.delimg)}`), { method: "DELETE" }), "ลบภาพแล้ว"); return; }
  const df = t.closest("[data-delfeat]"); if (df) { const fs = featureValues(); fs.splice(+df.dataset.delfeat, 1); S.job.comparison.features = fs; renderFeatures(); }
});
document.addEventListener("change", (e) => {
  const t = e.target;
  if (t.id === "statusNote") { update(post(lineUrl("/status"), { status: line().status, note: t.value }), "บันทึกหมายเหตุแล้ว"); return; }
  if (t.dataset.include) { S.scan.lines[+t.dataset.include].include = t.checked; renderScan(); return; }
  if (t.dataset.range) {
    const [i, k] = t.dataset.range.split(":").map(Number), l = S.scan.lines[i];
    l.range = l.range || ["", ""]; l.range[k] = t.value === "" ? "" : +t.value;
    return;
  }
  if (t.dataset.viewtoggle) {
    t.checked ? S.hidden.delete(t.dataset.viewtoggle) : S.hidden.add(t.dataset.viewtoggle);
    keep("hiddenViews", [...S.hidden]); renderLine(); if (S.sel) select(S.sel, false);
    return;
  }
  if (t.dataset.verdict) update(post(lineUrl("/ref/verdict"), { key: t.dataset.verdict, verdict: t.value || null }), "บันทึกคำตัดสินแล้ว");
  else if (t.dataset.vnote) update(post(lineUrl("/ref/verdict"), { key: t.dataset.vnote, note: t.value }), "บันทึกหมายเหตุแล้ว");
  else if (t.dataset.pair) update(api(lineUrl(`/ref/picks/${t.dataset.pair}`), { method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ match_zone: t.value || null }) }), "จับคู่ใหม่แล้ว");
});
document.addEventListener("submit", (e) => {
  const id = e.target.id;
  if (id === "pickForm" || id === "metaForm" || id === "tolForm") {
    e.preventDefault();
    const d = Object.fromEntries(new FormData(e.target));
    if (id === "pickForm") update(post(lineUrl("/ref/picks"), { ...d, source: $("#refSource").value }), "เพิ่มจุดอ้างอิงแล้ว").then(() => e.target.reset());
    else if (id === "metaForm") update(post(lineUrl(`/ref/meta/${$("#refSource").value}`), d), "บันทึกค่าจากซอฟต์แวร์อ้างอิงแล้ว");
    else update(post(jobUrl("/compare/settings"), { tolerance: d }), "ใช้เกณฑ์จับคู่ใหม่แล้ว");
    return;
  }
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

document.addEventListener("wheel", (e) => {
  const w = e.target.closest?.(".imgwrap");
  if (!w || !line()) return;
  e.preventDefault();
  const p = pos(e, w);
  zoomAt(p.fx, p.fy, e.deltaY < 0 ? 1 / 1.25 : 1.25, e.shiftKey);
}, { passive: false });
document.addEventListener("dblclick", (e) => { if (e.target.closest?.(".imgwrap") && S.drawFor === null) resetZoom(); });

// ------------------------------------------------------------------ wiring
const drop = $("#drop");
drop.addEventListener("click", () => $("#fileInput").click());
drop.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") $("#fileInput").click(); });
$("#fileInput").addEventListener("change", (e) => addFiles(e.target.files));
["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", (e) => addFiles(e.dataTransfer.files));
$("#runBtn").addEventListener("click", runUpload);
$("#pickBtn").addEventListener("click", pickFolder);
$("#scanBtn").addEventListener("click", scanFolder);
$("#browseBtn").addEventListener("click", () => browse($("#folderPath").value.trim()));
$("#folderPath").addEventListener("keydown", (e) => { if (e.key === "Enter") scanFolder(); });
$("#folderPath").value = store("lastFolder", "");
$("#toReview").addEventListener("click", async () => { S.job = await api(jobUrl("")); openReview(); });
$("#resumeBtn").addEventListener("click", resumeJob);
$("#demoBtn").addEventListener("click", runDemo);
$("#newJob").addEventListener("click", () => { if (confirm("เริ่มงานใหม่? งานนี้ยังเปิดได้จากโฟลเดอร์งานบนเครื่อง")) { location.hash = ""; location.reload(); } });
$("#drawBtn").addEventListener("click", () => startDraw(""));
$("#sectionBtn").addEventListener("click", () => startDraw(SECTION_DRAW));
$("#summaryBtn").addEventListener("click", openSummary);
$("#summaryBack").addEventListener("click", showReview);
$("#showArtifacts").addEventListener("change", () => { refreshOverlays(); M.drawZones(); });
$("#showRejected").addEventListener("change", () => { refreshOverlays(); M.drawZones(); });
for (const id of ["#sliceDepth", "#sliceThr", "#sliceCmap", "#mapMode"]) $(id).addEventListener("input", () => M.drawSlice());
$("#mapZones").addEventListener("change", () => M.drawZones());
$("#mapAll").addEventListener("change", () => M.draw(false));
$("#mapFit").addEventListener("click", (e) => { e.preventDefault(); M.fit(); });
$("#mapCard").addEventListener("toggle", () => { if ($("#mapCard").open) setTimeout(() => M.map?.invalidateSize(), 50); drawGuides(); });
$("#showRef").addEventListener("change", refreshOverlays);
$("#refSource").addEventListener("change", () => renderCompare(line()));
$("#refDrawBtn").addEventListener("click", () => startDraw(REF_DRAW));
$("#refImgBtn").addEventListener("click", () => $("#refImgInput").click());
$("#refCsvBtn").addEventListener("click", () => $("#refCsvInput").click());
$("#refImgInput").addEventListener("change", async (e) => {
  const files = [...e.target.files]; e.target.value = "";
  if (files.length) await update(upload(lineUrl("/ref/images"), files, { source: $("#refSource").value }), `แนบภาพ ${files.length} ภาพแล้ว`);
});
$("#refCsvInput").addEventListener("change", async (e) => {
  const f = e.target.files[0]; e.target.value = "";
  if (!f) return;
  try {
    const j = await upload(lineUrl("/ref/import"), [f], { fileField: "file", source: $("#refSource").value, replace: $("#refReplace").checked });
    S.job = j; render();
    const r = j.import_result;
    toast(`นำเข้า ${r.added} จุด` + (r.warnings.length ? ` (ข้าม ${r.warnings.length} แถว)` : ""));
  } catch (err) { toast("นำเข้าไม่ได้: " + err.message); }
});
$("#featAdd").addEventListener("click", () => { S.job.comparison.features = [...featureValues(), { feature: "", source: $("#refSource").value || "viewpoint", rating: "untested", note: "" }]; renderFeatures(); });
$("#featSave").addEventListener("click", () => update(post(jobUrl("/compare/settings"), { features: featureValues() }), "บันทึกคะแนนฟีเจอร์แล้ว"));
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
  if (done && !confirm(`ประมวลผลใหม่จะหาโซนใหม่ ผลตรวจทาน ${done} โซนจะย้ายไปยังโซนใหม่ที่ตำแหน่งตรงกัน โซนที่วาดเพิ่มคงไว้
โซนที่หาคู่ไม่ได้จะถูกล้าง (ยังเก็บไว้ใน labels) ทำต่อ?`)) return;
  busy(true, "กำลังประมวลผลเส้นนี้ใหม่…");
  S.sel = null; S.editing = null;
  await update(post(`/api/jobs/${S.job.job_id}/lines/${S.line}/reprocess`, { options }), "ประมวลผลใหม่แล้ว");
  busy(false);
  M.load();
}
$("#reprocessBtn").addEventListener("click", () => reprocess(optValues()));
$("#autoBtn").addEventListener("click", () => reprocess({}));

(async function boot() {
  const id = location.hash.slice(1);
  if (id) await openJob(id);
  else loadPastJobs();
})();

"use strict";
// Review workspace (layout B, 2026-11): an icon rail opens and closes panels, layouts open a set of panels at
// once, a dialog before processing says which panels are made now, when first opened, or not used, and a strip
// shows the queue of places to check along the line. Uses the globals of app.js (S, C, M, render, ...).

const W = (() => {
  const ICON = {
    confirm: "M5 12l4 4 10-10", lines: "M4 6h16M4 12h16M4 18h16", zones: "M4 4h7v7H4zM13 13h7v7h-7z",
    map: "M3 6l6-3 6 3 6-3v15l-6 3-6-3-6 3zM9 3v15M15 6v15", timeline: "M3 12h18M7 8v8M12 6v12M17 9v6",
    s3d: "M12 3l8 4.5v9L12 21l-8-4.5v-9zM12 12l8-4.5M12 12v9M12 12L4 7.5", thick: "M4 8l8-4 8 4-8 4zM4 12l8 4 8-4M4 16l8 4 8-4",
    compare: "M8 4v16M16 4v16M4 8h4M16 16h4", workflow: "M6 4v6a3 3 0 003 3h6a3 3 0 013 3v4M6 20v-4", recipe: "M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0M14 4v4M8 10v4M16 16v4",
    high_gain: "M3 12c2-6 4-6 6 0s4 6 6 0 4-6 6 0", envelope: "M3 18c3-10 6-12 9-12s6 2 9 12", layers: "M3 7h18M3 12h18M3 17h18",
    autodisplay: "M4 5h16l-6 7v6l-4 2v-8z", viewpoint: "M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12zM12 9a3 3 0 100 6 3 3 0 000-6z",
  };
  // kind: mode = confirm mode on/off · panel = part of the page · view = radargram picture of the line
  // processed: the view costs processing time for every line (only these are left out of processing)
  const PANELS = [
    { id: "confirm", kind: "mode", name: "ตรวจยืนยัน", full: "คิวตรวจยืนยัน + ปุ่มตัดสิน 1–4", note: "คิวทั้งงานเรียงจากจุดเสี่ยงสูงสุด" },
    { id: "lines", kind: "panel", name: "เส้น", full: "รายการเส้นสำรวจ", sel: ".review > .lines", note: "" },
    { id: "zones", kind: "panel", name: "โซน", full: "การ์ดโซนผิดปกติ", sel: ".review > .zones", note: "ยอมรับ / แก้ไข / ปฏิเสธทีละโซน" },
    { id: "map", kind: "panel", name: "แผนที่", full: "แผนที่ (มุมมองจากด้านบน)", sel: "#mapCard", note: "คลิกบนแผนที่เพื่อไปตำแหน่งนั้น" },
    { id: "timeline", kind: "panel", name: "คิวระยะ", full: "คิวตรวจตามแนวระยะ", sel: "#wsTimeline", note: "แถบจุดที่ต้องตรวจตลอดเส้น" },
    { id: "high_gain", kind: "view", name: "Gain สูง", full: "ภาพ Gain สูง (AGC)", note: "ทำทุกครั้งอยู่แล้ว" },
    { id: "envelope", kind: "view", name: "พลังงาน", full: "ภาพพลังงานสะท้อน", note: "ทำทุกครั้งอยู่แล้ว (ใช้กับแผนที่)" },
    { id: "layers", kind: "view", name: "ชั้นราบ", full: "ภาพชั้นแนวราบ (ไม่ลบพื้นหลัง)", processed: true, note: "ดูชั้นหินโรยทาง" },
    { id: "autodisplay", kind: "view", name: "อัตโนมัติ", full: "ภาพฟิลเตอร์อัตโนมัติ", processed: true, note: "ใช้ในโหมดตรวจยืนยันกับดินหลวม/สุ่มตรวจ" },
    { id: "viewpoint", kind: "view", name: "ViewPoint", full: "ภาพแบบหน้าจอ ViewPoint", processed: true, note: "เทียบกับโปรแกรมของ ImpulseRadar" },
    { id: "s3d", kind: "panel", name: "3D", full: "3D แนวสแกน / ปริมาตร", sel: "#survey3dCard", note: "สร้างเมื่อกดปุ่มในหน้าต่าง" },
    { id: "thick", kind: "panel", name: "Slice", full: "Thick slice ระบายสีตามความลึก", sel: "#thickCard", note: "สร้างเมื่อกดปุ่มในหน้าต่าง" },
    { id: "compare", kind: "panel", name: "เทียบ", full: "เทียบกับ ViewPoint / GeoLitix + คะแนนฟีเจอร์", sel: "#compare, #features", note: "" },
    { id: "workflow", kind: "panel", name: "A/B", full: "Workflow บันทึกและเทียบ A/B", sel: "#workflowCard", note: "" },
    { id: "recipe", kind: "panel", name: "ค่าตั้ง", full: "ขั้นตอนประมวลผลและพารามิเตอร์", sel: "#recipe", note: "ปรับค่าแล้วประมวลผลเส้นนี้ใหม่" },
  ];
  const BY = Object.fromEntries(PANELS.map((p) => [p.id, p]));
  // layouts: the panels open together; confirm = the confirm-mode queue and decision on the sides
  const LAYOUTS = {
    risk: { name: "ตรวจจุดเสี่ยง", note: "คิวซ้าย ภาพใหญ่ ตัดสินขวา แผนที่และคิวระยะใต้ภาพ", open: ["confirm", "map", "timeline"] },
    detail: { name: "วิเคราะห์ละเอียด", note: "เส้น โซน แผนที่ และค่าตั้ง", open: ["lines", "zones", "map", "timeline", "recipe", "high_gain", "envelope"] },
    compare: { name: "เทียบซอฟต์แวร์", note: "ภาพ ViewPoint + ตารางเทียบ + A/B", open: ["lines", "zones", "compare", "workflow", "viewpoint"] },
    d3: { name: "3D + แผนที่", note: "ภาพรวมพื้นที่หลายแนว", open: ["lines", "s3d", "thick", "map"] },
  };
  const STATES = [["now", "ทำเลยและเปิด"], ["open", "ทำเมื่อกดเปิด"], ["off", "ไม่ใช้"]];

  const w = {
    choice: null, jobId: null, layout: "detail", panels: {}, open: new Set(), queue: null, queueAt: 0, saveTimer: null,
    isOff(id) { return this.panels[id] === "off"; },
    defaults(layout) {  // panel states a layout starts from
      const L = LAYOUTS[layout] || LAYOUTS.detail, out = {};
      for (const p of PANELS) {
        if (p.kind === "view" && !p.processed) out[p.id] = "now";
        else if (p.id === "viewpoint") out[p.id] = L.open.includes("viewpoint") ? "now" : "open";
        else if (p.processed) out[p.id] = "now";  // layers / autodisplay: used by IDS ballast and confirm mode
        else out[p.id] = L.open.includes(p.id) ? "now" : "open";
      }
      return out;
    },

    // ------------------------------------------------------------ before processing
    ask(run) {
      const saved = store("wsChoice", null);
      let layout = saved?.layout in LAYOUTS ? saved.layout : "risk";
      let panels = { ...this.defaults(layout), ...(saved?.panels || {}) };
      const box = $("#wsStart");
      const draw = () => {
        const extra = PANELS.filter((p) => p.processed && panels[p.id] === "now").length;
        box.innerHTML = `<div class="ws-dialog" role="dialog" aria-modal="true" aria-labelledby="wsStartTitle">
          <div class="ws-dh"><h3 id="wsStartTitle">ก่อนเริ่มประมวลผล</h3><span class="small muted">เปลี่ยนทีหลังได้จากแถบไอคอนด้านซ้ายของหน้าตรวจผล</span></div>
          <div class="ws-dbody">
            <div class="ws-layouts"><div class="ws-sec">ผังหน้าจอ</div>
              ${Object.entries(LAYOUTS).map(([k, L]) => `<button type="button" class="ws-lay ${k === layout ? "on" : ""}" data-lay="${k}"><b>${L.name}</b><span class="small muted">${L.note}</span></button>`).join("")}
            </div>
            <div class="ws-rows"><table class="ws-tab"><thead><tr><th>หน้าต่าง</th>${STATES.map(([, t]) => `<th>${t}</th>`).join("")}</tr></thead><tbody>
              <tr><td><b>ภาพหน้าตัดขาวดำ</b><span class="small muted">จำเป็น ใช้ให้คะแนน</span></td><td><span class="ws-fixed">✓</span></td><td></td><td></td></tr>
              ${PANELS.map((p) => `<tr><td><b>${p.full}</b><span class="small muted">${p.processed ? "เพิ่มเวลาประมวลผลทุกเส้น · " : ""}${p.note}</span></td>
                ${STATES.map(([s]) => `<td><input type="radio" name="ws-${p.id}" value="${s}" aria-label="${p.full}: ${s}" ${panels[p.id] === s ? "checked" : ""}></td>`).join("")}</tr>`).join("")}
            </tbody></table></div>
          </div>
          <div class="ws-df"><span class="small muted">ภาพที่ต้องประมวลผลเพิ่ม ${extra} จาก 3 ภาพ · ภาพ "ทำเมื่อกดเปิด" จะทำเฉพาะเส้นที่เปิดดู ไม่ต้องประมวลผลใหม่ทั้งงาน</span>
            <span class="spacer"></span><label class="toggle small"><input type="checkbox" id="wsRemember" checked> จำค่านี้ไว้ครั้งหน้า</label>
            <button class="btn" type="button" id="wsCancel">ยกเลิก</button><button class="btn primary" type="button" id="wsGo">เริ่ม →</button></div></div>`;
      };
      draw();
      box.hidden = false;
      box.onclick = (e) => {
        const lay = e.target.closest("[data-lay]");
        if (lay) { layout = lay.dataset.lay; panels = this.defaults(layout); draw(); return; }
        if (e.target === box || e.target.id === "wsCancel") { box.hidden = true; return; }
        if (e.target.id === "wsGo") {
          this.choice = { layout, panels, open: LAYOUTS[layout].open.filter((id) => panels[id] !== "off") };
          if ($("#wsRemember").checked) keep("wsChoice", { layout, panels });
          box.hidden = true;
          run();
        }
      };
      box.onchange = (e) => {
        const r = e.target.closest("input[type=radio]");
        if (r) { panels[r.name.slice(3)] = r.value; draw(); }
      };
      box.onkeydown = (e) => { if (e.key === "Escape") box.hidden = true; };
      $("#wsGo").focus();
    },

    // ------------------------------------------------------------ the workspace of an open job
    apply(ws) {
      this.layout = ws?.layout in LAYOUTS ? ws.layout : "detail";
      this.panels = { ...this.defaults(this.layout), ...(ws?.panels || {}) };
      const open = ws?.open?.length ? ws.open : LAYOUTS[this.layout].open;
      this.open = new Set(open.filter((id) => BY[id] && !this.isOff(id) && BY[id].kind === "panel"));
      // pictures shown under each other: as saved / chosen before processing; a job without a workspace keeps
      // the pictures this browser showed last time
      for (const p of PANELS.filter((x) => x.kind === "view")) {
        if (this.isOff(p.id)) S.hidden.add(p.id);
        else if (ws?.panels) open.includes(p.id) || this.panels[p.id] === "now" && LAYOUTS[this.layout].open.includes(p.id) ? S.hidden.delete(p.id) : S.hidden.add(p.id);
      }
      $("#wsLayout").value = this.layout;
      this.paint();
      const wantConfirm = open.includes("confirm") && !this.isOff("confirm") && S.job.lines.length > 0;
      if (wantConfirm && !C.on) C.enter(); else if (!wantConfirm && C.on) C.exit(); else render();
    },
    setLayout(k) {
      this.layout = k;
      const L = LAYOUTS[k];
      this.open = new Set(L.open.filter((id) => BY[id]?.kind === "panel" && !this.isOff(id)));
      for (const p of PANELS.filter((x) => x.kind === "view")) L.open.includes(p.id) && !this.isOff(p.id) ? S.hidden.delete(p.id) : S.hidden.add(p.id);
      keep("hiddenViews", [...S.hidden]);
      this.paint();
      const wantConfirm = L.open.includes("confirm") && !this.isOff("confirm");
      if (wantConfirm && !C.on) C.enter(); else if (!wantConfirm && C.on) C.exit(); else { const v = { ...S.view }; render(); setView(v.x0, v.x1, v.y0, v.y1); }
      this.save();
    },
    isOpen(p) {
      if (p.kind === "mode") return C.on;
      if (p.kind === "view") return C.on ? C.viewFor(line() || { views: [] }) === p.id : !S.hidden.has(p.id);
      return this.open.has(p.id);
    },
    toggle(id) {
      const p = BY[id]; if (!p || this.isOff(id)) return;
      if (p.kind === "mode") { C.on ? C.exit() : C.enter(); this.save(); return; }
      if (p.kind === "view") {
        if (C.on) { C.view = C.view === id ? "auto" : id; }
        else { S.hidden.has(id) ? S.hidden.delete(id) : S.hidden.add(id); keep("hiddenViews", [...S.hidden]); }
        const v = { ...S.view }; renderLine(); setView(v.x0, v.x1, v.y0, v.y1); if (C.on) C.render();
        this.paintRail(); this.save();
        return;
      }
      const opening = !this.open.has(id);
      opening ? this.open.add(id) : this.open.delete(id);
      this.paint();
      if (opening) {
        const el = $(p.sel.split(",")[0]);
        if (el?.tagName === "DETAILS") el.open = true;
        el?.scrollIntoView({ block: "nearest", behavior: "smooth" });
        if (id === "timeline") this.timeline(true);
      }
      this.save();
    },
    paint() {
      for (const p of PANELS.filter((x) => x.kind === "panel")) {
        $$(p.sel).forEach((el) => {
          el.classList.add("ws-panel");
          el.classList.toggle("ws-closed", !this.open.has(p.id) || this.isOff(p.id));
          if (!el.querySelector(":scope > .ws-ctl, :scope > summary > .ws-ctl")) this.controls(el, p.id);
        });
      }
      this.paintRail();
      setTimeout(() => M.map?.invalidateSize(), 60);
    },
    paintRail() {
      $("#wsRail").innerHTML = PANELS.filter((p) => !this.isOff(p.id)).map((p, i, all) => {
        const sep = i && all[i - 1].kind !== p.kind && (p.kind === "view" || all[i - 1].kind === "view") ? `<span class="ws-sep"></span>` : "";
        return `${sep}<button type="button" class="ws-ri ${this.isOpen(p) ? "on" : ""}" data-ws="${p.id}" title="${esc(p.full)}${p.note ? " · " + esc(p.note) : ""}" aria-pressed="${this.isOpen(p)}">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="${ICON[p.id]}"/></svg>${p.name}</button>`;
      }).join("");
    },
    controls(el, id) {  // ⤢ maximise and ✕ close on every panel
      const c = document.createElement("span");
      c.className = "ws-ctl";
      c.innerHTML = `<button type="button" class="ws-cb" data-wsmax="${id}" title="ขยายเต็มหน้าจอ (Esc = คืนขนาด)">⤢</button><button type="button" class="ws-cb" data-wsclose="${id}" title="ปิดหน้าต่าง (เปิดใหม่จากแถบไอคอน)">✕</button>`;
      const head = el.tagName === "DETAILS" ? el.querySelector(":scope > summary") : el;
      head.prepend(c);
    },
    maximise(id) {
      const el = $(BY[id].sel.split(",")[0]);
      const on = !el.classList.contains("ws-max");
      $$(".ws-max").forEach((x) => x.classList.remove("ws-max"));
      if (on) { el.classList.add("ws-max"); if (el.tagName === "DETAILS") el.open = true; }
      document.body.classList.toggle("ws-maxed", on);
      setTimeout(() => { M.map?.invalidateSize(); window.dispatchEvent(new Event("resize")); }, 60);
    },
    save() {
      if (!S.job) return;
      clearTimeout(this.saveTimer);
      const open = [...this.open, ...(C.on ? ["confirm"] : []), ...PANELS.filter((p) => p.kind === "view" && !S.hidden.has(p.id)).map((p) => p.id)];
      const body = { layout: this.layout, panels: this.panels, open };
      this.saveTimer = setTimeout(() => api(`/api/jobs/${S.job.job_id}/workspace`, {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).catch(() => {}), 400);
    },

    // ------------------------------------------------------------ queue along the line
    async timeline(force = false) {
      const l = S.job && line();
      if (!l || !this.open.has("timeline") || $("#reviewView").hidden) return;
      if (C.on && C.items.length) this.queue = C.items;
      else if (force || !this.queue || Date.now() - this.queueAt > 3000) {
        this.queueAt = Date.now();
        try { this.queue = (await api(`/api/jobs/${S.job.job_id}/queue.json`)).items; } catch (e) { this.queue = []; }
      }
      const items = (this.queue || []).filter((it) => it.line_id === l.line_id), N = l.n_traces;
      const km = (t) => l.km_from != null && l.km_to != null ? l.km_from + (l.km_to - l.km_from) * t / Math.max(N - 1, 1) : null;
      const off = l.crop?.start_m || 0, dist = (t) => off + t * (l.dx_m || 0);
      const label = (t) => { const k = km(t); return k != null ? `${Math.floor(k)}+${String(Math.round((k % 1) * 1000)).padStart(3, "0")}` : l.dx_m ? `${dist(t).toFixed(0)} m` : `${Math.round(t)}`; };
      const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => `<span class="ws-tk" style="left:${f * 100}%">${label(f * (N - 1))}</span>`).join("");
      const todo = items.filter((it) => it.status === "todo").length;
      $("#wsTlNote").textContent = `${l.line_id} · ${items.length} จุด · รอตรวจ ${todo}${l.km_from != null ? " · กม." : l.dx_m ? " · ระยะจากต้นเส้น" : " · trace"}`;
      // wide items (layers along the whole line) go underneath, in a thin band, so short places stay clickable
      const span = (it) => it.trace_end - it.trace_start + 1;
      $("#wsTl").innerHTML = ticks + [...items].sort((a, b) => span(b) - span(a)).map((it) => {
        const c = it.status !== "todo" ? "--ok" : it.kind === "audit" ? "--accent" : ["E", "1"].includes(String(it.action)) ? "--bad" : "--warn";
        const x0 = it.trace_start / N * 100, wd = Math.max((it.trace_end - it.trace_start + 1) / N * 100, 0.6);
        return `<button type="button" class="ws-mk ${span(it) > N / 4 ? "wide" : ""} ${C.cur === C.key(it) || (!C.on && S.sel === it.id) ? "cur" : ""}" data-wsitem="${esc(C.key(it))}" style="left:${x0}%;width:${wd}%;--c:var(${c})"
          title="${esc(it.id)} · ${esc((TARGET[it.target] || TARGET.other)[0])} · ${esc(QSTATUS[it.status] || it.status)}"></button>`;
      }).join("") + `<span class="ws-win" id="wsWin"></span>`;
      this.window();
    },
    window() {
      const el = $("#wsWin"); if (!el) return;
      el.style.left = `${Math.max(S.view.x0, 0) * 100}%`; el.style.width = `${(Math.min(S.view.x1, 1) - Math.max(S.view.x0, 0)) * 100}%`;
    },
    async go(key, frac) {
      const it = (this.queue || []).find((x) => C.key(x) === key);
      if (it && C.on) return C.go(C.items.find((x) => C.key(x) === key) || it);
      const l = line(); if (!l) return;
      if (!it) { centreOn(Math.round(frac * (l.n_traces - 1))); return; }
      S.sel = it.kind === "zone" ? it.id : null;
      render();
      C.focus(it); refreshOverlays();
      this.timeline();
    },
  };

  // ------------------------------------------------------------ wiring
  $("#wsLayout").innerHTML = Object.entries(LAYOUTS).map(([k, L]) => `<option value="${k}">${L.name}</option>`).join("");
  $("#wsLayout").addEventListener("change", (e) => w.setLayout(e.target.value));
  $("#wsRail").addEventListener("click", (e) => { const b = e.target.closest("[data-ws]"); if (b) w.toggle(b.dataset.ws); });
  document.addEventListener("click", (e) => {
    const m = e.target.closest("[data-wsmax]"), x = e.target.closest("[data-wsclose]");
    if (!m && !x) return;
    e.preventDefault(); e.stopPropagation();  // inside <summary>: do not fold the panel
    if (m) w.maximise(m.dataset.wsmax);
    else { $$(".ws-max").forEach((el) => el.classList.remove("ws-max")); document.body.classList.remove("ws-maxed"); w.toggle(x.dataset.wsclose); }
  }, true);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && document.body.classList.contains("ws-maxed")) {
      $$(".ws-max").forEach((el) => el.classList.remove("ws-max")); document.body.classList.remove("ws-maxed");
      setTimeout(() => M.map?.invalidateSize(), 60);
    }
  });
  $("#wsTl").addEventListener("click", (e) => {
    const r = $("#wsTl").getBoundingClientRect(), mk = e.target.closest("[data-wsitem]");
    w.go(mk?.dataset.wsitem, Math.min(Math.max((e.clientX - r.left) / r.width, 0), 1));
  });
  window.addEventListener("gpr:job", (e) => {
    const job = e.detail.job;
    if (!job) return;
    if (job.job_id !== w.jobId) { w.jobId = job.job_id; w.queue = null; w.apply(job.workspace || {}); return; }
    w.paintRail();
    w.timeline();
  });
  const zoomed = applyZoom;  // keep the strip's window on the part of the line shown
  applyZoom = function (...a) { zoomed.apply(this, a); w.window(); };  // eslint-disable-line no-global-assign
  return w;
})();
window.W = W;

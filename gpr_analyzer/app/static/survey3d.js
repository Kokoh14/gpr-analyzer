import * as THREE from "three";
import { OrbitControls } from "/static/vendor/three/OrbitControls.js";
import * as V3 from "/static/volume3d.js";

// Independent viewer: the only coupling to the 2D page is the gpr:job / gpr:3d-pick events.
const $ = (id) => document.getElementById(id);
const card = $("survey3dCard"), stage = $("s3Stage");
const state = { job: null, selected: null, signature: "", data: null, hidden: new Set(), token: 0, drafts: new Map(),
                zones: [], zoneSignature: "", zoneToken: 0 };
// Mode 2 (volume): decoded grid, its slice/voxel objects, and the zone objects shared by both modes.
let vol = null, volObjects = {}, zoneObjects = [];
let renderer, scene, camera, controls, root, scheduled = false, lineObjects = [], labels = [], span = 20;
let east0 = 0, north0 = 0, maxDepth = 5, down = null;
let hoverEvent = null, hoverScheduled = false, sliceScheduled = false;
const raycaster = new THREE.Raycaster();
const number = (n, d = 2) => Number(n).toFixed(d);
const vec = (e, n, depth = 0) => new THREE.Vector3(e - east0, -depth, -(n - north0));
const lerpArray = (a, i) => a[Math.floor(i)] + (a[Math.min(a.length - 1, Math.ceil(i))] - a[Math.floor(i)]) * (i % 1);

async function request(url, options) {
  const r = await fetch(url, options);
  if (!r.ok) { let msg = r.statusText; try { msg = (await r.json()).detail || msg; } catch (_) {} throw new Error(msg); }
  return r.json();
}

function init() {
  if (renderer) return true;
  try {
    renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
    renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    renderer.setClearColor(0x091523);
    stage.prepend(renderer.domElement);
    scene = new THREE.Scene();
    camera = new THREE.PerspectiveCamera(43, 1, 0.01, 100000);
    controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = false;
    controls.maxPolarAngle = Math.PI * 0.95;
    controls.addEventListener("change", draw);
    new ResizeObserver(resize).observe(stage);
    renderer.domElement.addEventListener("pointerdown", (e) => { down = [e.clientX, e.clientY]; });
    renderer.domElement.addEventListener("pointermove", (e) => {
      if (e.buttons || !card.open) { hoverEvent = null; return; }
      hoverEvent = e;
      if (hoverScheduled) return;
      hoverScheduled = true;
      requestAnimationFrame(() => {
        hoverScheduled = false;
        if (hoverEvent && card.open) pick(hoverEvent, false);
        hoverEvent = null;
      });
    });
    renderer.domElement.addEventListener("pointerleave", () => { hoverEvent = null; });
    renderer.domElement.addEventListener("click", (e) => {
      if (down && Math.hypot(e.clientX - down[0], e.clientY - down[1]) < 5) pick(e, true);
    });
    renderer.domElement.addEventListener("webglcontextlost", (e) => {
      e.preventDefault(); $("s3Status").textContent = "WebGL หยุดทำงาน — ปิดแล้วเปิดส่วน 3D หรือโหลดหน้านี้ใหม่";
    });
    renderer.domElement.addEventListener("webglcontextrestored", () => load());
    resize();
    return true;
  } catch (e) {
    renderer = null;
    $("s3Status").textContent = "เปิด WebGL ไม่ได้: " + e.message + " · ยังตรวจภาพ 2D และส่งออกข้อมูลได้";
    return false;
  }
}

function resize() {
  if (!renderer || !stage.clientWidth || !stage.clientHeight) return;
  renderer.setSize(stage.clientWidth, stage.clientHeight, false);
  camera.aspect = stage.clientWidth / stage.clientHeight;
  camera.updateProjectionMatrix(); draw();
}

function draw() {
  if (!renderer || scheduled || !card.open) return;
  scheduled = true;
  requestAnimationFrame(() => {
    scheduled = false;
    if (!root) return;
    root.updateMatrixWorld(true);
    renderer.render(scene, camera);
    for (const label of labels) {
      const p = root.localToWorld(label.point.clone()).project(camera);
      const visible = p.z > -1 && p.z < 1 && Math.abs(p.x) < 1.1 && Math.abs(p.y) < 1.1 && (!label.line || !state.hidden.has(label.line));
      label.el.hidden = !visible;
      label.el.style.left = `${(p.x + 1) * 50}%`;
      label.el.style.top = `${(1 - p.y) * 50}%`;
    }
  });
}

function clear() {
  lineObjects.forEach((o) => o.pickMesh?.geometry.dispose());
  if (root) {
    const materials = new Set(), textures = new Set();
    root.traverse((o) => { o.geometry?.dispose(); if (o.material) materials.add(o.material); });
    materials.forEach((m) => { if (m.map) textures.add(m.map); m.dispose(); });
    textures.forEach((t) => t.dispose()); scene.remove(root);
  }
  root = new THREE.Group(); scene.add(root); labels = []; lineObjects = []; volObjects = {}; zoneObjects = [];
  $("s3Labels").replaceChildren();
}

function addLabel(text, point, line = null) {
  const el = document.createElement("span"); el.className = "s3-label" + (line ? " line" : "");
  el.textContent = text; $("s3Labels").append(el); labels.push({ el, point, line });
}

function path(points, color, opacity = 1) {
  const geometry = new THREE.BufferGeometry().setFromPoints(points);
  const o = new THREE.Line(geometry, new THREE.LineBasicMaterial({ color, transparent: opacity < 1, opacity }));
  root.add(o); return o;
}

function axes(x0, x1, n0, n1) {
  const bottom = maxDepth;
  for (let i = 0; i <= 5; i++) {
    const x = x0 + (x1 - x0) * i / 5, n = n0 + (n1 - n0) * i / 5, d = bottom * i / 5;
    path([vec(x, n0), vec(x, n1)], 0x29475c, 0.7);
    path([vec(x0, n), vec(x1, n)], 0x29475c, 0.7);
    addLabel(`E ${number(x, 1)}`, vec(x, n0 - span * 0.055, bottom + span * 0.025));
    if (i > 0) addLabel(`N ${number(n, 1)}`, vec(x1 + span * 0.055, n, bottom + span * 0.025));
    addLabel(`${number(d, 1)} m`, vec(x0 - span * 0.045, n0, d));
  }
  for (const [x, n] of [[x0, n0], [x0, n1], [x1, n0], [x1, n1]]) path([vec(x, n), vec(x, n, bottom)], 0x36566a, 0.65);
  path([vec(x0, n0, bottom), vec(x1, n0, bottom), vec(x1, n1, bottom), vec(x0, n1, bottom), vec(x0, n0, bottom)], 0x36566a, 0.65);
  addLabel("EAST / NORTH · m", vec(x1, n0 - span * 0.09, bottom + span * 0.04));
  addLabel("DEPTH ↓", vec(x0 - span * 0.12, n0, bottom * 0.55));
}

function geometryRows(depths) {
  // Texture UV stays on the original grid. Bound the error of its nonlinear depth mapping.
  const keep = new Set([0, depths.length - 1]);
  const tolerance = Math.max(0.001, Math.min(0.005, (depths.at(-1) - depths[0]) * 0.001));
  const split = (a, b) => {
    let worst = tolerance, at = -1;
    for (let i = a + 1; i < b; i++) {
      const error = Math.abs(depths[i] - (depths[a] + (depths[b] - depths[a]) * (i - a) / (b - a)));
      if (error > worst) { worst = error; at = i; }
    }
    if (at >= 0) { keep.add(at); split(a, at); split(at, b); }
  };
  split(0, depths.length - 1);
  return [...keep].sort((a, b) => a - b);
}

function curtain(line, texture) {
  const rows = geometryRows(line.depth_m);
  const positions = [], uvs = [], indices = [], nx = line.xy_m.length, ny = rows.length;
  for (let j = 0; j < ny; j++) for (let i = 0; i < nx; i++) {
    const p = vec(...line.xy_m[i], line.depth_m[rows[j]]);
    positions.push(p.x, p.y, p.z); uvs.push(i / (nx - 1), 1 - rows[j] / (line.depth_m.length - 1));
  }
  for (let j = 0; j < ny - 1; j++) for (let i = 0; i < nx - 1; i++) {
    const a = j * nx + i, b = a + nx; indices.push(a, b, a + 1, b, b + 1, a + 1);
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  g.setAttribute("uv", new THREE.Float32BufferAttribute(uvs, 2)); g.setIndex(indices); g.computeBoundingSphere();
  const m = new THREE.MeshBasicMaterial({ map: texture, side: THREE.DoubleSide, transparent: true, opacity: +$("s3Opacity").value, depthWrite: true });
  const mesh = new THREE.Mesh(g, m); mesh.userData.line = line; mesh.userData.kind = "curtain";
  root.add(mesh); return mesh;
}

function pickingCurtain(line, mesh) {
  // Each GPS segment is a vertical plane. Picking needs its boundary only; depth-to-UV
  // is recovered from the original nonlinear sample grid after intersecting that plane.
  const pos = [], uv = [], indices = [], nx = line.xy_m.length;
  for (const [j, depth] of [line.depth_m[0], line.depth_m.at(-1)].entries()) {
    for (let i = 0; i < nx; i++) {
      const p = vec(...line.xy_m[i], depth); pos.push(p.x, p.y, p.z); uv.push(i / (nx - 1), 1 - j);
    }
  }
  for (let i = 0; i < nx - 1; i++) indices.push(i, i + nx, i + 1, i + nx, i + nx + 1, i + 1);
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  g.setAttribute("uv", new THREE.Float32BufferAttribute(uv, 2)); g.setIndex(indices); g.computeBoundingSphere();
  const proxy = new THREE.Mesh(g, mesh.material); proxy.userData.line = line; proxy.userData.kind = "curtain";
  return proxy; // never rendered; shares the visible curtain's material and world transform
}

function sampleAtDepth(line, depth) {
  const ds = line.depth_m;
  if (depth < ds[0] || depth > ds.at(-1)) return null;
  let i = 0; while (i < ds.length - 2 && ds[i + 1] < depth) i++;
  return (i + (depth - ds[i]) / Math.max(1e-12, ds[i + 1] - ds[i])) / (ds.length - 1);
}

function slice(line, texture) {
  const g = new THREE.BufferGeometry(), m = new THREE.MeshBasicMaterial({ map: texture, side: THREE.DoubleSide, transparent: false });
  const mesh = new THREE.Mesh(g, m); mesh.userData.line = line; mesh.userData.kind = "slice"; root.add(mesh);
  const pos = [], uv = [], indices = [], xy = line.xy_m;
  const width = Math.min(0.35, Math.max(0.08, span / 110));
  for (let i = 0; i < xy.length - 1; i++) {
    const [x, n] = xy[i], [x2, n2] = xy[i + 1], len = Math.hypot(x2 - x, n2 - n);
    if (len < 1e-8) continue;
    const dx = -(n2 - n) / len * width / 2, dn = (x2 - x) / len * width / 2, k = pos.length / 3;
    for (const [e, north] of [[x + dx, n + dn], [x - dx, n - dn], [x2 + dx, n2 + dn], [x2 - dx, n2 - dn]]) {
      const p = vec(e, north); pos.push(p.x, p.y, p.z);
    }
    for (const col of [i, i, i + 1, i + 1]) uv.push(col / (xy.length - 1), 0);
    indices.push(k, k + 1, k + 2, k + 1, k + 3, k + 2);
  }
  g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  g.setAttribute("uv", new THREE.Float32BufferAttribute(uv, 2)); g.setIndex(indices);
  return mesh;
}

function updateSlice() {
  const depth = +$("s3Depth").value;
  $("s3DepthValue").textContent = `${number(depth)} m`;
  if (vol) {
    const k = Math.max(0, Math.min(vol.nz - 1, Math.round((depth - vol.depth_m[0]) / vol.dz_m)));
    $("s3DepthValue").textContent = `${number(vol.depth_m[k])} m`;
    replace("z", V3.depthSlice(vol, vec, k, $("s3Interp").checked));
  }
  for (const o of lineObjects) {
    if (!o.strip) continue;
    const row = sampleAtDepth(o.data, depth);
    o.strip.userData.depth = depth;
    o.strip.userData.hasData = row != null;
    if (row != null) {
      const g = o.strip.geometry, pos = g.attributes.position, uv = g.attributes.uv;
      for (let i = 0; i < pos.count; i++) { pos.setY(i, -depth); uv.setY(i, 1 - row); }
      pos.needsUpdate = true; uv.needsUpdate = true; g.computeBoundingSphere();
    }
  }
  visibility();
}

function visibility() {
  for (const o of lineObjects) {
    const visible = !state.hidden.has(o.data.line_id);
    if (o.tracks) {
      for (const t of o.tracks) { t.visible = visible && $("s3Paths").checked; t.material.color.set(o.data.line_id === state.selected ? 0xffce70 : 0x39d8c1); }
      continue;
    }
    o.mesh.visible = visible && $("s3Curtains").checked;
    o.strip.visible = visible && $("s3Slice").checked && !!o.strip.userData.hasData;
    o.track.visible = o.arrow.visible = visible && $("s3Paths").checked;
    o.track.material.color.set(o.data.line_id === state.selected ? 0xffce70 : 0x39d8c1);
  }
  for (const o of zoneObjects) {
    const z = o.userData.zone;
    o.visible = $("s3Zones").checked && !state.hidden.has(z.line_id) && (z.review !== "rejected" || $("s3Rejected").checked);
  }
  if (volObjects.z) volObjects.z.visible = $("s3VSlice").checked;
  if (volObjects.x) volObjects.x.visible = $("s3VEast").checked;
  if (volObjects.y) volObjects.y.visible = $("s3VNorth").checked;
  document.querySelectorAll(".s3-line").forEach((el) => el.classList.toggle("active", el.dataset.lineId === state.selected));
  draw();
}

function fit(view = "iso") {
  if (!root) return;
  root.updateMatrixWorld(true);
  const bounds = new THREE.Box3().setFromObject(root), target = bounds.getCenter(new THREE.Vector3());
  const radius = Math.max(bounds.getBoundingSphere(new THREE.Sphere()).radius, 1);
  const halfFov = Math.min(THREE.MathUtils.degToRad(camera.fov / 2), Math.atan(Math.tan(THREE.MathUtils.degToRad(camera.fov / 2)) * camera.aspect));
  const d = radius / Math.sin(halfFov) * 1.15;
  controls.target.copy(target);
  const offsets = { iso: [0.85, 0.65, 0.95], top: [0, 1.5, 0.001], front: [0, 0.03, 1.5] };
  camera.position.copy(target).add(new THREE.Vector3(...offsets[view]).normalize().multiplyScalar(d));
  camera.near = Math.max(0.005, d / 10000); camera.far = Math.max(1000, d * 100); camera.updateProjectionMatrix();
  controls.minDistance = d * 0.03; controls.maxDistance = d * 25; controls.update(); draw();
}

function build(data) {
  clear(); vol = null;
  if (!data.lines.length) { visibility(); return; }
  const xy = data.lines.flatMap((l) => l.xy_m), xs = xy.map((p) => p[0]), ns = xy.map((p) => p[1]);
  const xmin = Math.min(...xs), xmax = Math.max(...xs), nmin = Math.min(...ns), nmax = Math.max(...ns);
  east0 = (xmin + xmax) / 2; north0 = (nmin + nmax) / 2;
  span = Math.max(xmax - xmin, nmax - nmin, 1); maxDepth = Math.max(...data.lines.map((l) => l.depth_m.at(-1)));
  axes(xmin - span * 0.05, xmax + span * 0.05, nmin - span * 0.05, nmax + span * 0.05);
  const loader = new THREE.TextureLoader();
  for (const line of data.lines) {
    const texture = loader.load(line.texture, draw);
    texture.colorSpace = THREE.SRGBColorSpace; texture.minFilter = THREE.LinearFilter; texture.magFilter = THREE.LinearFilter;
    const mesh = curtain(line, texture), strip = slice(line, texture);
    const track = path(line.xy_m.map((p) => vec(...p)), 0x39d8c1);
    const points = line.xy_m, first = vec(...points[0]), last = vec(...points.at(-1));
    const next = points.find((p) => Math.hypot(p[0] - points[0][0], p[1] - points[0][1]) > 0.01) || points.at(-1);
    const direction = vec(...next).sub(first).normalize();
    const arrow = new THREE.ArrowHelper(direction, first, span * 0.055, 0x54eb9d, span * 0.018, span * 0.012); root.add(arrow);
    addLabel(`${line.line_id} · S`, first.clone().add(new THREE.Vector3(0, span * 0.025, 0)), line.line_id);
    addLabel("E", last.clone().add(new THREE.Vector3(0, span * 0.018, 0)), line.line_id);
    lineObjects.push({ data: line, mesh, strip, track, arrow, pickMesh: pickingCurtain(line, mesh) });
  }
  buildZones();
  root.scale.y = +$("s3Exag").value;
  $("s3Depth").max = maxDepth; $("s3Depth").step = 0.01; $("s3Depth").value = Math.min(+$("s3Depth").value, maxDepth);
  updateSlice(); fit();
}

function buildZones() {
  for (const o of zoneObjects) { root.remove(o); o.traverse((c) => { c.geometry?.dispose(); c.material?.dispose(); }); }
  zoneObjects = [];
  for (const zone of state.zones) {
    const group = V3.zoneWall(zone, vec); group.userData = { zone }; root.add(group); zoneObjects.push(group);
  }
}

function buildVolume(data) {
  clear(); vol = V3.decode(data);
  const x1 = vol.x0_m + vol.nx * vol.cell_m, y1 = vol.y0_m + vol.ny * vol.cell_m;
  east0 = (vol.x0_m + x1) / 2; north0 = (vol.y0_m + y1) / 2;
  span = Math.max(x1 - vol.x0_m, y1 - vol.y0_m, 1); maxDepth = vol.depth_m.at(-1);
  axes(vol.x0_m - span * 0.05, x1 + span * 0.05, vol.y0_m - span * 0.05, y1 + span * 0.05);
  for (const line of data.lines) {
    const tracks = line.tracks.map((t) => path(t.map((p) => vec(...p)), 0x39d8c1));
    if (line.tracks.length) addLabel(`${line.line_id} · S`, vec(...line.tracks[0][0]).add(new THREE.Vector3(0, span * 0.025, 0)), line.line_id);
    lineObjects.push({ data: line, tracks });
  }
  buildZones();
  root.scale.y = +$("s3Exag").value;
  $("s3Depth").max = maxDepth; $("s3Depth").step = vol.dz_m; $("s3Depth").value = Math.min(+$("s3Depth").value, maxDepth);
  $("s3VEastPos").max = vol.nx - 1; $("s3VEastPos").value = Math.min(+$("s3VEastPos").value || Math.floor(vol.nx / 2), vol.nx - 1);
  $("s3VNorthPos").max = vol.ny - 1; $("s3VNorthPos").value = Math.min(+$("s3VNorthPos").value || Math.floor(vol.ny / 2), vol.ny - 1);
  sections(); voxelView(); updateSlice(); fit();
}

function replace(key, object) {
  const old = volObjects[key];
  if (old) { root.remove(old); old.traverse((c) => { c.geometry?.dispose(); if (c.material) { c.material.map?.dispose(); c.material.dispose(); } }); }
  volObjects[key] = object; if (object) root.add(object);
}

function sections() {
  if (!vol) return;
  const interp = $("s3Interp").checked, i = +$("s3VEastPos").value, j = +$("s3VNorthPos").value;
  replace("x", V3.eastSection(vol, vec, i, interp)); replace("y", V3.northSection(vol, vec, j, interp));
  $("s3VEastValue").textContent = `E ${number(vol.x0_m + (i + 0.5) * vol.cell_m)} m`;
  $("s3VNorthValue").textContent = `N ${number(vol.y0_m + (j + 0.5) * vol.cell_m)} m`;
  visibility();
}

function voxelView() {
  if (!vol) return;
  $("s3VoxDbValue").textContent = `${$("s3VoxDb").value.replace("-", "−")} dB`;
  if (!$("s3Voxels").checked) { replace("voxels", null); visibility(); return; }
  const v = V3.voxels(vol, vec, +$("s3VoxDb").value, $("s3Interp").checked);
  replace("voxels", v.group);
  if (v.truncated) $("s3VoxDbValue").textContent += ` (แสดง ≥ ${number(v.level_db, 0)} dB: จำนวนเกินขีด)`;
  $("s3Status").textContent = `ก้อน ≥ ${number(v.level_db, 0)} dB: วัดจริง ${v.counts.measured} · ประมาณค่า ${v.counts.interpolated} ช่อง`;
  visibility();
}

function sidebar(data) {
  $("s3Lines").replaceChildren();
  for (const line of data.lines) {
    const label = document.createElement("label"); label.className = "s3-line"; label.dataset.lineId = line.line_id;
    label.title = `${line.navigation || "positioned traces"}; velocity ${line.velocity_m_per_ns} m/ns (${line.velocity_source}); antenna separation ${line.antenna_separation_m} m`;
    const check = document.createElement("input"); check.type = "checkbox"; check.checked = !state.hidden.has(line.line_id);
    check.addEventListener("change", () => { check.checked ? state.hidden.delete(line.line_id) : state.hidden.add(line.line_id); visibility(); });
    const text = document.createElement("div"), title = document.createElement("b"), desc = document.createElement("span");
    title.textContent = line.line_id;
    desc.textContent = line.coverage_traces ? `${line.source_file} · trace ${line.coverage_traces.join("–")}`
      : `${line.source_file} · วางตำแหน่ง ${line.positioned_traces}/${line.n_traces} trace · ลึกสุด ${number(line.max_depth_m)} m`;
    text.append(title, desc); label.append(check, text); $("s3Lines").append(label);
  }
  $("s3Count").textContent = `${data.lines.length}/${state.job.lines.length}`;
  $("s3Excluded").replaceChildren();
  for (const item of data.excluded) {
    const p = document.createElement("p"); p.textContent = `${item.line_id} · ${item.reason}`; $("s3Excluded").append(p);
  }
  const origin = data.origin_wgs84;
  $("s3Datum").textContent = (origin ? `WGS84 origin ${number(origin.lat, 8)}, ${number(origin.lon, 8)} → local E/N (m)` : "User-defined local E/N (m)")
    + " · ผิวอ้างอิงระดับเดียว z=0 · ความลึกจาก velocity ของแต่ละแนว · vertical " + $("s3Exag").value + "×";
  if (data.grid) {
    const c = data.counts, all = c.measured + c.interpolated + c.empty, pct = (n) => `${number(100 * n / all, 1)}%`;
    $("s3Datum").textContent += ` · grid ${data.grid.cell_m} m × ${data.grid.dz_m} m: วัดจริง ${pct(c.measured)} · ประมาณค่า ${pct(c.interpolated)}`
      + ` · ว่าง ${pct(c.empty)} · เติมช่องว่างได้ไม่เกิน ${data.parameters.max_gap_m} m`
      + (data.parameters.max_gap_source === "line spacing" ? " (จากระยะห่างระหว่างแนว)" : "")
      + (data.parameters.line_spacing_m != null ? ` · ระยะห่างระหว่างแนวที่วัดได้ ~${number(data.parameters.line_spacing_m, 2)} m` : "");
    if (data.parameters.max_gap_source === "line spacing") $("s3Gap").value = data.parameters.max_gap_m;
  }
  const energy = data.track === "envelope" || !!data.grid;
  $("s3Legend").classList.toggle("energy", energy);
  $("s3Legend").replaceChildren();
  const note = document.createElement("span"), bar = document.createElement("i"), scale = document.createElement("span");
  note.textContent = data.grid ? "Envelope / line reference (99.5%)" : energy ? "Envelope / line maximum" : "Amplitude / line contrast";
  scale.textContent = energy ? "−40 dB … 0 dB" : "− amplitude … + amplitude";
  $("s3Legend").append(note, bar, scale);
  if (state.zones.length) {
    const keys = document.createElement("span"); keys.style.display = "block"; keys.style.marginTop = "6px";
    for (const [type, label] of [["void_like", "โพรง"], ["pipe_point", "ท่อ/จุด"], ["layer", "ชั้น"], ["unknown", "อื่น ๆ"]]) {
      const k = document.createElement("i"); k.className = "s3-key"; k.style.background = `#${V3.ZONE_COLOURS[type].toString(16).padStart(6, "0")}`;
      keys.append(k, label);
    }
    $("s3Legend").append(keys);
  }
  placementRows(data.layout);
}

function placementRows(layout) {
  $("s3LayoutRows").replaceChildren();
  for (const line of state.job.lines) {
    const p = layout.lines[line.line_id], draft = state.drafts.get(line.line_id), row = document.createElement("tr"); row.dataset.lineId = line.line_id;
    const on = document.createElement("input"); on.type = "checkbox"; on.checked = draft ? draft.enabled : !!p;
    const td = document.createElement("td"); td.append(on); row.append(td);
    const name = document.createElement("td"); name.textContent = `${line.line_id} · ${line.source_file}`; row.append(name);
    for (const value of draft ? draft.values : p ? [...p.start, ...p.end] : ["", "", "", ""]) {
      const cell = document.createElement("td"), input = document.createElement("input"); input.type = "number"; input.step = "any"; input.value = value;
      input.addEventListener("input", () => { on.checked = true; }); cell.append(input); row.append(cell);
    }
    const remember = () => state.drafts.set(line.line_id, { enabled: on.checked, values: [...row.querySelectorAll('input[type="number"]')].map((el) => el.value) });
    row.addEventListener("input", remember); row.addEventListener("change", remember);
    $("s3LayoutRows").append(row);
  }
}

async function load() {
  if (!state.job || !card.open || !init()) return;
  const token = ++state.token, jobId = state.job.job_id, base = `/api/jobs/${encodeURIComponent(jobId)}`, frame = $("s3Frame").value;
  const volume = $("s3Mode").value === "volume";
  card.dataset.mode = volume ? "volume" : "fence";
  $("s3BadgeMode").textContent = volume ? "MODE 02 / VOLUME GRID" : "MODE 01 / FENCE VIEW";
  $("s3Status").textContent = volume ? "กำลังสร้างปริมาตรจากแนวสแกน…" : "กำลังวางแนวสำรวจ…";
  try {
    let data;
    if (volume) {
      const q = new URLSearchParams({ frame, cell_m: $("s3Cell").value, max_gap_m: $("s3Gap").value || "0",
                                      auto_gap: $("s3Scan").value === "raptor" });
      data = await request(`${base}/volume3d?${q}`);
      state.zones = data.zones;
    } else {
      const [scene, zones] = await Promise.all([request(`${base}/scene3d?frame=${frame}&track=${$("s3Track").value}`),
                                                request(`${base}/zones3d?frame=${frame}`)]);
      data = scene; state.zones = zones.zones;
    }
    if (token !== state.token || state.job.job_id !== jobId) return;
    state.data = data; sidebar(data); volume ? buildVolume(data) : build(data);
    $("s3Status").textContent = !data.lines.length ? "ยังไม่มีแนวที่วางได้ — ตรวจ GPS หรือกำหนดพิกัดท้องถิ่นด้านล่าง"
      : volume ? `${data.lines.length} scan lines · grid ${data.grid.nx}×${data.grid.ny}×${data.grid.nz} · ${state.zones.length} zones · ${data.frame.toUpperCase()}`
      : `${data.lines.length} scan lines · ${data.track.toUpperCase()} · ${state.zones.length} zones · ${data.frame.toUpperCase()}`;
    card.dataset.loaded = "true";
  } catch (e) { if (token === state.token) { $("s3Status").textContent = "โหลดฉากไม่ได้: " + e.message; card.dataset.loaded = "error"; } }
}

async function loadZones() {
  // Review decisions change zone colours only; the scene and volume stay as they are.
  if (!state.data || !root) return;
  const token = ++state.zoneToken, jobId = state.job.job_id;
  try {
    const res = await request(`/api/jobs/${encodeURIComponent(jobId)}/zones3d?frame=${state.data.frame}`);
    if (token !== state.zoneToken || state.job.job_id !== jobId) return;
    state.zones = res.zones; buildZones(); visibility();
  } catch (e) { $("s3Status").textContent = "โหลดโซนไม่ได้: " + e.message; }
}

function pick(event, select) {
  if (!root || !state.data) return;
  root.updateMatrixWorld(true);
  const rect = renderer.domElement.getBoundingClientRect();
  raycaster.setFromCamera(new THREE.Vector2((event.clientX - rect.left) / rect.width * 2 - 1, -(event.clientY - rect.top) / rect.height * 2 + 1), camera);
  const targets = [];
  for (const o of lineObjects) {
    if (!o.mesh) continue;
    if (o.mesh.visible) { o.pickMesh.matrixWorld.copy(o.mesh.matrixWorld); targets.push(o.pickMesh); }
    if (o.strip.visible) targets.push(o.strip);
  }
  for (const o of zoneObjects) if (o.visible) targets.push(...o.children.filter((c) => c.userData.kind === "zone"));
  for (const key of ["z", "x", "y"]) if (volObjects[key]?.visible) targets.push(volObjects[key]);
  const hit = raycaster.intersectObjects(targets, false)[0];
  if (hit?.object.userData.kind === "zone") return pickZone(hit.object.userData.zone, select);
  if (hit?.object.userData.kind === "vslice") return pickVoxel(hit);
  if (!hit?.uv) return;
  const line = hit.object.userData.line, col = Math.max(0, Math.min(line.xy_m.length - 1, hit.uv.x * (line.xy_m.length - 1)));
  const fraction = hit.object.userData.kind === "curtain" ? sampleAtDepth(line, -root.worldToLocal(hit.point.clone()).y) : 1 - hit.uv.y;
  const row = Math.max(0, Math.min(line.depth_m.length - 1, (fraction ?? (1 - hit.uv.y)) * (line.depth_m.length - 1)));
  const trace = Math.round(lerpArray(line.trace_indices, col)), d = lerpArray(line.depth_m, row), t = lerpArray(line.time_ns, row);
  const sourceTrace = Math.round(lerpArray(line.source_trace_indices || line.trace_indices, col));
  const east = lerpArray(line.xy_m.map((p) => p[0]), col), north = lerpArray(line.xy_m.map((p) => p[1]), col);
  $("s3Readout").textContent = `${line.line_id} / ${line.source_file} · trace ${trace}${sourceTrace !== trace ? ` (file ${sourceTrace})` : ""} · E ${number(east)} N ${number(north)} m · depth ${number(d)} m · ${number(t)} ns`;
  if (select) {
    state.selected = line.line_id; visibility();
    window.dispatchEvent(new CustomEvent("gpr:3d-pick", { detail: { line: line.line_id, trace, depth: d, time: t } }));
  }
}

function pickZone(z, select) {
  $("s3Readout").textContent = `${z.line_id} โซน ${z.zone_id} · ${z.suspected_type} · ${z.confidence} · ${V3.REVIEW_TH[z.review] || z.review}`
    + ` · ลึก ${number(z.depth_top_m)}–${number(z.depth_bottom_m)} m (v ${z.velocity_m_per_ns} m/ns) · trace ${z.trace_start}–${z.trace_end}`
    + (z.positioned_traces < z.n_traces ? ` · วางตำแหน่งได้ ${z.positioned_traces}/${z.n_traces} trace` : "");
  if (select) {
    state.selected = z.line_id; visibility();
    window.dispatchEvent(new CustomEvent("gpr:3d-pick", { detail: { line: z.line_id, trace: Math.round((z.trace_start + z.trace_end) / 2), depth: z.depth_top_m } }));
  }
}

function pickVoxel(hit) {
  const p = root.worldToLocal(hit.point.clone()), axis = hit.object.userData.axis;
  const depth = axis === "z" ? vol.depth_m[hit.object.userData.index] : -p.y;
  const east = axis === "x" ? vol.x0_m + (hit.object.userData.index + 0.5) * vol.cell_m : p.x + east0;
  const north = axis === "y" ? vol.y0_m + (hit.object.userData.index + 0.5) * vol.cell_m : north0 - p.z;
  const v = V3.voxelAt(vol, east, north, depth);
  if (!v) return;
  const kind = { 2: "วัดจริง (มีแนวสแกนผ่านช่องนี้)", 1: "ประมาณค่าระหว่างแนว", 0: "ไม่มีข้อมูล" }[v.kind];
  $("s3Readout").textContent = `E ${number(v.east)} N ${number(v.north)} m · ลึก ${number(v.depth)} m · `
    + (v.db == null ? "ไม่มีข้อมูล" : `${number(v.db, 1)} dB`) + ` · ${kind}`;
}

function download(blob, name) {
  const url = URL.createObjectURL(blob), a = document.createElement("a"); a.href = url; a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

$("s3LayoutForm").addEventListener("submit", async (event) => {
  event.preventDefault(); if (!state.job) return;
  const lines = {};
  for (const row of $("s3LayoutRows").rows) {
    const inputs = [...row.querySelectorAll("input")];
    if (!inputs[0].checked) continue;
    if (inputs.slice(1).some((el) => el.value === "")) { $("s3Status").textContent = `${row.dataset.lineId}: ระบุพิกัดเริ่ม–จบให้ครบ 4 ค่า`; return; }
    const values = inputs.slice(1).map((el) => Number(el.value)); lines[row.dataset.lineId] = { start: values.slice(0, 2), end: values.slice(2) };
  }
  try {
    await request(`/api/jobs/${encodeURIComponent(state.job.job_id)}/scene3d/layout`, {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ lines }),
    });
    state.drafts.clear(); $("s3Frame").value = "local"; await load();
  } catch (e) { $("s3Status").textContent = e.message; }
});

for (const id of ["s3Frame", "s3Track", "s3Mode"]) $(id).addEventListener("change", load);
for (const id of ["s3Paths", "s3Curtains", "s3Slice", "s3VSlice", "s3VEast", "s3VNorth", "s3Zones", "s3Rejected"]) $(id).addEventListener("change", visibility);
$("s3Build").addEventListener("click", load);
// Scan type presets: CrossOver lines are 0.5 m or more apart; Raptor swaths are denser, so the gap comes from the data.
$("s3Scan").addEventListener("change", () => {
  const scan = $("s3Scan").value;
  if (scan === "crossover") { $("s3Cell").value = "0.1"; $("s3Gap").value = "0.5"; }
  if (scan === "raptor") $("s3Cell").value = "0.05";
  $("s3Gap").disabled = scan === "raptor";
});
for (const id of ["s3Cell", "s3Gap"]) $(id).addEventListener("input", () => { if ($("s3Scan").value === "crossover") $("s3Scan").value = "custom"; });
$("s3Interp").addEventListener("change", () => { sections(); voxelView(); updateSlice(); });
$("s3Voxels").addEventListener("change", voxelView);
$("s3VoxDb").addEventListener("change", voxelView);
$("s3VoxDb").addEventListener("input", () => { $("s3VoxDbValue").textContent = `${$("s3VoxDb").value.replace("-", "−")} dB`; });
for (const id of ["s3VEastPos", "s3VNorthPos"]) $(id).addEventListener("input", () => {
  if (sliceScheduled) return;
  sliceScheduled = true;
  requestAnimationFrame(() => { sliceScheduled = false; sections(); });
});
$("s3Depth").addEventListener("input", () => {
  if (sliceScheduled) return;
  sliceScheduled = true;
  requestAnimationFrame(() => { sliceScheduled = false; updateSlice(); });
});
$("s3Opacity").addEventListener("input", () => { lineObjects.forEach((o) => { if (o.mesh) o.mesh.material.opacity = +$("s3Opacity").value; }); draw(); });
$("s3Exag").addEventListener("change", () => { if (root && state.data) { root.scale.y = +$("s3Exag").value; sidebar(state.data); fit(); } });
$("s3Fit").addEventListener("click", () => fit()); $("s3Top").addEventListener("click", () => fit("top")); $("s3Front").addEventListener("click", () => fit("front"));
$("s3All").addEventListener("click", () => { state.hidden.clear(); document.querySelectorAll(".s3-line input").forEach((el) => el.checked = true); visibility(); });
$("s3None").addEventListener("click", () => { state.hidden = new Set((state.data?.lines || []).map((l) => l.line_id)); document.querySelectorAll(".s3-line input").forEach((el) => el.checked = false); visibility(); });
$("s3Full").addEventListener("click", async () => { try { document.fullscreenElement ? await document.exitFullscreen() : await card.requestFullscreen(); resize(); } catch (e) { $("s3Status").textContent = e.message; } });
$("s3Json").addEventListener("click", () => { if (state.data) download(new Blob([JSON.stringify(state.data, null, 2)], { type: "application/json" }), state.data.grid ? "survey-3d-mode-2.json" : "survey-3d-mode-1.json"); });
function annotatedCanvas() {
  renderer.render(scene, camera);
  const canvas = document.createElement("canvas"); canvas.width = renderer.domElement.width; canvas.height = renderer.domElement.height;
  const ctx = canvas.getContext("2d"), ratio = canvas.width / stage.clientWidth;
  ctx.drawImage(renderer.domElement, 0, 0); ctx.scale(ratio, ratio); ctx.font = "11px system-ui"; ctx.fillStyle = "#b8d4e7";
  for (const l of labels) if (!l.el.hidden) ctx.fillText(l.el.textContent, parseFloat(l.el.style.left) / 100 * stage.clientWidth, parseFloat(l.el.style.top) / 100 * stage.clientHeight);
  ctx.fillStyle = "#07131ddd"; ctx.fillRect(0, 0, stage.clientWidth, 48); ctx.fillRect(0, stage.clientHeight - 37, stage.clientWidth, 37);
  ctx.fillStyle = "#e5f2fd";
  const grid = state.data.grid;
  ctx.fillText(grid ? `3D MODE 02 VOLUME | ${state.data.title} | ${state.data.frame} | cell ${grid.cell_m} m, dz ${grid.dz_m} m, max gap ${state.data.parameters.max_gap_m} m`
    : `3D MODE 01 | ${state.data.title} | ${state.data.frame} | ${state.data.track}`, 15, 20);
  ctx.fillText(`Depth from line velocity | relative surface z=0 | vertical ${$("s3Exag").value}x | ${grid ? state.data.parameters.reference : state.data.normalization}`, 15, 38);
  const legendX = stage.clientWidth - 185, legendY = stage.clientHeight - 78;
  const gradient = ctx.createLinearGradient(legendX, 0, legendX + 165, 0);
  const colours = state.data.track === "envelope" || grid ? ["#30123b", "#4686fb", "#1ae4b6", "#a2fc3c", "#faba39", "#e4460a", "#7a0403"] : ["#000", "#fff"];
  colours.forEach((colour, i) => gradient.addColorStop(i / (colours.length - 1), colour));
  ctx.fillStyle = gradient; ctx.fillRect(legendX, legendY, 165, 8); ctx.fillStyle = "#b8d4e7";
  ctx.fillText(state.data.track === "envelope" || grid ? "-40 dB                 0 dB / line max" : "- amplitude          + amplitude", legendX, legendY + 23);
  ctx.fillText(grid ? "Solid = cell crossed by a scan line; faded = interpolated between two lines; empty = no data. Colour is relative energy, not a probability or confirmed void."
    : "Measured 2D sections; gaps unsurveyed. Colour is not a probability or confirmed void.", 15, stage.clientHeight - 15);
  return canvas;
}
$("s3Png").addEventListener("click", () => {
  if (!renderer || !root || !state.data) return;
  annotatedCanvas().toBlob((blob) => { if (blob) download(blob, state.data.grid ? "survey-3d-mode-2.png" : "survey-3d-mode-1.png"); });
});
$("s3Report").addEventListener("click", () => {
  if (!renderer || !root || !state.data || !state.job) return;
  const caption = prompt("คำอธิบายภาพในรายงาน", `มุมมอง 3D · ${state.data.title}`);
  if (caption === null) return;
  annotatedCanvas().toBlob(async (blob) => {
    if (!blob) return;
    const form = new FormData();
    form.append("file", blob, "3d.png"); form.append("caption", caption); form.append("source", "3d");
    try { const list = await request(`/api/jobs/${state.job.job_id}/snapshots`, { method: "POST", body: form }); $("s3Status").textContent = `เก็บภาพไว้ใส่รายงานแล้ว (${list.length} ภาพ)`; }
    catch (e) { $("s3Status").textContent = e.message; }
  });
});
card.addEventListener("toggle", () => { if (card.open) { state.data ? (resize(), draw()) : load(); } });

function onJob({ job, line }) {
  if (!job) return;
  // Excluded sections change the volume (not the mode 1 curtains); zone edits only change the zones.
  const signature = JSON.stringify([job.job_id, job.lines.map((l) => [l.line_id, l.revision, l.n_samples, l.n_traces, l.trace_offset, l.crop, l.velocity_m_per_ns, l.options,
                                                                       $("s3Mode").value === "volume" ? l.sections : null])]);
  const zoneSignature = JSON.stringify(job.lines.map((l) => [l.sections, (l.zones || []).map((z) => [z.zone_id, z.review, z.trace_start, z.trace_end,
                                                                                                   z.time_top_ns, z.time_bottom_ns, z.suspected_type, z.confidence])]));
  if (state.job?.job_id !== job.job_id) { state.hidden.clear(); state.drafts.clear(); state.data = null; $("s3Frame").value = "gps"; }
  state.job = job; state.selected = line;
  if (state.signature !== signature) {
    state.signature = signature; state.zoneSignature = zoneSignature; state.data = null; state.token++;
    if (root) root.visible = false;
    load();
  } else if (state.zoneSignature !== zoneSignature) { state.zoneSignature = zoneSignature; loadZones(); }
  else visibility();
}
window.addEventListener("gpr:job", (e) => onJob(e.detail));
const current = window.getGprSceneContext?.(); if (current?.job) onJob(current);

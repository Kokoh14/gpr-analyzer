import * as THREE from "three";

// 3D mode 2 building blocks. The volume comes from /volume3d: uint8 per (depth, north row, east column),
// 0 = no data, 1..255 = floor..0 dB. cells: 2 measured, 1 interpolated, 0 empty (see app/volume3d.py).
export const MEASURED = 2, INTERPOLATED = 1;

export function decode(data) {
  const bytes = (b64) => Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
  return { ...data.grid, floor: data.parameters.floor_db, vol: bytes(data.volume_b64), cells: bytes(data.cells_b64) };
}

export const dbOf = (v, code) => (code ? v.floor + (code - 1) / 254 * -v.floor : null);
export const codeOf = (v, db) => Math.max(1, Math.min(255, Math.round(1 + (db - v.floor) / -v.floor * 254)));

// Turbo colour map (polynomial fit by Google, Apache-2.0), same scale as the mode 1 envelope curtains.
export const TURBO = Array.from({ length: 256 }, (_, i) => {
  const t = i / 255, c = (a) => Math.round(255 * Math.max(0, Math.min(1, a.reduceRight((s, k) => s * t + k, 0))));
  return [c([0.13572138, 4.6153926, -42.66032258, 132.13108234, -152.94239396, 59.28637943]),
          c([0.09140261, 2.19418839, 4.84296658, -14.18503333, 4.27729857, 2.82956604]),
          c([0.1066733, 12.64194608, -60.58204836, 110.36276771, -89.90310912, 27.34824973])];
});

function pixels(v, w, h, at, interpolated) {
  // at(a, b) -> [code, cell kind]; measured opaque, interpolated faded (or hidden), no data transparent.
  const rgba = new Uint8Array(w * h * 4);
  for (let b = 0; b < h; b++) for (let a = 0; a < w; a++) {
    const [code, kind] = at(a, b), o = (b * w + a) * 4;
    if (!code || !kind || (kind === INTERPOLATED && !interpolated)) continue;
    const c = TURBO[code];
    rgba[o] = c[0]; rgba[o + 1] = c[1]; rgba[o + 2] = c[2]; rgba[o + 3] = kind === MEASURED ? 255 : 130;
  }
  const tex = new THREE.DataTexture(rgba, w, h, THREE.RGBAFormat);
  tex.magFilter = THREE.NearestFilter; tex.minFilter = THREE.NearestFilter; tex.colorSpace = THREE.SRGBColorSpace;
  tex.needsUpdate = true; return tex;
}

// One textured quad; corners in (east, north, depth) with the texture's first row at corners[0]-corners[1].
function quad(vec, corners, texture) {
  const g = new THREE.BufferGeometry(), p = corners.map((c) => vec(...c));
  g.setAttribute("position", new THREE.Float32BufferAttribute(p.flatMap((q) => [q.x, q.y, q.z]), 3));
  g.setAttribute("uv", new THREE.Float32BufferAttribute([0, 0, 1, 0, 1, 1, 0, 1], 2));
  g.setIndex([0, 1, 2, 0, 2, 3]); g.computeBoundingSphere();
  return new THREE.Mesh(g, new THREE.MeshBasicMaterial({ map: texture, side: THREE.DoubleSide, transparent: true, depthWrite: false }));
}

const ext = (v) => ({ x1: v.x0_m + v.nx * v.cell_m, y1: v.y0_m + v.ny * v.cell_m,
                      top: v.depth_m[0] - v.dz_m / 2, bottom: v.depth_m[v.nz - 1] + v.dz_m / 2 });

export function depthSlice(v, vec, k, interpolated) {
  const e = ext(v), d = v.depth_m[k], plane = v.nx * v.ny;
  const tex = pixels(v, v.nx, v.ny, (i, j) => [v.vol[k * plane + j * v.nx + i], v.cells[j * v.nx + i]], interpolated);
  const mesh = quad(vec, [[v.x0_m, v.y0_m, d], [e.x1, v.y0_m, d], [e.x1, e.y1, d], [v.x0_m, e.y1, d]], tex);
  mesh.userData = { kind: "vslice", axis: "z", index: k }; return mesh;
}

export function eastSection(v, vec, i, interpolated) {
  // Vertical section at a fixed east column: texture u = north, v = depth.
  const e = ext(v), x = v.x0_m + (i + 0.5) * v.cell_m, plane = v.nx * v.ny;
  const tex = pixels(v, v.ny, v.nz, (j, k) => [v.vol[k * plane + j * v.nx + i], v.cells[j * v.nx + i]], interpolated);
  const mesh = quad(vec, [[x, v.y0_m, e.top], [x, e.y1, e.top], [x, e.y1, e.bottom], [x, v.y0_m, e.bottom]], tex);
  mesh.userData = { kind: "vslice", axis: "x", index: i }; return mesh;
}

export function northSection(v, vec, j, interpolated) {
  const e = ext(v), y = v.y0_m + (j + 0.5) * v.cell_m, plane = v.nx * v.ny;
  const tex = pixels(v, v.nx, v.nz, (i, k) => [v.vol[k * plane + j * v.nx + i], v.cells[j * v.nx + i]], interpolated);
  const mesh = quad(vec, [[v.x0_m, y, e.top], [e.x1, y, e.top], [e.x1, y, e.bottom], [v.x0_m, y, e.bottom]], tex);
  mesh.userData = { kind: "vslice", axis: "y", index: j }; return mesh;
}

// Voxel at (i, j, k) -> readout record; null outside the grid.
export function voxelAt(v, east, north, depth) {
  const i = Math.floor((east - v.x0_m) / v.cell_m), j = Math.floor((north - v.y0_m) / v.cell_m);
  const k = Math.round((depth - v.depth_m[0]) / v.dz_m);
  if (i < 0 || j < 0 || k < 0 || i >= v.nx || j >= v.ny || k >= v.nz) return null;
  const kind = v.cells[j * v.nx + i], code = v.vol[k * v.nx * v.ny + j * v.nx + i];
  return { i, j, k, kind, db: dbOf(v, code), depth: v.depth_m[k],
           east: v.x0_m + (i + 0.5) * v.cell_m, north: v.y0_m + (j + 0.5) * v.cell_m };
}

// Every voxel at or above a level, as boxes. Interpolated voxels go in a separate, fainter mesh.
// When more than `limit` qualify, the strongest are kept and the level actually used is returned.
export function voxels(v, vec, minDb, interpolated, limit = 120000) {
  const plane = v.nx * v.ny, hist = new Uint32Array(256);
  for (let n = 0; n < v.vol.length; n++) {
    const kind = v.cells[n % plane];
    if (kind === MEASURED || (interpolated && kind === INTERPOLATED)) hist[v.vol[n]]++;
  }
  let level = codeOf(v, minDb), total = 0;
  for (let c = 255; c >= level; c--) { if (total + hist[c] > limit) { level = c + 1; break; } total += hist[c]; }
  const group = new THREE.Group(), box = new THREE.BoxGeometry(v.cell_m * 0.92, v.dz_m * 0.92, v.cell_m * 0.92);
  const counts = { measured: 0, interpolated: 0 };
  for (const kind of [MEASURED, INTERPOLATED]) {
    if (kind === INTERPOLATED && !interpolated) continue;
    const material = new THREE.MeshLambertMaterial({ transparent: kind === INTERPOLATED, opacity: kind === INTERPOLATED ? 0.35 : 1,
                                                    depthWrite: kind === MEASURED });
    const mesh = new THREE.InstancedMesh(box, material, total || 1), m = new THREE.Matrix4(), colour = new THREE.Color();
    let n = 0;
    for (let k = 0; k < v.nz; k++) for (let j = 0; j < v.ny; j++) for (let i = 0; i < v.nx; i++) {
      const code = v.vol[k * plane + j * v.nx + i];
      if (code < level || v.cells[j * v.nx + i] !== kind) continue;
      m.setPosition(vec(v.x0_m + (i + 0.5) * v.cell_m, v.y0_m + (j + 0.5) * v.cell_m, v.depth_m[k]));
      mesh.setMatrixAt(n, m);
      const c = TURBO[code]; mesh.setColorAt(n, colour.setRGB(c[0] / 255, c[1] / 255, c[2] / 255, THREE.SRGBColorSpace)); n++;
    }
    mesh.count = n; counts[kind === MEASURED ? "measured" : "interpolated"] = n;
    if (n) { mesh.instanceMatrix.needsUpdate = true; mesh.instanceColor.needsUpdate = true; group.add(mesh); }
  }
  group.add(new THREE.HemisphereLight(0xffffff, 0x334455, 2.2));
  return { group, counts, level_db: dbOf(v, level), truncated: level > codeOf(v, minDb) };
}

export const ZONE_COLOURS = { void_like: 0xff4f5e, pipe_point: 0x46b4ff, layer: 0x63d471, scattered: 0xb98cff,
                              artifact: 0x8a97a3, unknown: 0xffd166 };
export const REVIEW_TH = { auto: "รอตรวจ", accepted: "ยืนยันแล้ว", edited: "แก้ไขแล้ว", rejected: "ปฏิเสธ" };

// A zone is a wall along its positioned traces from its top to bottom depth (each zone's own velocity).
// Reviewed zones are solid; unreviewed are faint; rejected are drawn only on request.
export function zoneWall(zone, vec) {
  const pos = [], idx = [], path = zone.path_m;
  for (const [x, n] of path) for (const d of [zone.depth_top_m, zone.depth_bottom_m]) { const p = vec(x, n, d); pos.push(p.x, p.y, p.z); }
  for (let s = 0; s < path.length - 1; s++) { const a = 2 * s; idx.push(a, a + 1, a + 2, a + 1, a + 3, a + 2); }
  const reviewed = zone.review === "accepted" || zone.review === "edited";
  const colour = zone.review === "rejected" ? 0x6b7680 : ZONE_COLOURS[zone.suspected_type] ?? 0xffd166;
  const group = new THREE.Group();
  if (path.length >= 2) {
    const g = new THREE.BufferGeometry(); g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3)); g.setIndex(idx);
    g.computeBoundingSphere();
    const wall = new THREE.Mesh(g, new THREE.MeshBasicMaterial({ color: colour, transparent: true, opacity: reviewed ? 0.55 : 0.28,
                                                                  side: THREE.DoubleSide, depthWrite: false }));
    wall.userData = { kind: "zone", zone }; group.add(wall);
  }
  const ring = [...path.map(([x, n]) => vec(x, n, zone.depth_top_m)), ...path.slice().reverse().map(([x, n]) => vec(x, n, zone.depth_bottom_m))];
  ring.push(ring[0].clone());
  const outline = new THREE.Line(new THREE.BufferGeometry().setFromPoints(ring),
                                 new THREE.LineBasicMaterial({ color: colour, transparent: !reviewed, opacity: reviewed ? 1 : 0.7 }));
  outline.userData = { kind: "zone-outline", zone }; group.add(outline);
  return group;
}

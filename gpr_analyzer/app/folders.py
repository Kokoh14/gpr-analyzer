"""Choose a data folder on this computer and check it before processing.

The app runs on the user's own computer, so instead of uploading (copying) large surveys it can read
them where they are. pick_folder opens the normal Windows folder dialog; list_dir backs the in-page
folder browser used when that dialog is not available. scan_folder reads only headers, file sizes and
the small GPS / marker files, so a survey of many lines is checked in seconds:

- each ImpulseRadar line has its .iprh and .iprb, the header has what the reader needs, the data file
  holds whole traces and agrees with LAST TRACE;
- the .cor GPS covers the line (first/last fix near the ends, no long gaps) and its length agrees with
  the wheel distance;
- markers (.mrk) are counted; files that belong to no line are listed.
"""

from __future__ import annotations

import os
import string
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np

from gpr_analyzer.app import geo
from gpr_analyzer.app.positioning import positioning_qa
from gpr_analyzer.app.files import FAMILIES, sort_files
from gpr_analyzer.io._util import parse_key_value_header, read_text, to_float
from gpr_analyzer.io.impulseradar import _side_file, read_cor, read_mrk

MAX_FILES = 5000
GPS_GAP_WARN_M = 5.0  # longest jump between consecutive fixes before we warn
LENGTH_MISMATCH_WARN = 0.10  # GPS length vs wheel length

_DIALOG = r"""
import sys, tkinter as tk
from tkinter import filedialog
root = tk.Tk(); root.withdraw(); root.attributes("-topmost", True)
p = filedialog.askdirectory(title=sys.argv[1], initialdir=sys.argv[2] or None, mustexist=True)
sys.stdout.write(p or "")
"""


def pick_folder(title: str = "เลือกโฟลเดอร์ข้อมูล GPR", start: str = "") -> str | None:
    """Open the system folder dialog (Tk) in a separate process. None if cancelled; RuntimeError if unavailable."""
    try:
        out = subprocess.run([sys.executable, "-c", _DIALOG, title, start], capture_output=True, text=True,
                             timeout=600)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise RuntimeError(f"folder dialog not available: {e}") from None
    if out.returncode != 0:
        raise RuntimeError("folder dialog not available (tkinter missing)")
    return out.stdout.strip() or None


def _roots() -> list[str]:
    if os.name == "nt":
        return [f"{d}:\\" for d in string.ascii_uppercase if Path(f"{d}:\\").exists()]
    return ["/"]


def list_dir(path: str | None) -> dict[str, Any]:
    """Sub-folders of `path` and how many GPR data files it holds (empty path = drives / home)."""
    if not path:
        home = str(Path.home())
        return {"path": "", "parent": None, "dirs": [{"name": r, "path": r} for r in [home, *_roots()]], "gpr_files": 0}
    p = Path(path).expanduser()
    if not p.is_dir():
        raise ValueError(f"not a folder: {path}")
    dirs, n = [], 0
    try:
        for e in sorted(p.iterdir(), key=lambda q: q.name.lower()):
            if e.name.startswith((".", "$")):
                continue
            try:
                if e.is_dir():
                    dirs.append({"name": e.name, "path": str(e)})
                elif e.suffix.lower() in FAMILIES:
                    n += 1
            except OSError:
                continue
    except PermissionError:
        raise ValueError(f"no permission to read {path}") from None
    parent = str(p.parent) if p.parent != p else ""
    return {"path": str(p), "parent": parent, "dirs": dirs, "gpr_files": n}


def folder_files(path: str | Path, recursive: bool = False) -> list[Path]:
    p = Path(path).expanduser()
    if not p.is_dir():
        raise ValueError(f"not a folder: {path}")
    it = p.rglob("*") if recursive else p.iterdir()
    out = []
    for f in it:
        if f.is_file() and not f.name.startswith("."):
            out.append(f)
            if len(out) > MAX_FILES:
                raise ValueError(f"more than {MAX_FILES} files; choose a smaller folder")
    return sorted(out)


def _issue(level: str, code: str, text: str) -> dict[str, str]:
    return {"level": level, "code": code, "text": text}


def check_impulseradar(primary: Path) -> dict[str, Any]:
    """Header / data / GPS / marker check of one ImpulseRadar line without reading the radar data."""
    hdr = primary.with_suffix(".iprh")
    dat = primary.with_suffix(".iprb")
    if not hdr.exists():
        hdr = next((q for q in primary.parent.glob(primary.stem + ".*") if q.suffix.lower() == ".iprh"), hdr)
    if not dat.exists():
        dat = next((q for q in primary.parent.glob(primary.stem + ".*") if q.suffix.lower() == ".iprb"), dat)
    out: dict[str, Any] = {"issues": []}
    iss = out["issues"]
    if not hdr.exists():
        iss.append(_issue("error", "no_header", "ไม่มีไฟล์ header (.iprh)"))
        return out
    if not dat.exists():
        iss.append(_issue("error", "no_data", "ไม่มีไฟล์ข้อมูล (.iprb)"))
        return out
    h = parse_key_value_header(read_text(hdr))
    samples = int(to_float(h.get("SAMPLES")) or 0)
    version = int(to_float(h.get("DATA VERSION")) or 16)
    if samples <= 0:
        iss.append(_issue("error", "no_samples", "header ไม่มีค่า SAMPLES"))
        return out
    if not (to_float(h.get("FREQUENCY")) or to_float(h.get("TIMEWINDOW"))):
        iss.append(_issue("error", "no_timing", "header ไม่มีทั้ง FREQUENCY และ TIMEWINDOW"))
    bps = version // 8 if version in (16, 32) else 2
    size = dat.stat().st_size
    n_traces, rest = divmod(size // bps, samples)
    out.update({"n_traces": n_traces, "n_samples": samples, "data_mb": round(size / 1e6, 1),
                "antenna": h.get("ANTENNA") or h.get("ANTENNAS") or "", "date": h.get("DATE", "")})
    dx = next((v for v in (to_float(h.get("DISTANCE INTERVAL")), to_float(h.get("USER DISTANCE INTERVAL"))) if v and v > 0), None)
    out["dx_m"] = dx
    out["length_m"] = round(n_traces * dx, 2) if dx else None
    fs, tw = to_float(h.get("FREQUENCY")), to_float(h.get("TIMEWINDOW"))
    out["time_window_ns"] = round(samples * 1000.0 / fs, 1) if fs else tw
    if n_traces == 0:
        iss.append(_issue("error", "empty", "ไฟล์ข้อมูลไม่มี trace ที่สมบูรณ์"))
    if rest:
        iss.append(_issue("warn", "partial_trace", "trace สุดท้ายไม่ครบ (บันทึกค้าง) จะตัดทิ้ง"))
    last = to_float(h.get("LAST TRACE"))
    if last and n_traces and abs(last - n_traces) > 1:
        iss.append(_issue("warn", "trace_count", f"header บอก {int(last)} trace แต่ไฟล์มี {n_traces} trace"))
    if not dx:
        iss.append(_issue("warn", "no_spacing", "ไม่มีระยะห่าง trace (แสดงระยะเป็นเมตรไม่ได้)"))
    if not to_float(h.get("ZERO LEVEL")):
        iss.append(_issue("info", "no_zero_level", "ไม่มี ZERO LEVEL ใน header (โปรแกรมจะหา time-zero เอง)"))

    cor = _side_file(dat, ".cor")
    out["gps_fixes"] = 0
    out["positioning"] = positioning_qa([], n_traces, dx)
    if cor is None:
        iss.append(_issue("warn", "no_gps", "ไม่มีไฟล์ GPS (.cor) วางบนแผนที่ไม่ได้"))
    else:
        rows = read_cor(cor)
        out["gps_fixes"] = len(rows)
        qa = out["positioning"] = positioning_qa(rows, n_traces, dx)
        if qa["low_density"]:
            iss.append(_issue("warn", "gps_density", f"GPS จุดห่างตามวงล้อสูงสุด {qa['max_wheel_gap_m']:.1f} m (>5 m): ตรวจความหนาแน่น ไม่ยืนยันว่าเสีย fix"))
        if qa["sharp_turns"]:
            iss.append(_issue("warn", "sharp_turn", "แนวเลี้ยวกลับ ≥120°: อาจเกิด swath ซ้อนกัน (เกณฑ์เตือน ไม่ใช่ผลตรวจขอบ swath)"))
        pos = geo.trace_positions(rows, max(n_traces, 1))
        if pos is None:
            iss.append(_issue("warn", "gps_unusable", f"ไฟล์ GPS {cor.name} มีพิกัดที่ใช้ได้น้อยกว่า 2 จุด"))
        else:
            lat, lon = pos
            trs = sorted(r["trace"] for r in rows)
            idx = sorted(set(np.linspace(0, n_traces - 1, min(n_traces, 200)).astype(int).tolist()))
            out["track"] = [[round(float(lat[i]), 7), round(float(lon[i]), 7)] for i in idx]
            out["length_gps_m"] = round(geo.path_length_m(lat, lon), 2)
            fl = np.array([[r["lat"], r["lon"]] for r in sorted(rows, key=lambda r: r["trace"])])
            gaps = [geo.path_length_m(fl[i:i + 2, 0], fl[i:i + 2, 1]) for i in range(len(fl) - 1)]
            out["gps_max_gap_m"] = round(max(gaps), 2) if gaps else 0.0
            cover = (trs[-1] - trs[0]) / max(n_traces - 1, 1)
            out["gps_coverage"] = round(min(cover, 1.0), 3)
            if cover < 0.9:
                iss.append(_issue("warn", "gps_short", f"GPS ครอบคลุมเพียง trace {trs[0]}–{trs[-1]} จาก {n_traces}"))
            if out["gps_max_gap_m"] > GPS_GAP_WARN_M:
                iss.append(_issue("warn", "gps_gap", f"GPS ขาดช่วง: กระโดดไกลสุด {out['gps_max_gap_m']:.1f} m"))
            if out["length_m"] and out["length_gps_m"] and \
                    abs(out["length_gps_m"] - out["length_m"]) > LENGTH_MISMATCH_WARN * out["length_m"] + 1.0:
                iss.append(_issue("warn", "length_mismatch",
                                  f"ความยาวตาม GPS {out['length_gps_m']:.1f} m ต่างจากล้อวัดระยะ {out['length_m']:.1f} m"))
    mrk = _side_file(dat, ".mrk")
    out["markers"] = len(read_mrk(mrk)) if mrk is not None else 0
    return out


def scan_folder(path: str | Path, recursive: bool = False) -> dict[str, Any]:
    """Lines found in a folder with a completeness check each (headers and side files only)."""
    files = folder_files(path, recursive)
    s = sort_files(files)
    lines = []
    for g in sorted(s.groups, key=lambda g: str(g.primary).lower()):
        row: dict[str, Any] = {"name": g.stem, "family": g.family, "primary": str(g.primary),
                               "folder": str(g.primary.parent), "files": sorted(p.name for p in g.members + g.companions)}
        if g.family == "impulseradar":
            try:
                row.update(check_impulseradar(g.primary))
            except Exception as e:  # noqa: BLE001 - shown per line
                row["issues"] = [_issue("error", "unreadable", f"อ่านไม่ได้: {type(e).__name__}: {e}")]
        else:
            row["issues"] = [_issue("info", "other_format", "ไม่ใช่ ImpulseRadar: ตรวจละเอียดตอนประมวลผล")]
        levels = {i["level"] for i in row["issues"]}
        row["status"] = "error" if "error" in levels else "warn" if "warn" in levels else "ok"
        lines.append(row)
    # side files of dual-channel lines (LINE.cor for LINE_0 / LINE_1) are not orphans
    used_stems = {Path(r["primary"]).stem.lower() for r in lines}
    used_stems |= {s[:-2] for s in used_stems if len(s) > 2 and s[-2] == "_"}
    others = [p for p in s.unassigned if p.stem.lower() not in used_stems]
    return {
        "path": str(Path(path).expanduser()), "recursive": recursive, "n_files": len(files), "lines": lines,
        "unassigned": [str(p.relative_to(Path(path).expanduser())) for p in others],
        "summary": {"lines": len(lines), "ok": sum(r["status"] == "ok" for r in lines),
                    "warn": sum(r["status"] == "warn" for r in lines),
                    "error": sum(r["status"] == "error" for r in lines),
                    "no_gps": sum(not r.get("track") for r in lines),
                    "length_m": round(sum(r.get("length_m") or 0 for r in lines), 1)},
    }

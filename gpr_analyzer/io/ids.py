"""IDS GeoRadar zone reader (acquisition software SRSv2, *.ZON folders inside a *.MIS job).

No public spec was used; the layout below was worked out from the user's own survey files
(SRT northern line, June 2022, 400 MHz, 3 saved channels) and checked against them:

- one zone folder (NAME.ZON) holds LIccpppp.dt: cc = channel 01, 02, ..., pppp = part 0001, 0002, ...
  The parts of one channel are one line cut every few km; they are joined in part order.
- a .dt file is a sequence of fixed-size records: 4-byte tag + payload. Record 0 (tag 'V') holds the
  record size as int32 at byte 4. Header records follow (tags 'I' zone name, 'C' date, 'ATR' antenna
  positions, 'H' scan settings, ...), then one record per trace with tag[0] == 'R' and the payload =
  int16 little-endian samples.
- 'H' record: int32 at payload bytes 8..12 = samples per trace; then ASCII numbers, of which the 3rd is
  the time window in seconds and the 7th the trace step in metres (checked: 80 ns / 512 samples and
  0.36 m, which matches the GPS track length to 0.5 %).
- 'ATR' record: TX x, y, angle, MHz, RX x, y, angle, MHz (metres across the cart).
- channel 1 trace records with tag[1] != 0 are the traces where a GPS sentence was logged; the n-th
  $GPGGA line of LI01pppp.GPS belongs to the n-th such trace of part pppp (one every 50 traces in
  the sample, count equal to the number of GGA lines).
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np

from gpr_analyzer.core import Radargram

_NAME = re.compile(r"^LI(\d\d)(\d{4})\.dt$", re.IGNORECASE)
_NUM = re.compile(rb"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")
_SCI = re.compile(rb"-?\d\.\d+[eE][-+]\d+")  # H record values; avoids digit bytes in its binary part


def zone_name(path: Path) -> str:
    """'.../North_0140to10500_ob_150622.ZON/LI010001.dt' -> 'North_0140to10500_ob_150622'."""
    parent = Path(path).parent.name
    return parent[:-4] if parent.lower().endswith(".zon") else parent


def zone_parts(folder: Path) -> dict[int, list[Path]]:
    """Channel number -> its .dt parts in order."""
    out: dict[int, list[tuple[int, Path]]] = {}
    for p in Path(folder).iterdir():
        m = _NAME.match(p.name)
        if m and p.is_file():
            out.setdefault(int(m.group(1)), []).append((int(m.group(2)), p))
    return {ch: [p for _, p in sorted(v)] for ch, v in sorted(out.items())}


def is_ids_dt(path: Path) -> bool:
    return bool(_NAME.match(Path(path).name))


def _records(path: Path) -> np.ndarray:
    raw = np.fromfile(path, dtype=np.uint8)
    if raw.size < 8:
        raise ValueError(f"{path}: too short for an IDS .dt file")
    rec = int(raw[4:8].view("<i4")[0])
    if rec < 64 or rec % 2:
        raise ValueError(f"{path}: bad record size {rec}")
    n = raw.size // rec
    return raw[:n * rec].reshape(n, rec)


def _header(recs: np.ndarray, path: Path) -> tuple[dict, np.ndarray]:
    """Header values and the trace records."""
    tags = [bytes(r[:4]) for r in recs]
    first = next((i for i, t in enumerate(tags) if t[:1] == b"R" and i > 0), None)
    if first is None:
        raise ValueError(f"{path}: no trace records")
    hdr: dict = {}
    for i in range(first):
        t, body = tags[i].rstrip(b"\0"), bytes(recs[i, 4:])
        if t[:1] == b"H":
            hdr["n_samples"] = int(np.frombuffer(body[8:12], "<i4")[0])
            nums = [float(x) for x in _SCI.findall(body)]
            if len(nums) >= 7:
                hdr["window_ns"] = nums[2] * 1e9
                hdr["dx_m"] = nums[6]
        elif t == b"ATR":
            nums = [float(x) for x in _NUM.findall(body)]
            if len(nums) >= 8:
                hdr["tx_x"], hdr["rx_x"], hdr["freq_mhz"] = nums[0], nums[4], nums[3]
        elif t == b"I":
            hdr["zone"] = body.split(b"\0")[0].decode("latin-1").strip()
        elif t == b"C":
            hdr["date"] = body.split(b"\0")[0].decode("latin-1").strip()
    traces = recs[first:]
    traces = traces[traces[:, 0] == ord("R")]
    n = (recs.shape[1] - 4) // 2
    if hdr.get("n_samples") != n:
        raise ValueError(f"{path}: header says {hdr.get('n_samples')} samples, records hold {n}")
    if not hdr.get("window_ns") or hdr["window_ns"] <= 0:
        raise ValueError(f"{path}: time window not found in header")
    return hdr, traces


def read_ids_header(path: str | Path) -> dict:
    """Header values and trace count of one part, reading only its first records (for folder checks)."""
    path = Path(path)
    with path.open("rb") as fh:
        head = fh.read(8)
        if len(head) < 8:
            raise ValueError(f"{path}: too short for an IDS .dt file")
        rec = int(np.frombuffer(head[4:8], "<i4")[0])
        if rec < 64 or rec % 2:
            raise ValueError(f"{path}: bad record size {rec}")
        fh.seek(0)
        recs = np.frombuffer(fh.read(rec * 64), np.uint8)
    recs = recs[:recs.size // rec * rec].reshape(-1, rec)
    hdr, traces = _header(recs, path)
    hdr["n_traces"] = path.stat().st_size // rec - (len(recs) - len(traces))
    return hdr


def read_gga(path: str | Path) -> list[dict | None]:
    """One entry per $..GGA line (None when that line has no position), in file order."""
    out: list[dict | None] = []
    for line in Path(path).read_bytes().decode("latin-1").splitlines():
        f = line.strip().split(",")
        if len(f) < 10 or not f[0].endswith("GGA"):
            continue
        try:
            lat = int(f[2][:2]) + float(f[2][2:]) / 60.0
            lon = int(f[4][:3]) + float(f[4][3:]) / 60.0
            q = int(f[6] or 0)
        except (ValueError, IndexError):
            out.append(None)
            continue
        t = f[1]
        row = {"lat": -lat if f[3] == "S" else lat, "lon": -lon if f[5] == "W" else lon,
               "time": f"{t[0:2]}:{t[2:4]}:{t[4:]}" if len(t) >= 6 else t, "quality": q}
        try:
            row["elevation"] = float(f[9])
        except ValueError:
            pass
        out.append(row)
    return out


def _gps(ch1_parts: list[Path]) -> list[dict]:
    rows, offset = [], 0
    for p in ch1_parts:
        _, traces = _header(_records(p), p)
        flagged = np.where(traces[:, 1] != 0)[0]
        gps = next((q for q in p.parent.glob(p.stem + ".*") if q.suffix.lower() == ".gps"), None)
        if gps is not None:
            for tr, fix in zip(flagged, read_gga(gps)):
                if fix is not None and fix["quality"] > 0:
                    rows.append({"trace": int(tr) + offset, **fix})
        offset += len(traces)
    return rows


def read_ids(path: str | Path) -> list[Radargram]:
    """Every saved channel of the zone that holds `path` (any LIccpppp.dt), parts joined."""
    path = Path(path)
    parts = zone_parts(path.parent)
    if not parts:
        raise ValueError(f"{path}: no LIccpppp.dt files in {path.parent}")
    gps = _gps(parts[min(parts)])
    out = []
    for ch, files in parts.items():
        hdr, blocks = None, []
        for p in files:
            h, traces = _header(_records(p), p)
            if hdr is None:
                hdr = h
            elif h["n_samples"] != hdr["n_samples"]:
                raise ValueError(f"{p}: {h['n_samples']} samples, first part has {hdr['n_samples']}")
            blocks.append(np.ascontiguousarray(traces[:, 4:]).view("<i2"))
        data = np.concatenate(blocks).T.astype(np.float32)
        meta = {"format": "ids", "source": str(files[0]), "parts": [p.name for p in files],
                "zone": hdr.get("zone") or zone_name(path), "date": hdr.get("date", ""),
                "channel": ch, "channel_name": f"channel {ch}",
                "dead_traces": [int(i) for i in np.where(np.ptp(data, axis=0) == 0)[0]]}
        if hdr.get("freq_mhz"):
            meta["antenna_frequency_mhz"] = hdr["freq_mhz"]
        if "tx_x" in hdr:
            meta["antenna_separation_m"] = round(abs(hdr["tx_x"] - hdr["rx_x"]), 4)
            meta["cross_offset_m"] = round((hdr["tx_x"] + hdr["rx_x"]) / 2, 4)
        if gps:
            meta["gps"] = gps
        dx = hdr.get("dx_m") if hdr.get("dx_m", 0) > 0 else None
        out.append(Radargram(data, dt_ns=hdr["window_ns"] / hdr["n_samples"], dx_m=dx, metadata=meta))
    return out

import numpy as np
import pytest

from gpr_analyzer.io.sniff import Candidate, candidates, expand_trace_headers, main, sniff, text_runs, to_radargram


def synthetic_profile(n_samples=256, n_traces=120, seed=0):
    """Direct wave + a dipping layer + a hyperbola + noise, like a simple real radargram."""
    rng = np.random.default_rng(seed)
    t = np.arange(n_samples)[:, None]
    x = np.arange(n_traces)[None, :]

    def ricker(t0, f=0.08):
        a = (np.pi * f * (t - t0)) ** 2
        return (1 - 2 * a) * np.exp(-a)

    d = 3000 * ricker(20) + 1200 * ricker(90 + 0.3 * x) + 900 * ricker(np.sqrt(150.0**2 + (2.0 * (x - 60)) ** 2))
    return d + rng.normal(0, 40, size=d.shape)


def write_unknown(path, data, header=b"", trace_header=0, dtype=">i2"):
    """Per-trace headers hold a trace counter plus varying bytes, as real ones usually do."""
    rng = np.random.default_rng(9)
    with open(path, "wb") as f:
        f.write(header)
        for i in range(data.shape[1]):
            th = bytearray(rng.integers(0, 256, size=trace_header, dtype=np.uint8).tobytes())
            th[: min(4, trace_header)] = i.to_bytes(4, "little")[: min(4, trace_header)]
            f.write(bytes(th))
            f.write(data[:, i].astype(dtype).tobytes())


def test_text_runs_finds_ascii_and_utf16():
    raw = b"\x00\x01Vendor TEST\x00\x00" + "Derinlik".encode("utf-16-le") + b"\xff"
    found = {(r.text, r.encoding) for r in text_runs(raw)}
    assert ("Vendor TEST", "latin-1") in found and ("Derinlik", "utf-16-le") in found


def test_candidates_fit_file_size():
    size = 1024 + 50 * (8 + 100 * 2)
    cands = candidates(size, n_traces=50)
    assert cands
    for c in cands:
        assert c.header_bytes + c.n_traces * c.stride == size
        for e in expand_trace_headers(c):
            assert e.header_bytes + e.n_traces * e.stride == size


@pytest.mark.parametrize(
    "header,trace_header,dtype",
    [(b"", 0, "<i2"), (b"GPR TEST FILE v1\nANT 400MHz\n".ljust(777, b"\x00"), 8, ">i2"), (b"H" * 512, 0, "<f4")],
    ids=["plain-i2", "text-header-trace-header-be", "float32"],
)
def test_sniff_recovers_layout_with_known_trace_count(tmp_path, header, trace_header, dtype):
    data = synthetic_profile()
    p = tmp_path / "unknown.dat"
    write_unknown(p, data, header, trace_header, dtype)
    _, best = sniff(p, n_traces=120, top=3)
    top = best[0]
    assert (top.dtype, top.n_samples, top.header_bytes, top.trace_header_bytes) == (dtype, 256, len(header), trace_header)


def test_sniff_without_trace_count(tmp_path):
    data = synthetic_profile()
    p = tmp_path / "unknown.dat"
    header = np.random.default_rng(5).integers(0, 256, 1024, dtype=np.uint8).tobytes()
    write_unknown(p, data, header, 0, "<i2")
    _, best = sniff(p, top=3)
    assert (best[0].dtype, best[0].n_samples, best[0].header_bytes) == ("<i2", 256, 1024)


def test_to_radargram_and_cli(tmp_path, capsys):
    data = synthetic_profile(n_samples=64, n_traces=30)
    p = tmp_path / "u.bin"
    write_unknown(p, data, b"", 0, "<i2")
    r = to_radargram(p, Candidate(0, 0, "<i2", 64, 30), dt_ns=0.25)
    np.testing.assert_array_equal(r.data, data.astype("<i2"))
    main([str(p), "--traces", "30", "--top", "2", "--plot", str(tmp_path / "c.png")])
    assert "Best layouts" in capsys.readouterr().out and (tmp_path / "c.png").exists()

import numpy as np
import pytest

from gpr_analyzer import Radargram
from gpr_analyzer.io import SUPPORTED_EXTENSIONS, read, read_file, write_segy


def test_dispatch_by_extension_case_insensitive(tmp_path):
    r = Radargram(np.ones((8, 3)), dt_ns=0.1)
    write_segy(r, tmp_path / "LINE.SGY")
    (back,) = read(tmp_path / "LINE.SGY")
    assert back.data.shape == (8, 3)
    assert read_file is read


def test_unknown_extension(tmp_path):
    p = tmp_path / "x.subscan"
    p.write_bytes(b"\x00" * 10)
    with pytest.raises(ValueError, match="unsupported"):
        read(p)


def test_supported_list():
    assert {".iprb", ".dzt", ".rd3", ".dt1", ".sgy"} <= set(SUPPORTED_EXTENSIONS)

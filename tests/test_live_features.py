import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from live_features import _ret, _z


def test_return_is_percentage():
    assert _ret(110.0, 100.0) == 10.0


def test_z_requires_history():
    assert _z(1.0, [1.0, 1.1]) is None


def test_z_detects_outlier():
    value = _z(10.0, [1, 2, 3, 4, 5, 6])
    assert value is not None and value > 2

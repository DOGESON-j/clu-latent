import pytest

from clu_latent.timecode import ms_to_timecode


@pytest.mark.parametrize(
    ("ms", "expected"),
    [
        (0, "00:00.000"),
        (1, "00:00.001"),
        (999, "00:00.999"),
        (1000, "00:01.000"),
        (61000, "01:01.000"),
        (3_600_000, "60:00.000"),
        (12345, "00:12.345"),
    ],
)
def test_ms_to_timecode(ms, expected):
    assert ms_to_timecode(ms) == expected


def test_ms_to_timecode_rejects_negative():
    with pytest.raises(ValueError):
        ms_to_timecode(-1)


def test_ms_to_timecode_rejects_non_int():
    with pytest.raises(TypeError):
        ms_to_timecode(1.5)  # type: ignore[arg-type]

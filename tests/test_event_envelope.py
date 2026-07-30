import pytest
from pydantic import ValidationError

from clu_latent.event import EventEnvelope, Producer


def _valid_kwargs(**overrides):
    kwargs = dict(
        id="kf_000000",
        type="keyframe",
        t_start_ms=0,
        t_end_ms=1000,
        producer=Producer(name="ffmpeg", version="6.1.1"),
        confidence=None,
        payload={"path": "media/keyframes/000000.jpg"},
    )
    kwargs.update(overrides)
    return kwargs


def test_valid_event_envelope_roundtrips_through_json():
    event = EventEnvelope(**_valid_kwargs())
    dumped = event.model_dump(mode="json")
    restored = EventEnvelope.model_validate(dumped)
    assert restored == event


def test_t_end_before_t_start_is_rejected():
    with pytest.raises(ValidationError):
        EventEnvelope(**_valid_kwargs(t_start_ms=1000, t_end_ms=500))


def test_negative_t_start_ms_is_rejected():
    with pytest.raises(ValidationError):
        EventEnvelope(**_valid_kwargs(t_start_ms=-1))


def test_confidence_out_of_range_is_rejected():
    with pytest.raises(ValidationError):
        EventEnvelope(**_valid_kwargs(confidence=1.5))


def test_confidence_in_range_is_accepted():
    event = EventEnvelope(**_valid_kwargs(confidence=0.87))
    assert event.confidence == 0.87


def test_confidence_none_is_accepted():
    event = EventEnvelope(**_valid_kwargs(confidence=None))
    assert event.confidence is None


def test_extra_fields_are_rejected():
    kwargs = _valid_kwargs()
    kwargs["unexpected_field"] = "nope"
    with pytest.raises(ValidationError):
        EventEnvelope(**kwargs)


def test_default_payload_is_empty_dict():
    kwargs = _valid_kwargs()
    del kwargs["payload"]
    event = EventEnvelope(**kwargs)
    assert event.payload == {}


def test_t_start_equals_t_end_is_valid_zero_length_event():
    event = EventEnvelope(**_valid_kwargs(t_start_ms=500, t_end_ms=500))
    assert event.t_start_ms == event.t_end_ms == 500

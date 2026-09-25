from app.core.ids import generate_event_id


def test_generate_event_id_is_unique():
    ids = {generate_event_id() for _ in range(1000)}
    assert len(ids) == 1000


def test_generate_event_id_is_26_chars():
    assert len(generate_event_id()) == 26


def test_generate_event_id_timestamp_prefix_is_non_decreasing():
    # The first 10 chars of a ULID encode the millisecond timestamp, which
    # must never move backwards across sequential calls (the random suffix
    # is not guaranteed to be monotonic within the same millisecond).
    first = generate_event_id()
    second = generate_event_id()
    assert first[:10] <= second[:10]

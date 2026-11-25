"""ate/sequence/runner.py: the retry policy.

    whole message lost or unreadable  -> ask again once (all channels)
    one channel damaged               -> re-read only that channel
    impossible value                  -> recorded, never retried
    temperature won't settle          -> one more full settling attempt

All random faults are off; each fault is forced on purpose through the bench.
"""

from __future__ import annotations

from tests.helpers import channel, load_data, raw_rows, rows_at, run_with


def test_one_lost_reply_is_retried_and_recovers(tmp_path):
    result, _ = run_with(tmp_path, lambda b: b.force_fault("opm_a", "timeout"))
    first = rows_at(result, 1)
    assert [r["status"] for r in first if r["attempt"] == "1"] == ["TIMEOUT"] * 8
    assert [r["status"] for r in first if r["attempt"] == "2"] == ["VALID"] * 8
    assert result.overall_result == "PASS"  # recovered, and nothing hidden
    assert channel(result, 1).retries_used == 1


def test_two_lost_replies_in_a_row_fail_every_channel(tmp_path):
    result, _ = run_with(tmp_path, lambda b: b.force_fault("opm_a", "timeout"),
                         lambda b: b.force_fault("opm_a", "timeout"))
    assert {r["status"] for r in rows_at(result, 1)} == {"TIMEOUT"}
    assert all(c.result == "FAIL" and c.timeout_count == 1 for c in result.summary.conditions[0].channels)


def test_unreadable_reply_is_kept_and_retried(tmp_path):
    result, _ = run_with(tmp_path, lambda b: b.force_fault("opm_a", "garbled"))
    attempt1 = [r for r in rows_at(result, 1) if r["attempt"] == "1"]
    assert {r["status"] for r in attempt1} == {"GARBLED"}
    assert all(r["raw_response"] for r in attempt1)  # the damaged text is preserved
    assert result.overall_result == "PASS"


def test_one_damaged_channel_is_re_read_alone(tmp_path):
    result, _ = run_with(tmp_path, lambda b: b.force_fault("opm_a", "channel_garbled", 3))
    first = rows_at(result, 1)
    retried = [r for r in first if r["attempt"] == "2"]
    assert [r["channel"] for r in retried] == ["3"]  # only channel 3 asked again
    assert retried[0]["status"] == "VALID" and retried[0]["note"] == "re-read of single channel"
    assert len([r for r in first if r["attempt"] == "1"]) == 8  # the other 7 kept their first reading
    assert result.overall_result == "PASS"


def test_impossible_value_is_recorded_not_retried(tmp_path):
    result, _ = run_with(tmp_path, lambda b: b.force_fault("opm_a", "channel_invalid", 3))
    ch3 = [r for r in rows_at(result, 1) if r["channel"] == "3"]
    assert len(ch3) == 1 and ch3[0]["status"] == "INVALID" and ch3[0]["raw_response"]
    assert channel(result, 3).result == "FAIL"
    assert all(channel(result, n).result == "PASS" for n in (1, 2, 4, 5, 6, 7, 8))


def test_per_channel_mode_retries_the_same_way(tmp_path):
    result, _ = run_with(tmp_path, lambda b: b.force_fault("opm_a", "timeout"),
                         sequence__measurement__read_mode="per_channel")
    first = [r for r in rows_at(result, 1) if r["channel"] == "1"]
    assert [(r["attempt"], r["status"]) for r in first] == [("1", "TIMEOUT"), ("2", "VALID")]
    assert result.overall_result == "PASS"


def test_temperature_timeout_is_retried(tmp_path):
    result, _ = run_with(tmp_path, lambda b: b.force_fault("tec_main", "timeout"))
    assert [r["status"] for r in rows_at(result, 1, phase="settle")] == ["TIMEOUT", "VALID"]
    assert result.overall_result == "PASS"


def test_invalid_temperature_reading_restarts_the_stability_window(tmp_path):
    baseline, _ = run_with(tmp_path)
    settle_polls = len([r for r in raw_rows(baseline) if r["phase"] == "settle"])

    def sensor_glitch_near_the_end(bench):
        tec = bench.simulator("tec_main")
        original = tec._read

        def read():
            reply = original()
            return type(reply)("ERR:SENSOR_FAULT") if tec.measurement_queries == settle_polls - 3 else reply
        tec._read = read

    result, _ = run_with(tmp_path, sensor_glitch_near_the_end)
    settle = [r for r in raw_rows(result) if r["phase"] == "settle"]
    assert "INVALID" in {r["status"] for r in settle}
    assert len(settle) > settle_polls  # had to wait for a fresh stable window
    assert result.summary.conditions[0].temp_result == "PASS"


def test_temperature_gets_one_more_settling_attempt(tmp_path):
    result, _ = run_with(tmp_path, data=load_data("examples/tec_wont_settle.yaml"))
    settle = [r for r in raw_rows(result) if r["phase"] == "settle"]
    assert {r["settle_attempt"] for r in settle} == {"1", "2"}
    assert result.summary.conditions[0].tecs[0].settle_attempts == 2

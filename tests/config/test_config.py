"""ate/config.py: every config rule produces a clear error before anything starts."""

from __future__ import annotations

import pytest

from ate.config import ConfigError, load_config, parse_config
from tests.helpers import CONFIG_DIR, load_data


def problems(data: dict, **overrides) -> str:
    with pytest.raises(ConfigError) as info:
        parse_config(data, overrides={k.replace("__", "."): v for k, v in overrides.items()} or None)
    return str(info.value)


def test_default_config_matches_the_brief():
    config = load_config(CONFIG_DIR / "default.yaml")
    assert config.sequence.conditions[0].setpoint_c == 40.0
    assert config.sequence.temperature.tolerance_c == 0.5
    assert config.sequence.measurement.readings_per_channel == 50
    assert (config.limits.avg_power_dbm.min, config.limits.avg_power_dbm.max) == (-3.0, 3.0)
    assert config.limits.require_all_valid
    assert config.simulation.device.channels == 8
    assert config.channel_map() == {ch: ("opm_a", ch) for ch in range(1, 9)}


def test_each_instrument_gets_its_own_settings_model():
    config = load_config(CONFIG_DIR / "default.yaml")
    tec, meter = config.instruments
    assert type(tec).__module__ == "ate.instruments.tec.settings"
    assert type(meter).__module__ == "ate.instruments.opm.settings"
    assert [t.name for t in config.tecs()] == ["tec_main"] and [m.name for m in config.meters()] == ["opm_a"]


@pytest.mark.parametrize("name", sorted(p.name for p in (CONFIG_DIR / "examples").glob("*.yaml")))
def test_every_example_config_is_valid(name):
    load_config(CONFIG_DIR / "examples" / name)


def test_bench_listens_where_the_tester_connects_unless_told_otherwise():
    sim = load_config(CONFIG_DIR / "default.yaml").simulation
    assert sim.listen_host is None and sim.bind_host == sim.host == sim.probe_host == "127.0.0.1"


def test_extends_overrides_only_what_differs():
    config = load_config(CONFIG_DIR / "examples" / "two_temperatures.yaml")
    assert [c.label for c in config.sequence.conditions] == ["25C", "40C"]
    assert config.sequence.measurement.readings_per_channel == 50  # inherited


def test_saved_config_loads_back_identically(tmp_path):
    from ate.config import save_config
    config = load_config(CONFIG_DIR / "examples" / "two_meters_16ch.yaml")
    save_config(config, tmp_path / "copy.yaml")
    assert load_config(tmp_path / "copy.yaml") == config


def test_limits_must_be_ordered():
    assert "must be below max" in problems(load_data(), limits__avg_power_dbm={"min": 3, "max": -3})


def test_allowed_bad_readings_must_be_fewer_than_the_readings_taken():
    assert "max_bad_readings (50) must be less than readings_per_channel (50)" in problems(
        load_data(), limits__max_bad_readings=50)
    parse_config(load_data(), overrides={"limits.max_bad_readings": 50, "limits.require_all_valid": False})


def test_offsets_must_match_channel_count():
    assert "channel_offsets_db has 2 values" in problems(load_data(), simulation__device__channel_offsets_db=[0, 0])


def test_interval_cannot_be_shorter_than_averaging():
    assert "before the last one finished" in problems(
        load_data(), sequence__measurement__interval_ms=50, sequence__measurement__averaging_time_ms=100)


def test_timeout_must_exceed_averaging_time():
    message = problems(load_data(), sequence__measurement__averaging_time_ms=2000,
                       sequence__measurement__interval_ms=2000)
    assert "every reading would time out" in message


def test_stable_window_must_fit_in_max_wait():
    assert "could never pass" in problems(load_data(), sequence__temperature__stable_for_s=200)


def test_time_scale_must_be_between_0_and_1():
    assert "simulation.time_scale" in problems(load_data(), simulation__time_scale=-1)
    assert "simulation.time_scale" in problems(load_data(), simulation__time_scale=2)


def test_fault_rate_must_be_a_probability():
    data = load_data()
    data["instruments"][1]["simulation"]["faults"]["timeout_rate"] = 1.5
    message = problems(data)
    assert "opm_a" in message and "timeout_rate" in message


def test_unknown_setting_is_rejected_not_ignored():
    data = load_data()
    data["sequence"]["measurement"]["readings_per_chanel"] = 10  # typo
    assert "readings_per_chanel" in problems(data)


def test_unknown_instrument_setting_is_rejected():
    data = load_data()
    data["instruments"][0]["simulation"]["jiter_c"] = 0.1  # typo, inside an instrument's own settings
    assert "jiter_c" in problems(data)


def test_duplicate_instrument_names():
    data = load_data()
    data["instruments"][1]["name"] = "tec_main"
    assert "both named 'tec_main'" in problems(data)


def test_unknown_instrument_type_lists_known_types():
    data = load_data()
    data["instruments"][0]["type"] = "laser_9000"
    message = problems(data)
    assert "unknown type 'laser_9000'" in message and "sim_tec" in message


def test_channel_connected_to_two_meters():
    data = load_data()
    data["instruments"].append({"name": "opm_b", "type": "sim_opm_8ch", "device_channels": [8]})
    assert "device channel 8 is connected to more than one meter (opm_a, opm_b)" in problems(data)


def test_channel_connected_to_no_meter():
    data = load_data()
    data["instruments"][1]["device_channels"] = [1, 2, 3, 4, 5, 6, 7]
    assert "device channel 8 is not connected to any power meter" in problems(data)


def test_meter_cannot_have_more_channels_than_inputs():
    data = load_data()
    data["instruments"][1]["device_channels"] = list(range(1, 10))
    assert "only has 8 inputs" in problems(data)


def test_real_hardware_needs_real_addresses():
    assert "needs a real address" in problems(load_data(), simulation__enabled=False)


def test_needs_a_tec_and_a_meter():
    data = load_data()
    data["instruments"] = [data["instruments"][1]]
    assert "no temperature controller" in problems(data)


def test_condition_names_must_be_unique():
    assert "unique names" in problems(load_data(), sequence__conditions=[{"setpoint_c": 40}, {"setpoint_c": 40}])


def test_missing_file():
    with pytest.raises(ConfigError, match="not found"):
        load_config(CONFIG_DIR / "nope.yaml")

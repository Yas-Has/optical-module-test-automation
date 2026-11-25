"""ate/core/registry.py: instruments are found automatically, one folder each."""

from __future__ import annotations

import textwrap

import pytest

import ate.instruments
from ate.config import ConfigError
from ate.core import registry
from ate.core.roles import ROLE_POWER_METER, ROLE_TEMPERATURE, PowerMeter, TemperatureController
from ate.core.settings import InstrumentConfigBase


def test_finds_the_built_in_instruments():
    assert registry.known_types() == ["sim_opm_8ch", "sim_tec"]
    assert registry.get("sim_tec").role == ROLE_TEMPERATURE
    assert registry.get("sim_opm_8ch").role == ROLE_POWER_METER


@pytest.mark.parametrize("name", registry.known_types())
def test_every_instrument_folder_is_complete(name):
    entry = registry.get(name)
    assert issubclass(entry.config_model, InstrumentConfigBase)
    assert issubclass(entry.driver, (TemperatureController, PowerMeter))  # fills a role the sequence knows
    assert entry.simulator is not None
    assert entry.config_model.model_fields["type"].default == name  # config `type:` matches its registry name


def test_unknown_type_lists_the_known_ones():
    with pytest.raises(ConfigError, match="Known types: sim_opm_8ch, sim_tec"):
        registry.get("laser_9000")


def test_two_instruments_cannot_share_a_name(monkeypatch):
    monkeypatch.setattr(registry, "_TYPES", dict(registry._TYPES))
    clash = registry.InstrumentType("sim_tec", InstrumentConfigBase, TemperatureController)
    with pytest.raises(ValueError, match="both call themselves 'sim_tec'"):
        registry.register(clash)


def test_a_new_instrument_folder_is_picked_up_with_no_other_changes(tmp_path, monkeypatch):
    # Drop a new folder next to tec/ and opm/ ...
    folder = tmp_path / "third_instrument"
    folder.mkdir()
    (folder / "__init__.py").write_text(textwrap.dedent("""
        from ate.core.registry import InstrumentType
        from ate.instruments.tec import TecDriver, TecSettings, TecSimulator

        INSTRUMENT_TYPES = [InstrumentType("third_instrument", TecSettings, TecDriver, TecSimulator)]
    """))
    monkeypatch.setattr(ate.instruments, "__path__", [*ate.instruments.__path__, str(tmp_path)])
    monkeypatch.setattr(registry, "_TYPES", {})
    monkeypatch.setattr(registry, "_discovered", False)
    # ... and it is found, alongside the existing ones.
    assert registry.known_types() == ["sim_opm_8ch", "sim_tec", "third_instrument"]

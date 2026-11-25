"""Temperature controller (TEC): sets and monitors the device temperature."""

from ate.core.registry import InstrumentType
from ate.instruments.tec.driver import TecDriver
from ate.instruments.tec.settings import TecSettings
from ate.instruments.tec.simulator import TecSimulator

INSTRUMENT_TYPES = [
    InstrumentType(name="sim_tec", config_model=TecSettings, driver=TecDriver, simulator=TecSimulator),
]

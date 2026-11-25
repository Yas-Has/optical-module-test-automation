"""Optical power meter (OPM), 8 inputs: measures optical power on every channel at once."""

from ate.core.registry import InstrumentType
from ate.instruments.opm.driver import OpmDriver
from ate.instruments.opm.settings import OpmSettings
from ate.instruments.opm.simulator import OpmSimulator

INSTRUMENT_TYPES = [
    InstrumentType(name="sim_opm_8ch", config_model=OpmSettings, driver=OpmDriver, simulator=OpmSimulator),
]

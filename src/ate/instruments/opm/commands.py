"""The meter's command vocabulary. Used by both the driver and the simulator, so they cannot drift apart."""

from ate.core.instrument import IDENTIFY


class OpmCommands:
    IDENTIFY = IDENTIFY
    RESET = "*RST"
    UNIT = "UNIT"  # UNIT DBM
    AVERAGING = "AVG"  # AVG <milliseconds>
    AVERAGING_QUERY = "AVG?"
    READ_ONE = "POW?"  # POW? <input>          -> "-0.12"
    READ_ALL = "POW:ALL?"  # every input at once -> "1:-0.12,2:0.03,..."

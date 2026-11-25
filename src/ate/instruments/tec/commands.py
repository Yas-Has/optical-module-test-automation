"""The TEC's command vocabulary. Used by both the driver and the simulator, so they cannot drift apart."""

from ate.core.instrument import IDENTIFY


class TecCommands:
    IDENTIFY = IDENTIFY
    RESET = "*RST"
    OUTPUT = "OUTP"  # OUTP ON | OUTP OFF
    OUTPUT_QUERY = "OUTP?"
    SET_TEMPERATURE = "TEMP"  # TEMP <celsius>
    SETPOINT_QUERY = "TEMP:SET?"
    READ_TEMPERATURE = "TEMP?"

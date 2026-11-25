"""One folder per instrument. Each folder contains:

    commands.py    the instrument's command words (shared by driver and simulator)
    settings.py    its config settings
    driver.py      talks to it (real or simulated, it can't tell the difference)
    simulator.py   pretends to be it
    __init__.py    declares INSTRUMENT_TYPES so the registry finds it automatically

To add an instrument, copy a folder and change those five files. Nothing else in the program changes.
"""

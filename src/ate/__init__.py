"""Modular test automation system for an 8-channel optical module.

Layers (each only talks to the one below it):

    config      -> load + validate all settings
    sequence    -> the test recipe (settle, verify, measure, retry, clean up)
    instruments -> drivers that speak each instrument's command language
    data        -> streaming raw recorder, analysis, reports
    simulation  -> the simulated test bench (pretend hardware)
"""

__version__ = "0.1.0"

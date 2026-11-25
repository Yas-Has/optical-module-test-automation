"""Shared machinery that every instrument uses. Knows about no specific instrument.

    settings.py    settings every instrument has (name, address, timeouts, retries)
    instrument.py  the common instrument contract + how a reply is judged
    roles.py       what *any* temperature controller / power meter must be able to do
    transport.py   send one line of text, get one line back
    registry.py    finds every instrument folder under ate/instruments/ automatically
"""

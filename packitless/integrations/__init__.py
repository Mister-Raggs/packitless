"""Adapters for dropping packitless into existing applications.

Each adapter is deliberately thin. The library's job is to turn a payload and
a budget into text; an adapter's job is to hand it the payload in whatever
shape the host application already has, and hand back a string the host can
interpolate into the prompt it already wrote. If an adapter needs more than a
few dozen lines, the library interface is wrong.
"""

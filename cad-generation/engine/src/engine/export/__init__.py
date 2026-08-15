"""Exporters: RobotIR -> robot interop formats.

The IR is canonical (§1). Everything in this package is a one-way projection
of it, and nothing here is ever read back as a source of truth. An exporter
that cannot represent something in the IR must say so, not approximate it
quietly.
"""

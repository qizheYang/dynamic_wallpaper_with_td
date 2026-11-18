"""Offline asset pipeline: versioned job -> PNG sequence -> validated video bundle.

Standard library only, and Python 3.11 compatible, so the TouchDesigner-side
controller can import the job and attempt helpers from TD's embedded Python.
"""

PIPELINE_VERSION = "0.1.0"

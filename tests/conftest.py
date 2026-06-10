"""Test fixtures for the radiacode_ble protocol layer.

protocol.py is pure stdlib, but importing it through the package would
pull in ``custom_components.radiacode.__init__`` (homeassistant) and
``radiacode_ble.__init__`` (bleak).  Loading the module directly from
its file path keeps the test environment dependency-free.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

_PROTOCOL_PATH = (
    Path(__file__).resolve().parent.parent
    / "custom_components"
    / "radiacode"
    / "radiacode_ble"
    / "protocol.py"
)


@pytest.fixture(scope="session")
def protocol():
    """Load and return the protocol module."""
    spec = importlib.util.spec_from_file_location("radiacode_protocol", _PROTOCOL_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["radiacode_protocol"] = module
    spec.loader.exec_module(module)
    return module

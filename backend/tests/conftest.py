"""Test defaults.

Agent ranking is disabled for the suite unless a test opts in: the SDK spawns
a CLI subprocess, so leaving it on would make every research-step test slow and
dependent on ambient credentials. The ranking logic itself is tested directly
in test_m5.py.
"""
import os

import pytest


@pytest.fixture(autouse=True)
def _disable_agent_ranking(monkeypatch):
    monkeypatch.setenv("CARMATCH_AGENT_RANKING", "off")

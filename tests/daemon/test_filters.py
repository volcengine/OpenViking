"""Tests for LowValueFilter."""
from openviking.daemon.filters import LowValueFilter


def test_filter_short_content():
    f = LowValueFilter()
    events = [
        {"content": "Short"},
        {"content": "This is a longer meaningful conversation about architecture"},
    ]
    filtered = f.apply(events)
    assert len(filtered) == 1


def test_filter_noise_patterns():
    f = LowValueFilter()
    events = [
        {"content": "npm install lodash --save"},
        {"content": "git commit -m 'fix bug'"},
        {"content": "Let's discuss the architecture design pattern for the new module"},
    ]
    filtered = f.apply(events)
    assert len(filtered) == 1
    assert "architecture" in filtered[0]["content"]


def test_filter_pip_install():
    f = LowValueFilter()
    events = [
        {"content": "pip install requests library for HTTP calls"},
    ]
    filtered = f.apply(events)
    assert len(filtered) == 0


def test_preserves_valid_content():
    f = LowValueFilter()
    events = [
        {"content": "We decided to use PostgreSQL instead of MySQL for better JSON support"},
        {"content": "The memory leak was caused by unclosed database connections"},
    ]
    filtered = f.apply(events)
    assert len(filtered) == 2


def test_empty_content_filtered():
    f = LowValueFilter()
    events = [
        {"content": ""},
        {"content": "   "},
    ]
    filtered = f.apply(events)
    assert len(filtered) == 0

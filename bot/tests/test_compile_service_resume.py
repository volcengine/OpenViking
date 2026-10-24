"""Compile lifecycle must not expose a resume path without an implementation.

`vikingbot.compile.resume` never shipped, yet `_run_pipeline` used to accept a
``resume`` flag whose only effect was a deferred ``ModuleNotFoundError`` deep
inside task execution (issue #6369). These tests pin the contract that a resume
entry point may only return once a real implementation exists.
"""

import inspect

from vikingbot.compile import service


def test_run_pipeline_exposes_no_resume_parameter():
    parameters = inspect.signature(service.BotCompileService._run_pipeline).parameters
    assert "resume" not in parameters


def test_service_module_has_no_dangling_resume_import():
    assert "vikingbot.compile.resume" not in inspect.getsource(service)

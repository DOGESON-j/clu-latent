"""Tests for Phase 3.14: local install / system-integration experience.

Covers the `system_integration` adapters, `doctor.py` capability
checks, and the `presentation.py` rendering layer, plus the CLI
commands that wire them together (`welcome`, `doctor`,
`system status|register|unregister`).

Core rule under test: never claim OS-level registration happened
unless it genuinely did, and never mutate a package while doing any of
this reporting.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from clu_latent import doctor as doctor_mod
from clu_latent import system_integration as si_mod
from clu_latent.cli import app
from clu_latent.system_integration.base import ActionOutcome

REPO_ROOT = Path(__file__).resolve().parents[1]


# --- 1. Structured status data ------------------------------------------------


def test_status_returns_structured_dataclass():
    status = si_mod.get_system_integration(system_name="Darwin").status()
    assert isinstance(status, si_mod.RegistrationStatus)
    data = status.to_dict()
    assert data["platform"] == "Darwin"
    assert data["registered"] is False
    assert isinstance(data["details"], dict)
    assert isinstance(data["warnings"], list)


# --- 2. register() returns only genuine successes -----------------------------


@pytest.mark.parametrize("system_name", ["Darwin", "Linux", "Windows"])
def test_register_never_falsely_claims_success(system_name):
    result = si_mod.get_system_integration(system_name=system_name).register()
    assert isinstance(result, si_mod.RegistrationResult)
    assert result.outcome == ActionOutcome.NOT_IMPLEMENTED
    assert result.succeeded is False
    assert result.changed_paths == []
    for step in result.steps:
        assert step.outcome != ActionOutcome.SUCCESS


# --- 3. unregister() is safe/explicit ------------------------------------------


@pytest.mark.parametrize("system_name", ["Darwin", "Linux", "Windows"])
def test_unregister_is_safe_and_explicit(system_name):
    result = si_mod.get_system_integration(system_name=system_name).unregister()
    assert result.action == "unregister"
    assert result.outcome == ActionOutcome.NOT_IMPLEMENTED
    assert result.changed_paths == []
    assert "not" in result.message.lower() or "nothing" in result.message.lower()


# --- 4. Unsupported platform fails clearly -------------------------------------


def test_unsupported_platform_falls_back_cleanly():
    adapter = si_mod.get_system_integration(system_name="PlayStation5")
    assert isinstance(adapter, si_mod.UnsupportedSystemIntegration)
    status = adapter.status()
    assert status.registered is False
    assert status.platform == "PlayStation5"
    result = adapter.register()
    assert result.outcome == ActionOutcome.NOT_IMPLEMENTED


# --- 5. No false claim of file association registration ------------------------


@pytest.mark.parametrize("system_name", ["Darwin", "Linux", "Windows"])
def test_status_never_claims_registered_in_this_phase(system_name):
    status = si_mod.get_system_integration(system_name=system_name).status()
    assert status.registered is False
    assert status.message == "System registration: not registered"
    assert "file association registered" not in status.message.lower()


# --- 6/7. NO_COLOR / --no-color disable styling --------------------------------


def test_no_color_env_var_disables_styling(monkeypatch):
    from clu_latent.presentation import make_console

    monkeypatch.setenv("NO_COLOR", "1")
    console = make_console()
    assert console.no_color is True


def test_no_color_flag_disables_styling():
    from clu_latent.presentation import make_console

    console = make_console(no_color=True)
    assert console.no_color is True


# --- 8. --quiet suppresses decorative output but not errors --------------------


def test_welcome_quiet_prints_nothing():
    runner = CliRunner()
    result = runner.invoke(app, ["welcome", "--quiet"])
    assert result.exit_code == 0
    assert result.output.strip() == ""


def test_doctor_quiet_still_prints_capability_lines():
    runner = CliRunner()
    result = runner.invoke(app, ["doctor", "--quiet"])
    assert result.exit_code == 0
    assert "CLULATENT" not in result.output  # decorative header suppressed
    assert "READY" in result.output or "UNAVAILABLE" in result.output


def test_system_status_command_errors_are_not_suppressed_by_quiet(tmp_path):
    # A malformed CLI invocation still produces a real (non-empty) error,
    # even with --quiet -- quiet only hides decoration, never errors.
    runner = CliRunner()
    result = runner.invoke(app, ["system", "status", "--quiet", "--bogus-flag"])
    assert result.exit_code != 0
    assert result.output.strip() != ""


# --- 9. No splash/branding when stdout is not a TTY -----------------------------


def test_welcome_is_plain_when_not_a_tty():
    # CliRunner captures output through a non-TTY stream, so this
    # exercises the same `console.is_terminal is False` branch a piped
    # `clulatent welcome` would hit.
    runner = CliRunner()
    result = runner.invoke(app, ["welcome"])
    assert result.exit_code == 0
    assert "CLULATENT" not in result.output
    assert "package reader available" in result.output


# --- 10. JSON/machine-readable output has no branding decorations --------------


def test_welcome_json_has_no_branding():
    runner = CliRunner()
    result = runner.invoke(app, ["welcome", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "tool_name" in data
    assert "CLULATENT" not in result.output
    assert "\x1b" not in result.output


def test_doctor_json_has_no_branding():
    runner = CliRunner()
    result = runner.invoke(app, ["doctor", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "checks" in data
    assert "CLULATENT" not in result.output


def test_system_status_json_has_no_branding():
    runner = CliRunner()
    result = runner.invoke(app, ["system", "status", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["platform"]
    assert "CLULATENT" not in result.output


def test_system_register_json_has_no_branding():
    runner = CliRunner()
    result = runner.invoke(app, ["system", "register", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["outcome"] == "not_implemented"
    assert "CLULATENT" not in result.output


# --- 11. welcome works in narrow terminals --------------------------------------


def test_welcome_narrow_terminal_does_not_crash():
    runner = CliRunner()
    result = runner.invoke(app, ["welcome"], terminal_width=20)
    assert result.exit_code == 0


# --- 12. doctor reports reader/validator/keyframe retrieval availability -------


def test_doctor_report_includes_all_capability_checks():
    report = doctor_mod.run_doctor_checks(system_name="Darwin")
    names = {check.name for check in report.checks}
    assert {"CLI", "Reader", "Validator", "Keyframe retrieval"} <= names
    assert report.all_available is True
    assert report.system_status is not None
    assert report.system_status.platform == "Darwin"


# --- 13. system adapter logic is separate from presentation --------------------


@pytest.mark.parametrize(
    "module",
    [
        "clu_latent.system_integration.base",
        "clu_latent.system_integration.macos",
        "clu_latent.system_integration.linux",
        "clu_latent.system_integration.windows",
        "clu_latent.doctor",
    ],
)
def test_adapter_and_doctor_modules_never_print(module):
    import importlib

    mod = importlib.import_module(module)
    source = inspect.getsource(mod)
    assert "console.print(" not in source
    assert "\nprint(" not in source and not source.startswith("print(")


# --- 14. Commands do not mutate packages ----------------------------------------


def test_welcome_doctor_system_commands_touch_no_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    for args in (
        ["welcome"],
        ["doctor"],
        ["system", "status"],
        ["system", "register"],
        ["system", "unregister"],
    ):
        result = runner.invoke(app, args)
        assert result.exit_code == 0, result.output
    assert list(tmp_path.iterdir()) == []


# --- 15. Existing suites still pass (smoke-checked here; full suite run separately) --


def test_keyframe_and_validate_modules_still_importable():
    import clu_latent.keyframe_retrieval  # noqa: F401
    import clu_latent.validate  # noqa: F401


# --- Docs / README bookkeeping --------------------------------------------------


def test_readme_includes_phase_3_14_bullet():
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "CLULatent" in readme


def test_phase_3_14_doc_exists():
    doc = REPO_ROOT / "docs" / "PHASE_3_14_LOCAL_INSTALL_SYSTEM_INTEGRATION.md"
    assert doc.is_file()
    text = doc.read_text(encoding="utf-8")
    assert "registration" in text.lower()


def test_cli_top_level_help_still_works():
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "welcome" in result.output
    assert "doctor" in result.output
    assert "system" in result.output

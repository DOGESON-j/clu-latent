"""Presentation layer for welcome, doctor, and system-integration output.

Structured data from `doctor.py` and `system_integration` is rendered
*here* -- adapter and capability-check code never prints anything
itself (see `system_integration/base.py`). Keeping rendering in one
place is what lets every presentation rule below be enforced
consistently instead of re-implemented ad hoc per command:

- Respect `NO_COLOR` (Rich's `Console` already honours the env var by
  itself) and `--no-color` (forces it regardless of the environment).
- Skip decorative branding entirely when stdout is not a TTY
  (`console.is_terminal`) -- a piped/redirected `welcome` prints one
  plain status line instead of the panel.
- `--quiet` suppresses decorative banner/header lines but never hides
  the actual information or an error.
- Output is always bounded: a handful of fixed lines, never a loop
  over unbounded package data (this module never touches a package).
- `print_json` never goes through Rich at all, so JSON output can never
  carry a color code, markup, or the ASCII banner -- it is always
  exactly `json.dumps(...)`.
"""

from __future__ import annotations

import json
from typing import Any

from rich.console import Console

from .constants import PACKAGE_SUFFIX, TOOL_NAME
from .doctor import CapabilityCheck, DoctorReport
from .security.console import safe_console_text
from .system_integration import ActionOutcome, RegistrationResult, RegistrationStatus

_SIGNAL_BAR_WIDTH = 39
_NAME_COLUMN_WIDTH = 20


def make_console(*, no_color: bool = False) -> Console:
    """A fresh `Console` for one command invocation.

    Always constructed fresh (never a shared module-level console) so
    `NO_COLOR` / `--no-color` and the current stdout are re-evaluated
    every call.
    """
    return Console(no_color=True) if no_color else Console()


def print_json(data: dict[str, Any]) -> None:
    """Print `data` as plain, unstyled JSON -- no branding, no ANSI, ever."""
    print(json.dumps(data, indent=2, sort_keys=True))


def _outcome_marker(outcome: ActionOutcome) -> str:
    return {
        ActionOutcome.SUCCESS: "[green]done[/green]",
        ActionOutcome.FAILURE: "[red]failed[/red]",
        ActionOutcome.SKIPPED: "[dim]skipped[/dim]",
        ActionOutcome.NOT_IMPLEMENTED: "[yellow]not implemented[/yellow]",
    }[outcome]


def _pick(checks: list[CapabilityCheck], name: str) -> CapabilityCheck:
    for check in checks:
        if check.name == name:
            return check
    return CapabilityCheck(name, False, "not checked")


def render_welcome(console: Console, checks: list[CapabilityCheck], *, quiet: bool) -> None:
    """Render the `clulatent welcome` panel.

    Canonical, minimal, and safe in a narrow terminal: a fixed set of
    short lines, no per-package data, no unbounded loop. Skips the
    ASCII panel entirely (one plain line instead) when stdout is not a
    TTY, and prints nothing at all when `quiet` is set (this command is
    purely decorative/informational).
    """
    if quiet:
        return

    if not console.is_terminal:
        console.print(
            f"{TOOL_NAME}: local-first media evidence packages; package reader available."
        )
        console.print("First run:")
        console.print("  clulatent build-video INPUT -o demo.clulatent --profile v1")
        console.print("  clulatent open demo.clulatent")
        console.print("Demo: clulatent demo --no-browser")
        console.print("Docs: https://github.com/DOGESON-j/clu-latent/tree/main/docs")
        console.print("Compatibility certifies package structure, not media meaning.")
        return

    reader = _pick(checks, "Reader")
    validator = _pick(checks, "Validator")
    cli_check = _pick(checks, "CLI")

    console.print("[bold cyan]CLULATENT[/bold cyan] // [magenta]LOCAL SUPPORT[/magenta]")
    console.print("")
    bar = "\u2500" * _SIGNAL_BAR_WIDTH
    console.print(f"[cyan]SIGNAL[/cyan]  {bar}[cyan]\u25cf[/cyan]  [green]ONLINE[/green]")
    console.print("")
    for check in (reader, validator, cli_check):
        state = "[green]READY[/green]" if check.available else "[red]UNAVAILABLE[/red]"
        console.print(f"  \u25c7 {safe_console_text(check.name):<14} {state}")
    console.print(f"  \u25c7 {'File type':<14} {PACKAGE_SUFFIX}")
    console.print("")
    console.print("Next:")
    console.print("  clulatent build-video INPUT -o demo.clulatent --profile v1")
    console.print("  clulatent open demo.clulatent")
    console.print("  clulatent demo --no-browser")
    console.print("Docs: https://github.com/DOGESON-j/clu-latent/tree/main/docs")
    console.print("Safety: compatibility certifies package structure, not media meaning.")


def render_doctor(console: Console, report: DoctorReport, *, quiet: bool) -> None:
    """Render the `clulatent doctor` report.

    `quiet` suppresses only the decorative header -- every capability
    line and any "unavailable" warning always prints, since those are
    information/errors, not decoration.
    """
    if not quiet:
        console.print("[bold cyan]CLULATENT[/bold cyan] // [magenta]DOCTOR[/magenta]")
        console.print("")

    for check in report.checks:
        state = {
            "PASS": "[green]PASS[/green] (READY)",
            "WARNING": "[yellow]WARNING[/yellow]",
            "FAIL": "[red]FAIL[/red]",
        }.get(check.level, "[red]FAIL[/red]")
        console.print(
            f"  \u25c7 {safe_console_text(check.name):<{_NAME_COLUMN_WIDTH}} {state}  "
            f"{safe_console_text(check.detail)}"
        )

    status = report.system_status
    if status is not None:
        label = (
            "[green]System registration: registered[/green]"
            if status.registered
            else "System registration: not registered"
        )
        console.print(f"  \u25c7 {'System integration':<{_NAME_COLUMN_WIDTH}} {label}")

    if not report.all_available:
        console.print("")
        console.print(
            "[yellow]One or more capabilities are unavailable in this "
            "environment.[/yellow]"
        )


def render_registration_status(console: Console, status: RegistrationStatus, *, quiet: bool) -> None:
    """Render `clulatent system status`."""
    if not quiet:
        console.print("[bold cyan]CLULATENT[/bold cyan] // [magenta]SYSTEM STATUS[/magenta]")
        console.print("")

    if status.registered:
        console.print("[green]System registration: registered[/green]")
    else:
        console.print("System registration: not registered")
    console.print(f"  platform: {safe_console_text(status.platform)}")
    console.print(f"  {safe_console_text(status.message)}")
    for key, value in status.details.items():
        console.print(f"  {safe_console_text(key)}: {safe_console_text(value)}")
    for warning in status.warnings:
        console.print(f"  [yellow]warning:[/yellow] {safe_console_text(warning)}")


def render_registration_result(console: Console, result: RegistrationResult, *, quiet: bool) -> None:
    """Render `clulatent system register` / `clulatent system unregister`.

    Every step is shown with its genuine outcome (`done`, `failed`,
    `skipped`, or `not implemented`) -- never collapsed into a blanket
    "success" line.
    """
    if not quiet:
        header = "REGISTER" if result.action == "register" else "UNREGISTER"
        console.print(f"[bold cyan]CLULATENT[/bold cyan] // [magenta]SYSTEM {header}[/magenta]")
        console.print("")

    console.print(safe_console_text(result.message))
    for step in result.steps:
        marker = _outcome_marker(step.outcome)
        console.print(f"  \u25c7 {safe_console_text(step.name):<28} {marker}  {safe_console_text(step.message)}")
    for path in result.changed_paths:
        console.print(f"  changed: {safe_console_text(path)}")
    for warning in result.warnings:
        console.print(f"  [yellow]warning:[/yellow] {safe_console_text(warning)}")

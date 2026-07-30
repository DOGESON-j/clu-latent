# Phase 3.14 — Local Install and System Integration Experience

## Summary

Every prior phase made CLULatent packages more capable. Phase 3.14
makes the *tool itself* feel installed and present on a machine: a
small, text-based welcome/status surface (`clulatent welcome`,
`clulatent doctor`) plus honest, structured reporting of whether the
OS knows about `.clulatent` files (`clulatent system status` /
`register` / `unregister`).

This is **not** the interactive inspector, a dashboard, an analysis
UI, or a full-screen TUI. It communicates one thing, quickly: *this
machine can read, validate, and inspect CLULatent media packages* —
and, separately and honestly, whether OS-level file-type registration
has actually happened.

## Core rule

> **Do not claim something happened that did not happen.**

`register()`/`unregister()` only ever report a step as done if it
genuinely completed. `status()` only ever says "registered" once an
adapter genuinely confirms it. This phase ships with real OS-level
`.clulatent` file-type registration (macOS Launch Services, Linux
`xdg-mime`, the Windows Registry) **not implemented** — so every
platform, honestly, reports "not registered" / "not implemented;
manual registration required" rather than a placeholder success. That
is the correct answer for this phase, not a stub to be embarrassed
about.

## What it does

### `system_integration` package (`src/clu_latent/system_integration/`)

A structured, presentation-free adapter layer:

- `SystemIntegration` (ABC) — `register()`, `unregister()`, `status()`,
  each returning a dataclass, never printing anything itself.
- `RegistrationStatus` — `platform`, `registered`, `message`, `details`,
  `warnings`.
- `RegistrationResult` — `action`, `platform`, `outcome`
  (`success` / `failure` / `skipped` / `not_implemented`), `message`,
  granular `steps` (each with its own `outcome`), `changed_paths`,
  `warnings`.
- `ManualRegistrationIntegration` — the shared, honest implementation
  used by `macos.py`, `linux.py`, and `windows.py`: each only sets
  `platform_name` and the OS `mechanism` name a *future* phase would
  use; `register()`/`unregister()` report `NOT_IMPLEMENTED` and change
  nothing; `status()` always reports `registered=False`.
- `UnsupportedSystemIntegration` — fallback for an unrecognized
  platform name; behaves identically, never crashes.
- `get_system_integration(system_name=None)` — resolves the adapter for
  `platform.system()` (or an explicit name, for deterministic tests).

### `doctor.py`

`run_doctor_checks()` runs four genuine probes — CLI availability,
`manifest.py` importable ("Reader"), `validate.py` importable
("Validator"), `keyframe_retrieval.py` importable ("Keyframe
retrieval") — plus the current platform's `status()`. Each check is a
real import, not a hardcoded `True`; a broken/partial install reports
`available=False` with the import error, not a silent assumption.

### `presentation.py`

The only place anything in this phase calls `console.print` or
`print`. Renders:

- `render_welcome` — the ASCII "LOCAL SUPPORT" panel; falls back to one
  plain status line when `console.is_terminal` is `False` (piped
  output); prints nothing under `--quiet`.
- `render_doctor` — every capability line plus system-integration
  status; `--quiet` hides only the decorative header.
- `render_registration_status` / `render_registration_result` — render
  `system status` / `system register` / `system unregister`, always
  showing the genuine `outcome` of every step (`done`, `failed`,
  `skipped`, `not implemented`) instead of collapsing to one blanket
  "success" line.
- `print_json` — bypasses Rich entirely (`json.dumps` straight to
  `print`), so JSON output can never carry a color code, markup, or the
  ASCII banner.
- `make_console(no_color=...)` — always builds a *fresh* `Console` (Rich
  honours `NO_COLOR` on its own); `--no-color` forces it regardless of
  the environment.

### CLI commands

```
clulatent welcome [--no-color] [--quiet] [--json]
clulatent doctor  [--no-color] [--quiet] [--json]
clulatent system status     [--no-color] [--quiet] [--json]
clulatent system register   [--no-color] [--quiet] [--json]
clulatent system unregister [--no-color] [--quiet] [--json]
```

`welcome` and `doctor` are read-only capability reports; the `system`
group is the explicit, user-initiated OS-integration surface. None of
these commands ever touch a `.clulatent` package.

## What it deliberately does NOT do

- No interactive inspector, dashboard, analysis UI, or full-screen TUI.
- No visual AI, no semantic-network visualization, no fake graphs, no
  clocks, no excessive animation.
- No renaming of `.clulatent` to `.clu`; no `.clu` alias required.
- No real OS-level file-type registration performed automatically —
  every platform honestly reports "not implemented" instead.
- No network access; no package mutation (no manifest/track/receipt/
  index/lock writes).
- No reliance on pip post-install hooks (unreliable with modern pip);
  the branded entry points are simply the commands above, run
  explicitly by the user.

## Safety / presentation properties

- Adapter and `doctor.py` logic never print — verified by tests that
  scan their source for `print(`/`console.print(`.
- `NO_COLOR` and `--no-color` both disable Rich styling
  (`Console(no_color=True)` / Rich's own `NO_COLOR` handling).
- `--quiet` suppresses only decorative banner/header lines; capability
  lines, status, and errors always print.
- Non-TTY stdout (piped/redirected `welcome`) prints one plain line
  instead of the ASCII panel.
- `--json` output is plain `json.dumps` — no ANSI, no banner, safe for
  machine consumption.
- Every value taken from package data (there is none touched here, but
  including platform/detail strings for symmetry) is passed through
  `safe_console_text` before printing.

## Canonical wording

- `"CLULatent package reader available"` / `".clulatent support
  available through the CLI"` — the non-TTY `welcome` fallback line.
- `"System registration: not registered"` — the only status message in
  this phase (no platform has real registration yet).
- `"System registration: registered"` — reserved for a future phase
  once an adapter genuinely performs OS-level registration; never
  emitted today.

## Files

- `src/clu_latent/system_integration/{base,macos,linux,windows,__init__}.py`
  — adapter abstractions and per-platform (honest, not-yet-implemented)
  behaviour.
- `src/clu_latent/doctor.py` — capability checks.
- `src/clu_latent/presentation.py` — all rendering (Rich panels + JSON).
- `src/clu_latent/cli.py` — `welcome`, `doctor`, and the `system`
  sub-app (`status`, `register`, `unregister`).
- `tests/test_system_integration.py` — structured status/result data,
  honest register/unregister, unsupported-platform handling, no false
  "registered" claim, `NO_COLOR`/`--no-color`/`--quiet` behaviour,
  non-TTY and JSON output shape, doctor capability coverage, adapter/
  presentation separation, no package mutation.

## Trust position

Adapters produce evidence, not truth; generated does not mean
canonical. Extended here to system integration: a status report is
only as trustworthy as its willingness to say "not registered." This
phase never lies about what the OS has been told.

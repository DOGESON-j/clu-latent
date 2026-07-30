"""Terminal-injection-safe rendering of untrusted package strings.

Any string that originates from a `.clulatent` package we did not
create ourselves -- manifest fields, track payload values, filenames,
error text that echoes package content back -- must pass through
`safe_console_text` before being handed to Rich's `console.print`.
Otherwise package data could alter terminal formatting via Rich
markup, or emit raw ANSI/control character sequences to the user's
terminal.
"""

from __future__ import annotations

import re

from rich.markup import escape as _escape_rich_markup

# CSI sequences ("\x1b[...m"), OSC sequences ("\x1b]...\x07" or terminated
# by ST "\x1b\\"), and other single/two-character escape sequences.
_ANSI_ESCAPE_RE = re.compile(
    r"""
    \x1b
    (?:
        \[ [0-?]* [ -/]* [@-~]   # CSI sequence
      | \] .*? (?:\x07|\x1b\\)     # OSC sequence, terminated by BEL or ST
      | [@-Z\\-_]                  # single-character (Fe) escape sequence
    )
    """,
    re.VERBOSE,
)

# Control characters other than tab (\x09) and newline (\x0a), which are
# left intact since they're normal whitespace in rendered text. \r is
# stripped along with the rest -- it can be used to overwrite/hide
# previously printed terminal content.
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def safe_console_text(value: object) -> str:
    """Return `value` as text that is safe to pass to `console.print`.

    - Converts non-string values via `str()`.
    - Strips ANSI escape sequences (CSI/OSC/Fe).
    - Strips control characters other than tab/newline.
    - Escapes Rich markup, so `[bold red]...[/]`-style sequences in
      untrusted data render as literal text instead of being interpreted.
    """
    text = value if isinstance(value, str) else str(value)
    text = _ANSI_ESCAPE_RE.sub("", text)
    text = _CONTROL_CHAR_RE.sub("", text)
    return _escape_rich_markup(text)

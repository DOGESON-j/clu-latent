from __future__ import annotations

from io import StringIO

from rich.console import Console

from clu_latent.security.console import safe_console_text


def test_rich_markup_is_escaped_not_interpreted():
    # The literal bracketed text is expected to survive (that's the safe,
    # non-interpreted rendering) -- what must never happen is Rich actually
    # applying bold/red styling (which would emit ANSI SGR codes) for it.
    result = safe_console_text("[bold red]INJECTED[/bold red]")

    buf = StringIO()
    console = Console(file=buf, force_terminal=True, color_system="standard", width=200)
    # highlight=False isolates the property under test (did the injected
    # markup get interpreted?) from Rich's unrelated default repr
    # highlighting of punctuation/numbers in plain printed text.
    console.print(result, highlight=False)
    rendered = buf.getvalue()

    assert "INJECTED" in rendered
    assert "\x1b[" not in rendered


def test_ansi_escape_sequences_are_stripped():
    payload = "before\x1b[31mred\x1b[0mafter"
    result = safe_console_text(payload)
    assert "\x1b" not in result
    assert result == "beforeredafter"


def test_control_characters_stripped_but_tab_and_newline_preserved():
    payload = "a\x00b\x07c\x1fd\te\nf\rg"
    result = safe_console_text(payload)
    assert "\x00" not in result
    assert "\x07" not in result
    assert "\x1f" not in result
    assert "\r" not in result
    assert "\t" in result
    assert "\n" in result


def test_non_string_values_are_stringified():
    assert safe_console_text(123) == "123"
    assert safe_console_text(None) == "None"


def test_osc_sequence_stripped():
    # OSC 8 hyperlink injection, terminated by BEL.
    payload = "\x1b]8;;http://evil.example\x07click\x1b]8;;\x07"
    result = safe_console_text(payload)
    assert "\x1b" not in result
    assert "click" in result

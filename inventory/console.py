"""Console helpers for CLI output routing."""

from __future__ import annotations

from typing import Any, NoReturn

import typer
from rich.console import Console

_stdout = Console()
_stderr = Console(stderr=True)


class HumanConsole:
    """Route human-facing output without corrupting JSON stdout.

    Normal command output belongs on stdout, but when ``--json`` is active any
    Rich-formatted status text must move to stderr so stdout remains parseable.
    Errors and warnings always go to stderr.
    """

    def print(self, *objects: Any, **kwargs: Any) -> None:
        from .main import state

        target = _stderr if state.json_output or _is_diagnostic(objects) else _stdout
        target.print(*objects, **kwargs)

    def print_json(self, *args: Any, **kwargs: Any) -> None:
        _stdout.print_json(*args, **kwargs)


def print_error(message: str) -> None:
    """Emit an error in the active output mode."""
    from .main import state

    if state.json_output:
        _stdout.print_json(data={"error": message})
    else:
        console.print(f"[red]Error:[/red] {message}")


def print_warning(message: str) -> None:
    """Emit a warning in the active output mode."""
    from .main import state

    if state.json_output:
        _stdout.print_json(data={"status": "warning", "message": message})
    else:
        console.print(f"[yellow]Warning:[/yellow] {message}")


def abort(message: str, code: int = 1) -> NoReturn:
    """Print a local validation error and stop command execution."""
    print_error(message)
    raise typer.Exit(code)


def confirm(text: str, *, default: bool | None = False) -> bool:
    """Prompt without corrupting JSON stdout."""
    from .main import state

    return typer.confirm(text, default=default, err=state.json_output)


def _is_diagnostic(objects: tuple[Any, ...]) -> bool:
    if not objects:
        return False
    first = str(objects[0])
    return first.startswith(("[red]Error:", "[yellow]Warning:", "[yellow]Aborted."))


console = HumanConsole()
out = _stdout

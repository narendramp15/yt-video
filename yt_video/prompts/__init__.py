"""Prompt templates (``*.md``) with ``# system`` and ``# prompt`` sections."""
from __future__ import annotations

from importlib import resources
from string import Template


def render_prompt(name: str, **values: str) -> tuple[str, str]:
    """Return ``(system, prompt)`` for template ``name``.

    Substitution uses ``string.Template.substitute``: a missing key raises KeyError.
    """
    text = (resources.files(__package__) / f"{name}.md").read_text(encoding="utf-8")
    head, sep, body = text.partition("\n# prompt\n")
    if not sep or not head.startswith("# system\n"):
        raise ValueError(f"prompt template {name!r} needs '# system' and '# prompt' sections")
    system = head[len("# system\n"):].strip()
    return Template(system).substitute(**values), Template(body.strip()).substitute(**values)

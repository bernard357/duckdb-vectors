"""Load the versioned enrichment prompts.

A prompt file has a front matter block (``prompt``, ``version``, ``inputs``,
``sees_queries``, ...), a ``## System`` section and a ``## User`` section that
holds ``{placeholders}``. The version is part of the file name, so a derived
dataset can record exactly which prompt produced it.
"""

import re
from pathlib import Path

import yaml
from pydantic import BaseModel

PROMPTS = Path(__file__).resolve().parent / "prompts"
_PLACEHOLDER = re.compile(r"(?<!\{)\{([a-z_]+)\}(?!\})")


class Prompt(BaseModel):
    """One versioned prompt."""

    name: str
    version: int
    inputs: list[str]
    sees_queries: bool
    sees_tags: bool = True
    system: str
    user: str

    def placeholders(self) -> set[str]:
        """Names of the ``{placeholders}`` in the user section."""
        return set(_PLACEHOLDER.findall(self.user))

    def render(self, **values: str) -> str:
        """Fill the user section; every declared input must be given."""
        missing = set(self.inputs) - set(values)
        if missing:
            raise ValueError(f"missing inputs for {self.name}: {sorted(missing)}")
        return _PLACEHOLDER.sub(lambda m: str(values[m.group(1)]), self.user)


def load_prompt(name: str, version: int = 1, directory: Path = PROMPTS) -> Prompt:
    """Read ``{name}.v{version}.md``."""
    text = (directory / f"{name}.v{version}.md").read_text(encoding="utf-8")
    _, front, body = text.split("---\n", 2)
    meta = yaml.safe_load(front)
    system, _, user = body.split("## System", 1)[1].partition("## User")
    return Prompt(
        name=meta["prompt"],
        version=meta["version"],
        inputs=meta["inputs"],
        sees_queries=meta["sees_queries"],
        sees_tags=meta.get("sees_tags", True),
        system=system.strip(),
        user=user.strip(),
    )

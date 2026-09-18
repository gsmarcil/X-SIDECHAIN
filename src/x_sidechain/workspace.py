from __future__ import annotations

import re
from pathlib import Path


SAFE_AGENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def validate_agent_id(agent_id: str) -> None:
    if not SAFE_AGENT_ID.fullmatch(agent_id):
        raise ValueError(
            "agent id must be 1-64 ASCII letters, digits, dots, underscores, or hyphens"
        )


def restrict(path: Path, mode: int) -> None:
    """Tighten local permissions, tolerating filesystems that do not support it."""
    try:
        path.chmod(mode)
    except OSError:
        pass


class SessionWorkspace:
    """File-backed per-agent workspaces with private local permissions."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        # mkdir(mode=...) applies to the leaf only, so tighten the session directory
        # that parents=True created with the process umask.
        restrict(self.root.parent, 0o700)
        restrict(self.root, 0o700)

    def write(self, revision: int, agent_id: str, relative_path: str, content: str) -> Path:
        validate_agent_id(agent_id)
        relative = Path(relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("workspace path must remain inside the agent directory")
        revision_root = self.root / f"revision-{revision}"
        agents_root = revision_root / "agents"
        agent_root = agents_root / agent_id
        target = agent_root / relative
        if target.is_symlink() or target.parent.is_symlink():
            raise ValueError("workspace path must not traverse a symlink")
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        for directory in (revision_root, agents_root, agent_root, target.parent):
            restrict(directory, 0o700)
        target.write_text(content.rstrip() + "\n", encoding="utf-8")
        restrict(target, 0o600)
        return target

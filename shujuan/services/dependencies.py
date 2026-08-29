from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class RuntimeDeps:
    source: Mapping[str, Any]

    def require(self, *names: str) -> dict[str, Any]:
        missing = [name for name in names if name not in self.source]
        if missing:
            raise RuntimeError(f"dependency boundary is missing: {', '.join(missing)}")
        return {name: self.source[name] for name in names}


def require_dependencies(source: Mapping[str, Any], names: tuple[str, ...], *, label: str) -> dict[str, Any]:
    missing = [name for name in names if name not in source]
    if missing:
        raise RuntimeError(f"{label} boundary is missing: {', '.join(missing)}")
    return {name: source[name] for name in names}


__all__ = ["RuntimeDeps", "require_dependencies"]

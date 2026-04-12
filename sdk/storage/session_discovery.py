from __future__ import annotations

from pathlib import Path


def looks_like_session_dir(path: str | Path) -> bool:
    path = Path(path)
    if not path.is_dir():
        return False
    if (path / "meta.json").exists():
        return True
    if not (path / "manifest.json").exists():
        return False
    return (path / "streams").is_dir() or (path / "aligned").is_dir() or (path / "trajectory").is_dir()


def discover_session_dirs(root: str | Path) -> list[Path]:
    root = Path(root)
    if not root.exists():
        return []
    if looks_like_session_dir(root):
        return [root]

    candidates: set[Path] = set()
    for name in ("meta.json", "manifest.json"):
        for match in root.rglob(name):
            parent = match.parent
            if looks_like_session_dir(parent):
                candidates.add(parent)
    return sorted(candidates, key=lambda item: tuple(item.relative_to(root).parts))

"""AC 4 / Invariant 7: the SDK-isolation seam.

No module under the core layer (core/, git/, and later store/projection/cache)
may import ``mcp`` or ``starlette``. Only the adapter layer
(server_factory, server, middleware, cli) is allowed to touch the SDK.
"""

import ast
from pathlib import Path

import dev_helper_mcp

PACKAGE_ROOT = Path(dev_helper_mcp.__file__).parent

# Core-layer locations to police. Packages: scanned recursively. Single modules
# (store/projection/cache) appear in later stories — included now so the seam is
# guarded the moment they exist.
SEAM_PACKAGES = ["core", "git"]
SEAM_MODULES = ["store.py", "projection.py", "cache.py"]

FORBIDDEN_ROOTS = {"mcp", "starlette"}


def _seam_files() -> list[Path]:
    files: list[Path] = []
    for pkg in SEAM_PACKAGES:
        pkg_dir = PACKAGE_ROOT / pkg
        if pkg_dir.is_dir():
            files.extend(pkg_dir.rglob("*.py"))
    for mod in SEAM_MODULES:
        mod_path = PACKAGE_ROOT / mod
        if mod_path.is_file():
            files.append(mod_path)
    return files


def _imported_roots(source: str) -> set[str]:
    roots: set[str] = set()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                roots.add(node.module.split(".")[0])
    return roots


def test_core_layer_has_no_sdk_imports():
    files = _seam_files()
    # At minimum the two seam-anchor packages must exist in this story.
    assert files, "expected core/ and git/ seam anchors to exist"

    violations = []
    for path in files:
        roots = _imported_roots(path.read_text(encoding="utf-8"))
        leaked = roots & FORBIDDEN_ROOTS
        if leaked:
            violations.append(f"{path.relative_to(PACKAGE_ROOT)} imports {sorted(leaked)}")

    assert not violations, "SDK seam violated:\n" + "\n".join(violations)

"""Import-boundary check for the pdfacts repository.

Rules:
  engine-boundary  pypdfium2 only in backends/pdfium.py, pikepdf only in
                   backends/qpdf.py
  forbidden        MuPDF bindings are never imported by the library, its
                   tests or its tools; bench/ is not checked
  core-isolation   pdfacts.core does not import feature packages

The library is SOURCE_ROOT/pdfacts; tests/ and tools/ are next to SOURCE_ROOT.

Usage: python tools/check_architecture.py [SOURCE_ROOT]
Exit status is 1 when a rule is violated, 2 when a directory to check is
missing.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

ENGINE_OWNERS = {
    "pypdfium2": "backends/pdfium.py",
    "pypdfium2_raw": "backends/pdfium.py",
    "pikepdf": "backends/qpdf.py",
}
FORBIDDEN = {"fitz", "pymupdf", "mupdf"}
FEATURE_PACKAGES = {
    "text",
    "graphics",
    "annotations",
    "forms",
    "inspect",
    "edit",
    "render",
    "content",
}


@dataclass(frozen=True)
class Violation:
    rule: str
    path: str
    line: int
    module: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: [{self.rule}] imports {self.module}"


def _resolve(node: ast.ImportFrom, package_parts: tuple[str, ...]) -> str:
    """Absolute module name of a `from ... import` statement."""
    if node.level == 0:
        return node.module or ""
    base = package_parts[: len(package_parts) - (node.level - 1)]
    return ".".join((*base, node.module)) if node.module else ".".join(base)


def _imports(tree: ast.AST, package_parts: tuple[str, ...]):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom):
            module = _resolve(node, package_parts)
            yield node.lineno, module
            # `from pdfacts import text` names a feature package as an alias.
            for alias in node.names:
                yield node.lineno, f"{module}.{alias.name}" if module else alias.name


def find_violations(source_root: Path) -> list[Violation]:
    """Check every module under `source_root` (the directory holding `pdfacts/`)."""
    package_root = source_root / "pdfacts"
    violations: list[Violation] = []
    for path in sorted(package_root.rglob("*.py")):
        relative = path.relative_to(package_root).as_posix()
        module_parts = ("pdfacts", *path.relative_to(package_root).with_suffix("").parts)
        package_parts = module_parts[:-1]
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        seen: set[tuple[str, int, str]] = set()
        for line, module in _imports(tree, package_parts):
            top = module.split(".")[0]
            found: list[tuple[str, str]] = []
            if top in ENGINE_OWNERS and relative != ENGINE_OWNERS[top]:
                found.append(("engine-boundary", top))
            if top in FORBIDDEN:
                found.append(("forbidden", top))
            parts = module.split(".")
            if (
                module_parts[:2] == ("pdfacts", "core")
                and len(parts) >= 2
                and parts[0] == "pdfacts"
                and parts[1] in FEATURE_PACKAGES
            ):
                found.append(("core-isolation", ".".join(parts[:2])))
            for rule, name in found:
                key = (rule, line, name)
                if key not in seen:
                    seen.add(key)
                    violations.append(Violation(rule, relative, line, name))
    return violations


def find_forbidden(root: Path, directories: tuple[str, ...]) -> list[Violation]:
    """Imports of MuPDF bindings in the given directories below `root`.

    Paths in the result are relative to `root`. A directory that does not
    exist raises FileNotFoundError: a check that looked at nothing must not
    pass.
    """
    violations: list[Violation] = []
    for directory in directories:
        base = root / directory
        if not base.is_dir():
            raise FileNotFoundError(f"no directory {base}")
        for path in sorted(base.rglob("*.py")):
            relative = path.relative_to(root).as_posix()
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            seen: set[tuple[int, str]] = set()
            for line, module in _imports(tree, ()):
                top = module.split(".")[0]
                if top in FORBIDDEN and (line, top) not in seen:
                    seen.add((line, top))
                    violations.append(Violation("forbidden", relative, line, top))
    return violations


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parent.parent / "src"
    if not (root / "pdfacts").is_dir():
        print(f"no pdfacts package under {root}", file=sys.stderr)
        return 2
    repository = root.resolve().parent
    try:
        violations = find_violations(root) + find_forbidden(repository, ("tests", "tools"))
    except FileNotFoundError as error:
        print(error, file=sys.stderr)
        return 2
    for violation in violations:
        print(violation)
    print(f"{len(violations)} violation(s) in {root}, and in tests/ and tools/ of {repository}")
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

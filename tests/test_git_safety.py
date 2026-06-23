"""Enforce the "Git safety in tests" rule (project-context.md).

This tool spawns real git that creates branches and worktrees, so a test that
runs git against a path resolving to this working tree mutates *this* repo — the
incident that once destroyed branch ``master``. Two guards back the rule:

  1. Runtime: the autouse ``_guard_project_repo_untouched`` fixture in
     ``conftest.py`` asserts no test mutated the project's own repo (refs/HEAD
     unchanged) — mechanism-independent regression guard.
  2. Static (this module): every direct git invocation in the test tree must
     (a) pass ``-C <repo>`` so it targets an explicit repo and never defaults to
     CWD = the project repo, and (b) pass ``env=`` so inherited ``GIT_*`` can be
     stripped (left in place they redirect tmp-repo calls at the outer repo when
     pytest runs inside the pre-commit hook). ``os.system``/``os.popen`` shelling
     out to git is forbidden outright — it cannot carry ``-C``/``env`` safely.

Mirrors the AST-scan style of ``test_adapter_seam.py``.
"""

import ast
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent

# subprocess entry points that spawn a process from an argv list.
_SUBPROCESS_FUNCS = {"run", "Popen", "call", "check_call", "check_output"}


def _test_py_files() -> list[Path]:
    return [p for p in sorted(TESTS_DIR.rglob("*.py")) if "__pycache__" not in p.parts]


def _is_subprocess_call(call: ast.Call) -> bool:
    func = call.func
    if isinstance(func, ast.Attribute) and func.attr in _SUBPROCESS_FUNCS:
        return True  # subprocess.run(...) / sp.run(...)
    return isinstance(func, ast.Name) and func.id in _SUBPROCESS_FUNCS


def _git_command_list(call: ast.Call) -> ast.List | None:
    """Return the argv list node iff this call spawns ``git`` (``["git", ...]``)."""
    if not call.args:
        return None
    first = call.args[0]
    if not isinstance(first, ast.List) or not first.elts:
        return None
    head = first.elts[0]
    if isinstance(head, ast.Constant) and head.value == "git":
        return first
    return None


def _list_has_const(list_node: ast.List, value: str) -> bool:
    return any(isinstance(e, ast.Constant) and e.value == value for e in list_node.elts)


def _has_keyword(call: ast.Call, name: str) -> bool:
    return any(kw.arg == name for kw in call.keywords)


def _is_os_shell_call(call: ast.Call) -> bool:
    func = call.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr in {"system", "popen"}
        and isinstance(func.value, ast.Name)
        and func.value.id == "os"
    )


def _mentions_git(call: ast.Call) -> bool:
    return any(
        isinstance(n, ast.Constant) and isinstance(n.value, str) and "git" in n.value
        for n in ast.walk(call)
    )


def test_tests_never_run_git_against_the_project_repo():
    """No test git call may default to CWD or be redirected by inherited GIT_*."""
    violations: list[str] = []

    for path in _test_py_files():
        rel = path.relative_to(TESTS_DIR)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue

            if _is_os_shell_call(node) and _mentions_git(node):
                violations.append(
                    f"{rel}:{node.lineno}: os.system/os.popen invoking git is forbidden "
                    f"(cannot carry -C/env safely) — use the tmp_git_repo helper"
                )
                continue

            if not _is_subprocess_call(node):
                continue
            cmd = _git_command_list(node)
            if cmd is None:
                continue

            if not _list_has_const(cmd, "-C"):
                violations.append(
                    f"{rel}:{node.lineno}: git subprocess without '-C <repo>' — it would "
                    f"default to CWD = the project repo. Pass '-C' a tmp_path repo."
                )
            if not _has_keyword(node, "env"):
                violations.append(
                    f"{rel}:{node.lineno}: git subprocess without env= — inherited GIT_* "
                    f"would redirect it at the project repo under the pre-commit hook. "
                    f"Pass a GIT_*-stripped env (see conftest)."
                )

    assert not violations, (
        "Git-safety rule violated (project-context.md 'Git safety in tests'):\n"
        + "\n".join(violations)
    )

"""型別註解不得引用同一模組中尚未定義的名稱。

這條規則存在的理由是一次真實的事故：`GetFamilyTreeResponse.role_assignment`
的註解引用了 135 行之後才定義的 `FamilyRoleAssignmentStatus`。

開發機是 Python 3.14，PEP 649 把註解改成延後求值，整套測試 2582 全綠；CI 跑
3.12，註解在 class 建立的當下就求值，於是 30 個測試檔在 collection 階段
`NameError`——而錯誤訊息指向的是一堆與該模組毫無關係的測試檔，從訊息本身
看不出根因在哪。

這個測試以靜態分析取代「換一個 Python 版本跑跑看」，因此在**任何**版本上都
會失敗，不必等進了 CI 才知道。
"""

import ast
import builtins
import io
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BUILTIN_NAMES = frozenset(dir(builtins))


def _forward_references(path: Path) -> list[tuple[int, str, str]]:
    """回傳 (行號, 名稱, 位置) —— 註解引用了本模組稍後才定義的名稱。

    只檢查模組層與 class body 的註解。函式內的區域註解在每個版本都是延後求值
    的，不會有這個問題。
    """
    tree = ast.parse(io.open(path, encoding="utf-8").read())

    # `from __future__ import annotations` 會讓整份檔案的註解都變成字串，
    # 前向參照因此合法。
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            if any(alias.name == "annotations" for alias in node.names):
                return []

    def bound_names(node) -> set[str]:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            return {node.name}
        if isinstance(node, ast.Assign):
            return {t.id for t in node.targets if isinstance(t, ast.Name)}
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            return {node.target.id}
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            return {(a.asname or a.name).split(".")[0] for a in node.names}
        return set()

    # 先掃一遍拿到模組層的全部名稱，才分得出「稍後才定義」與「從別處匯入」。
    defined_somewhere: set[str] = set()
    for node in tree.body:
        defined_somewhere |= bound_names(node)

    problems: list[tuple[int, str, str]] = []
    defined_so_far: set[str] = set()

    def check(annotation, lineno: int, where: str) -> None:
        for name in {n.id for n in ast.walk(annotation) if isinstance(n, ast.Name)}:
            if name in BUILTIN_NAMES or name in defined_so_far:
                continue
            if name in defined_somewhere:
                problems.append((lineno, name, where))

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and stmt.annotation is not None:
                    field = getattr(stmt.target, "id", "?")
                    check(stmt.annotation, stmt.lineno, f"{node.name}.{field}")
        elif isinstance(node, ast.AnnAssign) and node.annotation is not None:
            check(node.annotation, node.lineno, "模組層")
        defined_so_far |= bound_names(node)

    return problems


def _python_sources() -> list[Path]:
    return sorted((PROJECT_ROOT / "app").rglob("*.py"))


def test_there_are_sources_to_check():
    """路徑寫錯時這個測試會空轉並永遠通過——先擋住那個情況。"""
    assert len(_python_sources()) > 20


@pytest.mark.parametrize(
    "source", _python_sources(), ids=lambda p: str(p.relative_to(PROJECT_ROOT))
)
def test_annotations_do_not_reference_names_defined_later(source: Path):
    problems = _forward_references(source)
    assert not problems, "\n".join(
        f"{source.relative_to(PROJECT_ROOT)}:{lineno} 的註解引用了稍後才定義的 "
        f"{name!r}（於 {where}）。請把定義移到使用處之前。"
        for lineno, name, where in problems
    )

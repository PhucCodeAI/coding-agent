import ast
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def test_every_function_and_class_has_a_docstring():
    """Quy ước dự án: mọi hàm và class (kể cả private, hàm lồng, test) có docstring Google Style.

    Ruff chỉ kiểm hàm/class public, nên test này bắt phần còn lại.
    """
    missing = []
    for f in [*BACKEND.joinpath("app").rglob("*.py"), BACKEND / "test.py", *BACKEND.joinpath("tests").glob("*.py")]:
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) and ast.get_docstring(node) is None:
                missing.append(f"{f.relative_to(BACKEND)}:{node.lineno} {node.name}")
    assert not missing, "Thiếu docstring:\n" + "\n".join(missing)

"""Biến 1 file Python thành bản mô tả cấu trúc gọn (cho người và agent), không kèm thân hàm.

Mô tả đúng 4 thứ:
    1. imports            — import những gì, từ đâu (gộp theo module; phần chỉ để type-check được tách riêng)
    2. class              — lớp kế thừa gì, `__init__` nhận gì (dataclass: các field), thuộc tính, các method
    3. hàm cấp module     — chữ ký + dòng đầu docstring
    4. code lẻ            — các câu lệnh cấp module không phải import/class/def (hằng số, `if __name__...`, ...)

Định dạng văn bản (mặc định) — dùng ít token hơn JSON ~3–4 lần và ~5–10 lần so với đọc nguyên file:

    app/x.py · 120 dòng — Mô tả module
    imports:
      os, re
      pathlib: Path
      typing: Any
      [type-check] app.y: Z
    class Foo(Base) @dataclass L10-50 — Mô tả lớp
      init(a: int, b: str = 'x')
      attrs: count, name
      async run(x) -> int L20-30 — Mô tả
      @property total -> int L32
    def helper(a, b=1) -> str L55-60 — Mô tả
    code:
      L70 MAX = 5
      L72 if __name__ == '__main__': …

Dùng:
    from app.utils.py_outline import outline, analyze
    print(outline(path_or_source))              # văn bản
    data = analyze(source).to_dict()            # cấu trúc (JSON-able)
"""

from __future__ import annotations

import ast
from pathlib import Path

from app.dtos.outline import ClassInfo, FileOutline, FuncInfo, ImportGroup, LooseCode

_DEFAULT_MAX = 24
_LOOSE_MAX = 90


def _clip(text: str, n: int) -> str:
    """Gộp khoảng trắng và cắt còn `n` ký tự."""
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _doc1(node: ast.AST) -> str:
    """Dòng đầu của docstring."""
    d = ast.get_docstring(node)
    return _clip(d.strip().splitlines()[0], 100) if d and d.strip() else ""


def _un(node: ast.AST | None) -> str:
    """Mã nguồn của node AST (rỗng nếu None)."""
    return ast.unparse(node) if node is not None else ""


def _params(args: ast.arguments, drop_first: bool, types: bool) -> str:
    """Chữ ký tham số gọn: bỏ self/cls, giữ *, /, mặc định (cắt ngắn), type hint (tuỳ chọn)."""
    pos = [*args.posonlyargs, *args.args]
    defaults = [None] * (len(pos) - len(args.defaults)) + list(args.defaults)
    out: list[str] = []

    def one(a: ast.arg, d: ast.expr | None) -> str:
        """1 tham số dạng `tên: kiểu = mặc định`."""
        s = a.arg
        if types and a.annotation is not None:
            s += f": {_clip(_un(a.annotation), 40)}"
            if d is not None:
                s += f" = {_clip(_un(d), _DEFAULT_MAX)}"
        elif d is not None:
            s += f"={_clip(_un(d), _DEFAULT_MAX)}"
        return s

    for i, (a, d) in enumerate(zip(pos, defaults, strict=True)):
        if i == 0 and drop_first and a.arg in ("self", "cls"):
            continue
        out.append(one(a, d))
        if args.posonlyargs and i == len(args.posonlyargs) - 1:
            out.append("/")
    if args.vararg:
        out.append("*" + one(args.vararg, None))
    elif args.kwonlyargs:
        out.append("*")
    out += [one(a, d) for a, d in zip(args.kwonlyargs, args.kw_defaults, strict=True)]
    if args.kwarg:
        out.append("**" + one(args.kwarg, None))
    return ", ".join(out)


def _func(node: ast.FunctionDef | ast.AsyncFunctionDef, types: bool, in_class: bool) -> FuncInfo:
    """Thông tin 1 hàm từ node AST.

    Args:
        node: Node hàm.
        types: Giữ type hint.
        in_class: Là method (bỏ self/cls).

    Returns:
        FuncInfo.
    """
    decos = [_clip(_un(d), 30) for d in node.decorator_list]
    return FuncInfo(
        name=node.name,
        params=_params(node.args, in_class and "staticmethod" not in decos, types),
        returns=_clip(_un(node.returns), 40) if types and node.returns is not None else "",
        is_async=isinstance(node, ast.AsyncFunctionDef),
        decorators=decos,
        doc=_doc1(node),
        line=node.lineno,
        end_line=node.end_lineno or node.lineno,
    )


def _is_type_checking(test: ast.expr) -> bool:
    """Điều kiện `if` là `TYPE_CHECKING`."""
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING")


def _collect_imports(body: list[ast.stmt], type_only: bool, acc: dict[tuple[str, bool], list[str]]) -> None:
    """Gom import theo module vào `acc`.

    Args:
        body: Câu lệnh cấp module (hoặc trong `if TYPE_CHECKING:`).
        type_only: Đang ở trong `if TYPE_CHECKING:`.
        acc: (module, type_only) → tên import.
    """
    for st in body:
        if isinstance(st, ast.Import):
            acc.setdefault(("", type_only), []).extend(a.name + (f" as {a.asname}" if a.asname else "") for a in st.names)
        elif isinstance(st, ast.ImportFrom):
            mod = "." * st.level + (st.module or "")
            acc.setdefault((mod, type_only), []).extend(a.name + (f" as {a.asname}" if a.asname else "") for a in st.names)
        elif isinstance(st, ast.If) and _is_type_checking(st.test):
            _collect_imports(st.body, True, acc)
        elif isinstance(st, ast.Try):
            _collect_imports(st.body, type_only, acc)


def _class(node: ast.ClassDef, types: bool, private: bool) -> ClassInfo:
    """Thông tin 1 class từ node AST.

    Args:
        node: Node class.
        types: Giữ type hint.
        private: Giữ thành viên private.

    Returns:
        ClassInfo.
    """
    decos = [_clip(_un(d), 30) for d in node.decorator_list]
    info = ClassInfo(
        name=node.name,
        bases=[_clip(_un(b), 30) for b in node.bases],
        decorators=decos,
        doc=_doc1(node),
        line=node.lineno,
        end_line=node.end_lineno or node.lineno,
        init=None,
    )
    fields: list[str] = []
    for st in node.body:
        if isinstance(st, ast.FunctionDef | ast.AsyncFunctionDef):
            if not private and st.name.startswith("_") and st.name != "__init__" and not st.name.startswith("__"):
                continue
            f = _func(st, types, in_class=True)
            if st.name == "__init__":
                info.init = f.params
            else:
                info.methods.append(f)
        elif isinstance(st, ast.ClassDef):
            info.classes.append(_class(st, types, private))
        elif isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name):
            ann = _clip(_un(st.annotation), 30)
            default = f" = {_clip(_un(st.value), _DEFAULT_MAX)}" if st.value is not None else ""
            fields.append(f"{st.target.id}: {ann}{default}" if types else f"{st.target.id}{default.replace(' = ', '=')}")
        elif isinstance(st, ast.Assign):
            fields += [t.id for t in st.targets if isinstance(t, ast.Name)]
    if info.init is None:
        declared = [f for f in fields if (": " in f if types else True)]
        if declared and (any("dataclass" in d for d in decos) or any(b.split(".")[-1] in ("NamedTuple", "BaseModel", "TypedDict") for b in info.bases)):
            info.init = ", ".join(declared)
            fields = []
    info.attrs = fields
    return info


def _loose(st: ast.stmt) -> LooseCode:
    """Một dòng mô tả câu lệnh cấp module."""
    if isinstance(st, ast.If | ast.For | ast.While | ast.With | ast.AsyncWith | ast.Try):
        head = _un(st).splitlines()[0].rstrip(":")
        return LooseCode(st.lineno, _clip(head, _LOOSE_MAX) + ": …")
    return LooseCode(st.lineno, _clip(_un(st), _LOOSE_MAX))


def analyze(source: str, path: str = "<source>", *, types: bool = True, private: bool = True) -> FileOutline:
    """Phân tích mã nguồn Python thành `FileOutline` (không chạy code)."""
    n_lines = source.count("\n") + 1
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as e:
        return FileOutline(path, n_lines, "", [], [], [], [], error=f"SyntaxError dòng {e.lineno}: {e.msg}")
    acc: dict[tuple[str, bool], list[str]] = {}
    _collect_imports(tree.body, False, acc)
    imports = [ImportGroup(mod, names, to) for (mod, to), names in acc.items()]
    classes: list[ClassInfo] = []
    funcs: list[FuncInfo] = []
    loose: list[LooseCode] = []
    for st in tree.body:
        if isinstance(st, ast.Import | ast.ImportFrom):
            continue
        if isinstance(st, ast.If) and _is_type_checking(st.test):
            continue
        if isinstance(st, ast.Expr) and isinstance(st.value, ast.Constant) and isinstance(st.value.value, str):
            continue
        if isinstance(st, ast.ClassDef):
            classes.append(_class(st, types, private))
        elif isinstance(st, ast.FunctionDef | ast.AsyncFunctionDef):
            if private or not st.name.startswith("_"):
                funcs.append(_func(st, types, in_class=False))
        else:
            loose.append(_loose(st))
    return FileOutline(path, n_lines, _doc1(tree), imports, classes, funcs, loose)


def _fn_line(f: FuncInfo, prefix: str, docs: bool, lines: bool, kind: str = "") -> str:
    """1 dòng mô tả hàm (decorator, chữ ký, docstring, số dòng).

    Args:
        f: Hàm.
        prefix: Thụt lề + từ khóa đầu dòng.
        docs: Kèm dòng đầu docstring.
        lines: Kèm số dòng.
        kind: Nhãn thêm (VD "property").

    Returns:
        Dòng văn bản.
    """
    decos = " ".join(
        d
        for d in f.decorators
        if d.split(".")[-1] in ("property", "staticmethod", "classmethod", "abstractmethod", "cached_property", "contextmanager", "asynccontextmanager", "tool")
    )
    decos = " ".join("@" + d for d in decos.split()) if decos else ""
    head = ("async " if f.is_async else "") + (f"{kind} " if kind else "")
    s = f"{prefix}{decos + ' ' if decos else ''}{head}{f.name}({f.params})" + (f" -> {f.returns}" if f.returns else "")
    if lines:
        s += f" L{f.line}" + (f"-{f.end_line}" if f.end_line > f.line else "")
    if docs and f.doc:
        s += f" — {f.doc}"
    return s


def _class_lines(c: ClassInfo, ind: str, docs: bool, lines: bool) -> list[str]:
    """Các dòng mô tả 1 class (kèm method, class lồng).

    Args:
        c: Class.
        ind: Thụt lề.
        docs: Kèm dòng đầu docstring.
        lines: Kèm số dòng.

    Returns:
        Danh sách dòng.
    """
    head = f"{ind}class {c.name}" + (f"({', '.join(c.bases)})" if c.bases else "")
    head += "".join(f" @{d}" for d in c.decorators)
    if lines:
        head += f" L{c.line}-{c.end_line}"
    if docs and c.doc:
        head += f" — {c.doc}"
    out = [head]
    if c.init is not None:
        out.append(f"{ind}  init({c.init})")
    if c.attrs:
        out.append(f"{ind}  attrs: {', '.join(c.attrs)}")
    out += [_fn_line(m, f"{ind}  ", docs, lines) for m in c.methods]
    for sub in c.classes:
        out += _class_lines(sub, ind + "  ", docs, lines)
    return out


def render(o: FileOutline, *, docs: bool = True, lines: bool = True) -> str:
    """Văn bản gọn của `FileOutline` (định dạng ở docstring đầu file)."""
    head = f"{o.path} · {o.lines} dòng" + (f" — {o.doc}" if docs and o.doc else "")
    if o.error:
        return f"{head}\n⚠ {o.error}"
    out = [head]
    if o.imports:
        out.append("imports:")
        for g in o.imports:
            tag = "[type-check] " if g.type_only else ""
            out.append(f"  {tag}{g.module + ': ' if g.module else ''}{', '.join(g.names)}")
    for c in o.classes:
        out += _class_lines(c, "", docs, lines)
    out += [_fn_line(f, "", docs, lines, kind="def") for f in o.functions]
    if o.code:
        out.append("code:")
        out += [f"  L{c.line} {c.text}" if lines else f"  {c.text}" for c in o.code]
    return "\n".join(out)


def outline(
    source_or_path: str | Path,
    *,
    docs: bool = True,
    types: bool = True,
    lines: bool = True,
    private: bool = True,
) -> str:
    """Mô tả cấu trúc 1 file Python. Truyền đường dẫn file (Path / chuỗi tồn tại) hoặc mã nguồn.

    Args:
        source_or_path: đường dẫn file .py hoặc chính mã nguồn.
        docs: kèm dòng đầu docstring (module/class/hàm).
        types: kèm type hint của tham số và kiểu trả về.
        lines: kèm số dòng (Lx-y) để tra cứu nhanh.
        private: kèm hàm/method bắt đầu bằng `_` (trừ dunder luôn hiện).

    Returns:
        Văn bản cấu trúc; nếu file lỗi cú pháp thì có dòng ⚠ báo lỗi.
    """
    p = (
        Path(source_or_path)
        if isinstance(source_or_path, Path) or ("\n" not in str(source_or_path) and len(str(source_or_path)) < 400 and Path(str(source_or_path)).is_file())
        else None
    )
    if p is not None:
        o = analyze(p.read_text(encoding="utf-8"), str(p), types=types, private=private)
    else:
        o = analyze(str(source_or_path), types=types, private=private)
    return render(o, docs=docs, lines=lines)

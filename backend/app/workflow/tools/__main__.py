"""In danh mục công cụ theo nhóm: `python -m app.workflow.tools`."""

from app.workflow.tools import TOOLS

by_group: dict[str, list[str]] = {}
for t in TOOLS.values():
    by_group.setdefault(t.group, []).append(t.name)
for group, names in by_group.items():
    print(f"[{group}]")
    for n in names:
        t = TOOLS[n]
        print(f"  {t.usage}\n    {t.desc}\n    Dùng khi: {t.when}\n    Tham số: {t.args_doc or '(không có)'}")
print(f"\n{len(TOOLS)} công cụ.")

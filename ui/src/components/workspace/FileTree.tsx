import { ChevronDownIcon, ChevronRightIcon, FileIcon, FolderIcon } from "lucide-react";
import { useMemo, useState } from "react";

interface Node {
  name: string;
  path: string;
  isDir: boolean;
  children: Node[];
}

function build(files: string[]): Node[] {
  const root: Node = { name: "", path: "", isDir: true, children: [] };
  for (const f of files) {
    const parts = f.split("/");
    let node = root;
    parts.forEach((part, i) => {
      const isDir = i < parts.length - 1;
      let child = node.children.find((c) => c.name === part && c.isDir === isDir);
      if (!child) {
        child = { name: part, path: parts.slice(0, i + 1).join("/"), isDir, children: [] };
        node.children.push(child);
      }
      node = child;
    });
  }
  const sort = (nodes: Node[]) => {
    nodes.sort((a, b) => Number(b.isDir) - Number(a.isDir) || a.name.localeCompare(b.name));
    for (const n of nodes) sort(n.children);
  };
  sort(root.children);
  return root.children;
}

/** Cây file; file đang mở hiện nội dung (`renderOpen`) ngay bên dưới dòng của nó. */
export function FileTree({ files, open, changed, onToggle, renderOpen }: { files: string[]; open: string | null; changed: Set<string>; onToggle: (path: string) => void; renderOpen: (path: string) => React.ReactNode }) {
  const tree = useMemo(() => build(files), [files]);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const toggleDir = (p: string) => setCollapsed((s) => (s.has(p) ? new Set([...s].filter((x) => x !== p)) : new Set([...s, p])));
  const render = (nodes: Node[], depth: number): React.ReactNode =>
    nodes.map((n) =>
      n.isDir ? (
        <div key={n.path}>
          <button type="button" onClick={() => toggleDir(n.path)} className="flex w-full items-center gap-1.5 rounded-md py-1.5 pr-2 text-left text-[12.5px] text-zinc-400 hover:bg-[#1c1c20]" style={{ paddingLeft: depth * 14 + 8 }}>
            {collapsed.has(n.path) ? <ChevronRightIcon className="size-3.5 shrink-0 text-zinc-600" /> : <ChevronDownIcon className="size-3.5 shrink-0 text-zinc-600" />}
            <FolderIcon className="size-3.5 shrink-0 text-zinc-500" />
            <span className="truncate">{n.name}</span>
          </button>
          {!collapsed.has(n.path) && render(n.children, depth + 1)}
        </div>
      ) : (
        <div key={n.path} id={`ws-file-${n.path}`} className="scroll-mt-1">
          <button
            type="button"
            title={n.path}
            onClick={() => onToggle(n.path)}
            className={`flex w-full items-center gap-1.5 rounded-md py-1.5 pr-2 text-left font-mono text-[12.5px] ${open === n.path ? "bg-[#232328] text-zinc-100" : "text-zinc-300 hover:bg-[#1c1c20]"}`}
            style={{ paddingLeft: depth * 14 + 8 }}
          >
            {open === n.path ? <ChevronDownIcon className="size-3.5 shrink-0 text-zinc-500" /> : <ChevronRightIcon className="size-3.5 shrink-0 text-zinc-600" />}
            <FileIcon className="size-3.5 shrink-0 text-zinc-500" />
            <span className="truncate">{n.name}</span>
            {changed.has(n.path) && <span className="ml-auto size-1.5 shrink-0 rounded-full bg-amber-400" title="Thay đổi ở commit mới nhất" />}
          </button>
          {open === n.path && <div className="my-1.5" style={{ marginLeft: depth * 14 + 8 }}>{renderOpen(n.path)}</div>}
        </div>
      ),
    );
  return <div className="p-2">{tree.length ? render(tree, 0) : <div className="px-3 py-4 text-[12px] text-zinc-500">Workspace chưa có file.</div>}</div>;
}

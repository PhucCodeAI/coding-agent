interface Line {
  kind: "add" | "del" | "ctx" | "hunk";
  text: string;
  oldNo: number | null;
  newNo: number | null;
}

interface DiffFile {
  path: string;
  status: "added" | "deleted" | "modified";
  added: number;
  removed: number;
  lines: Line[];
}

function parse(diff: string): DiffFile[] {
  const out: DiffFile[] = [];
  let cur: DiffFile | null = null;
  let oldNo = 0;
  let newNo = 0;
  for (const raw of diff.split("\n")) {
    if (raw.startsWith("diff --git ")) {
      cur = { path: raw.match(/ b\/(.+)$/)?.[1] ?? raw, status: "modified", added: 0, removed: 0, lines: [] };
      out.push(cur);
    } else if (!cur || raw.startsWith("+++") || raw.startsWith("---") || raw.startsWith("index ")) {
      continue;
    } else if (raw.startsWith("new file mode")) cur.status = "added";
    else if (raw.startsWith("deleted file mode")) cur.status = "deleted";
    else if (raw.startsWith("@@")) {
      const m = raw.match(/@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/);
      oldNo = Number(m?.[1] ?? 0);
      newNo = Number(m?.[2] ?? 0);
      cur.lines.push({ kind: "hunk", text: raw, oldNo: null, newNo: null });
    } else if (raw.startsWith("+")) {
      cur.added++;
      cur.lines.push({ kind: "add", text: raw.slice(1), oldNo: null, newNo: newNo++ });
    } else if (raw.startsWith("-")) {
      cur.removed++;
      cur.lines.push({ kind: "del", text: raw.slice(1), oldNo: oldNo++, newNo: null });
    } else if (raw.startsWith(" ")) cur.lines.push({ kind: "ctx", text: raw.slice(1), oldNo: oldNo++, newNo: newNo++ });
  }
  return out;
}

const LABEL = { added: "mới", deleted: "đã xóa", modified: "sửa" } as const;
const MAX_FILES = 40;

export function DiffViewer({ diff }: { diff: string }) {
  const files = parse(diff);
  if (!files.length) return <div className="p-6 text-[12.5px] text-zinc-500">Không có thay đổi nội dung.</div>;
  return (
    <div className="space-y-4 p-3">
      {files.length > MAX_FILES && <div className="text-[11.5px] text-amber-400">Hiện {MAX_FILES}/{files.length} file thay đổi.</div>}
      {files.slice(0, MAX_FILES).map((f) => (
        <div key={f.path} className="overflow-hidden rounded-lg border border-[#27272a]">
          <div className="flex items-center gap-2 border-[#27272a] border-b bg-[#18181b] px-3 py-1.5 text-[12px]">
            <span className="truncate font-mono text-zinc-200">{f.path}</span>
            <span className="rounded bg-[#232328] px-1.5 text-[10.5px] text-zinc-400">{LABEL[f.status]}</span>
            <span className="ml-auto font-mono text-[11px]">
              <span className="text-emerald-400">+{f.added}</span> <span className="text-rose-400">−{f.removed}</span>
            </span>
          </div>
          {!f.lines.length && <div className="px-3 py-2 text-[11.5px] text-zinc-500">{f.status === "deleted" ? "File đã xóa." : "Không có nội dung văn bản (file rỗng hoặc nhị phân)."}</div>}
          <div className="overflow-x-auto font-mono text-[12px] leading-5">
            <div className="min-w-max">
              {f.lines.map((l, i) =>
                l.kind === "hunk" ? (
                  <div key={i} className="bg-sky-500/[0.07] px-3 text-[11.5px] text-sky-300/80">
                    {l.text}
                  </div>
                ) : (
                  <div key={i} className={`flex ${l.kind === "add" ? "bg-emerald-500/10" : l.kind === "del" ? "bg-rose-500/10" : ""}`}>
                    <span className="w-10 shrink-0 select-none pr-2 text-right text-zinc-600">{l.oldNo ?? ""}</span>
                    <span className="w-10 shrink-0 select-none border-[#27272a] border-r pr-2 text-right text-zinc-600">{l.newNo ?? ""}</span>
                    <span className={`w-5 shrink-0 select-none text-center ${l.kind === "add" ? "text-emerald-400" : l.kind === "del" ? "text-rose-400" : "text-zinc-700"}`}>{l.kind === "add" ? "+" : l.kind === "del" ? "−" : ""}</span>
                    <pre className={`whitespace-pre pr-4 ${l.kind === "add" ? "text-emerald-200" : l.kind === "del" ? "text-rose-200" : "text-zinc-400"}`}>{l.text}</pre>
                  </div>
                ),
              )}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

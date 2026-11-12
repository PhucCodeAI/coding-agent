import { ActivityIcon, CheckIcon, PanelLeftCloseIcon, PencilIcon, PlusIcon, SearchIcon, Trash2Icon, XIcon } from "lucide-react";
import { useState } from "react";
import { api, type SessionSummary } from "@/api";
import { STATUS_DOT } from "@/lib/format";
import { linkHandler } from "@/lib/router";

const DAY = 86_400_000;

function groupOf(iso: string): string {
  const start = new Date();
  start.setHours(0, 0, 0, 0);
  const t = new Date(iso).getTime();
  if (t >= start.getTime()) return "Hôm nay";
  if (t >= start.getTime() - DAY) return "Hôm qua";
  if (t >= start.getTime() - 7 * DAY) return "7 ngày qua";
  if (t >= start.getTime() - 30 * DAY) return "30 ngày qua";
  return "Cũ hơn";
}

function Item({ s, active, running, onChanged, onDeleted }: { s: SessionSummary; active: boolean; running: boolean; onChanged: () => void; onDeleted: (id: string) => void }) {
  const [editing, setEditing] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [title, setTitle] = useState(s.title);
  const save = async () => {
    setEditing(false);
    if (title.trim() && title.trim() !== s.title) {
      await api.renameSession(s.session_id, title.trim()).catch(() => setTitle(s.title));
      onChanged();
    } else setTitle(s.title);
  };
  const dot = running ? "animate-pulse bg-amber-400" : (STATUS_DOT[s.last_status ?? ""] ?? "bg-zinc-700");
  const href = `/c/${s.session_id}`;

  if (editing)
    return (
      <input
        autoFocus
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        onBlur={save}
        onKeyDown={(e) => {
          if (e.key === "Enter") void save();
          if (e.key === "Escape") (setTitle(s.title), setEditing(false));
        }}
        className="w-full rounded-md border border-amber-500/50 bg-[#1a1a1e] px-2.5 py-1.5 text-[13px] text-zinc-100 focus:outline-none"
      />
    );
  return (
    <a href={href} onClick={linkHandler(href)} className={`group flex items-center gap-2 rounded-md px-2.5 py-1.5 text-[13px] ${active ? "bg-[#1f1f23] text-zinc-100" : "text-zinc-400 hover:bg-[#18181b] hover:text-zinc-200"}`}>
      <span className={`size-1.5 shrink-0 rounded-full ${dot}`} />
      <span className="min-w-0 flex-1 truncate" title={s.title}>
        {s.title || "Phiên chưa đặt tên"}
      </span>
      {confirming ? (
        <span className="flex shrink-0 items-center gap-0.5" onClick={(e) => e.preventDefault()}>
          <button type="button" title="Xóa" onClick={() => api.deleteSession(s.session_id).then(() => onDeleted(s.session_id))} className="rounded p-0.5 text-rose-400 hover:bg-rose-500/15">
            <CheckIcon className="size-3.5" />
          </button>
          <button type="button" title="Hủy" onClick={() => setConfirming(false)} className="rounded p-0.5 text-zinc-400 hover:bg-[#2a2a30]">
            <XIcon className="size-3.5" />
          </button>
        </span>
      ) : (
        <span className="hidden shrink-0 items-center gap-0.5 group-hover:flex" onClick={(e) => e.preventDefault()}>
          <button type="button" title="Đổi tên" onClick={() => setEditing(true)} className="rounded p-0.5 hover:bg-[#2a2a30] hover:text-zinc-100">
            <PencilIcon className="size-3" />
          </button>
          <button type="button" title="Xóa phiên" disabled={running} onClick={() => setConfirming(true)} className="rounded p-0.5 hover:bg-rose-500/15 hover:text-rose-400 disabled:opacity-30">
            <Trash2Icon className="size-3" />
          </button>
        </span>
      )}
    </a>
  );
}

export function Sidebar({ sessions, activeId, runningIds, onClose, onChanged, onDeleted }: { sessions: SessionSummary[]; activeId: string | null; runningIds: string[]; onClose: () => void; onChanged: () => void; onDeleted: (id: string) => void }) {
  const [query, setQuery] = useState("");
  const shown = sessions.filter((s) => s.title.toLowerCase().includes(query.toLowerCase()));
  const groups = new Map<string, SessionSummary[]>();
  for (const s of shown) groups.set(groupOf(s.updated_at), [...(groups.get(groupOf(s.updated_at)) ?? []), s]);

  return (
    <aside className="flex h-full w-64 shrink-0 flex-col border-[#1f1f23] border-r bg-[#0f0f11]">
      <div className="flex h-12 items-center gap-2 px-3">
        <span className="size-2 rounded-full bg-amber-500" />
        <span className="font-semibold text-[13px] text-zinc-100 tracking-tight">Coding Agent</span>
        <a href="/traces" onClick={linkHandler("/traces")} title="Trace Explorer" className="ml-auto rounded-md p-1.5 text-zinc-500 hover:bg-[#1c1c20] hover:text-zinc-200">
          <ActivityIcon className="size-4" />
        </a>
        <button type="button" onClick={onClose} title="Thu gọn (⌘/Ctrl B)" className="rounded-md p-1.5 text-zinc-500 hover:bg-[#1c1c20] hover:text-zinc-200">
          <PanelLeftCloseIcon className="size-4" />
        </button>
      </div>
      <div className="space-y-2 px-3 pb-2">
        <a href="/" onClick={linkHandler("/")} className="flex items-center justify-center gap-1.5 rounded-lg bg-amber-500 py-1.5 font-semibold text-[13px] text-zinc-950 hover:bg-amber-400">
          <PlusIcon className="size-4" strokeWidth={2.5} />
          Phiên mới
        </a>
        <label className="flex items-center gap-2 rounded-lg border border-[#232327] bg-[#141416] px-2.5 py-1.5 focus-within:border-zinc-600">
          <SearchIcon className="size-3.5 text-zinc-500" />
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Tìm phiên…" className="min-w-0 flex-1 bg-transparent text-[12.5px] text-zinc-200 placeholder-zinc-600 focus:outline-none" />
        </label>
      </div>
      <nav className="min-h-0 flex-1 space-y-3 overflow-y-auto px-2 pb-3">
        {[...groups].map(([label, items]) => (
          <div key={label}>
            <div className="px-2.5 pt-1 pb-1 font-medium text-[10.5px] text-zinc-600 uppercase tracking-wider">{label}</div>
            {items.map((s) => (
              <Item key={s.session_id} s={s} active={s.session_id === activeId} running={runningIds.includes(s.session_id)} onChanged={onChanged} onDeleted={onDeleted} />
            ))}
          </div>
        ))}
        {!shown.length && <div className="px-2.5 py-4 text-[12px] text-zinc-600">{query ? "Không có phiên khớp." : "Chưa có phiên nào."}</div>}
      </nav>
      <div className="border-[#1f1f23] border-t px-3 py-2 text-[10.5px] text-zinc-600">
        {sessions.length} phiên · ⌘/Ctrl B · ⌘/Ctrl ⇧ O
      </div>
    </aside>
  );
}

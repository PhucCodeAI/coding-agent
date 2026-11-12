import { PanelLeftOpenIcon, PanelRightOpenIcon, PlusIcon } from "lucide-react";
import { formatCost, formatNumber, formatTokens } from "@/lib/format";
import type { Usage } from "@/lib/agent";
import { linkHandler } from "@/lib/router";

const CONTEXT_BUDGET = 8000;

function ContextMeter({ usage }: { usage: Usage }) {
  const ratio = Math.min(1, usage.contextTokens / CONTEXT_BUDGET);
  const color = ratio > 0.9 ? "#f43f5e" : ratio > 0.7 ? "#f59e0b" : "#10b981";
  const r = 7;
  const c = 2 * Math.PI * r;
  return (
    <div className="group relative flex cursor-help items-center gap-1.5 text-[11.5px] text-zinc-400 tabular-nums">
      <svg viewBox="0 0 18 18" className="size-4 -rotate-90" aria-hidden="true">
        <circle cx="9" cy="9" r={r} fill="none" stroke="#2a2a2e" strokeWidth="2.5" />
        <circle cx="9" cy="9" r={r} fill="none" stroke={color} strokeWidth="2.5" strokeDasharray={c} strokeDashoffset={c * (1 - ratio)} strokeLinecap="round" />
      </svg>
      {formatTokens(usage.contextTokens)}
      <div className="pointer-events-none absolute top-full right-0 z-40 mt-2 hidden w-60 space-y-1 rounded-lg border border-[#2f2f35] bg-[#18181c] p-3 text-[11.5px] shadow-2xl shadow-black/40 group-hover:block">
        <div className="mb-1.5 font-medium text-zinc-200">Context hiện tại</div>
        <Row k="Turn gần nhất (vào + ra)" v={formatNumber(usage.contextTokens)} />
        <Row k="· vào" v={formatNumber(usage.inputTokens)} />
        <Row k="· ra" v={formatNumber(usage.outputTokens)} />
        <Row k="Ngân sách tham chiếu" v={formatNumber(CONTEXT_BUDGET)} />
        <div className="h-1.5 overflow-hidden rounded-full bg-[#2a2a2e]">
          <div className="h-full rounded-full" style={{ width: `${ratio * 100}%`, background: color }} />
        </div>
        <p className="pt-1 text-[10.5px] text-zinc-500">Vượt ngân sách thì agent tự rút gọn lịch sử cũ trước khi gọi model.</p>
      </div>
    </div>
  );
}

const Row = ({ k, v }: { k: string; v: string }) => (
  <div className="flex justify-between gap-3 text-zinc-400">
    <span>{k}</span>
    <span className="font-mono text-zinc-200">{v}</span>
  </div>
);

export function Header({ title, live, usage, sidebarOpen, workspaceOpen, onOpenSidebar, onOpenWorkspace }: { title: string; live: boolean; usage: Usage; sidebarOpen: boolean; workspaceOpen: boolean; onOpenSidebar: () => void; onOpenWorkspace: () => void }) {
  return (
    <header className="flex h-12 shrink-0 items-center gap-2 border-[#232327] border-b px-3">
      {!sidebarOpen && (
        <>
          <button type="button" onClick={onOpenSidebar} title="Mở sidebar (⌘/Ctrl B)" className="rounded-md p-1.5 text-zinc-500 hover:bg-[#1c1c20] hover:text-zinc-200">
            <PanelLeftOpenIcon className="size-4" />
          </button>
          <a href="/" onClick={linkHandler("/")} title="Phiên mới" className="rounded-md p-1.5 text-zinc-500 hover:bg-[#1c1c20] hover:text-zinc-200">
            <PlusIcon className="size-4" />
          </a>
        </>
      )}
      <h1 className="min-w-0 truncate font-medium text-[13.5px] text-zinc-200">{title}</h1>
      {live && <span className="shrink-0 animate-pulse text-[11.5px] text-amber-300">đang làm việc…</span>}
      <div className="ml-auto flex shrink-0 items-center gap-3">
        {usage.turns > 0 && (
          <>
            <span className="cursor-help text-[11.5px] text-zinc-500 tabular-nums" title={`Tổng token cả phiên: ${formatNumber(usage.totalTokens)}${usage.model ? `\nModel gần nhất: ${usage.model}` : ""}`}>
              {formatTokens(usage.totalTokens)} tokens · {formatCost(usage.cost)}
            </span>
            <span className="text-[11.5px] text-zinc-500 tabular-nums" title="Số lần gọi model trong phiên">
              {usage.turns} turn
            </span>
            <ContextMeter usage={usage} />
          </>
        )}
        {!workspaceOpen && (
          <button type="button" onClick={onOpenWorkspace} title="Mở workspace (⌘/Ctrl ⇧ O)" className="flex items-center gap-1.5 rounded-md border border-[#2a2a2e] px-2 py-1 text-[12px] text-zinc-400 hover:bg-[#1c1c20] hover:text-zinc-200">
            <PanelRightOpenIcon className="size-3.5" />
            Workspace
          </button>
        )}
      </div>
    </header>
  );
}

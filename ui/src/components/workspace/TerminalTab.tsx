import { ChevronDownIcon, ChevronRightIcon } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { SandboxRun, Turn } from "@/api";
import { COMMANDS, StatusIcon, Terminal } from "@/components/chat/ToolRow";
import { ErrorBanner } from "@/components/common";
import type { ToolStatus } from "@/lib/agent";
import { formatDuration } from "@/lib/format";
import type { UserRun } from "./useWorkspace";

interface Block {
  key: string;
  by: "agent" | "user";
  command: string;
  at: string;
  status: ToolStatus;
  durationMs?: number;
  runs: SandboxRun[];
  output?: string;
  run?: UserRun;
}

/** Mỗi lời gọi công cụ chạy lệnh của agent = 1 khối; lệnh sandbox phát sinh trong lúc gọi gắn vào khối đó. */
function agentBlocks(turns: Turn[]): Block[] {
  const out: Block[] = [];
  for (const turn of turns) {
    const byId = new Map<string, Block>();
    for (const e of turn.events) {
      if (e.event === "step")
        for (const c of e.data.tool_calls) {
          if (!COMMANDS[c.name]) continue;
          const b: Block = { key: c.id, by: "agent", command: COMMANDS[c.name](c.args), at: turn.at, status: turn.live ? "running" : "unknown", runs: [] };
          byId.set(c.id, b);
          out.push(b);
        }
      else if (e.event === "sandbox") [...byId.values()].find((b) => b.status === "running" || b.status === "unknown")?.runs.push(e.data);
      else if (e.event === "tool") {
        const b = byId.get(e.data.id);
        if (b) Object.assign(b, { status: e.data.ok ? "done" : "error", durationMs: e.data.duration_ms, output: e.data.output });
      }
    }
  }
  return out;
}

function userBlock(r: UserRun): Block {
  const res = r.result;
  return {
    key: `u${r.id}`,
    by: "user",
    command: r.command,
    at: r.at,
    status: r.error ? "error" : !res ? "running" : res.exit_code === 0 ? "done" : "error",
    durationMs: res?.duration_ms,
    runs: [],
    output: res ? [res.stdout, res.stderr].filter(Boolean).join("\n").trimEnd() + (res.is_timeout ? "\n[timeout]" : res.exit_code ? `\n[exit ${res.exit_code}]` : "") : undefined,
    run: r,
  };
}

function BlockRow({ b, open, onToggle }: { b: Block; open: boolean; onToggle: () => void }) {
  return (
    <div className={`overflow-hidden rounded-lg border bg-[#16161a] ${b.status === "error" ? "border-rose-500/30" : "border-[#27272a]"}`}>
      <button type="button" onClick={onToggle} className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left hover:bg-[#1b1b20]">
        {open ? <ChevronDownIcon className="size-3.5 shrink-0 text-zinc-600" /> : <ChevronRightIcon className="size-3.5 shrink-0 text-zinc-600" />}
        <span className={`shrink-0 rounded px-1 font-medium text-[10px] ${b.by === "agent" ? "bg-amber-500/10 text-amber-300" : "bg-sky-500/10 text-sky-300"}`}>{b.by === "agent" ? "AI" : "Bạn"}</span>
        <span className="min-w-0 flex-1 truncate font-mono text-[12px] text-zinc-200">
          <span className="text-zinc-500">$ </span>
          {b.command}
        </span>
        {b.durationMs !== undefined && <span className="shrink-0 font-mono text-[10.5px] text-zinc-600">{formatDuration(b.durationMs)}</span>}
        <StatusIcon status={b.status} />
      </button>
      {open && (
        <div className="space-y-2 border-[#232327] border-t p-2.5">
          {b.run?.error && <ErrorBanner error={b.run.error} />}
          {b.runs.length > 0
            ? b.runs.map((r, i) => (
                <div key={i} className="space-y-1">
                  <div className="flex gap-2 text-[11px] text-zinc-500">
                    <span className={r.is_timeout ? "text-amber-400" : r.exit_code === 0 ? "text-emerald-400" : "text-rose-400"}>{r.is_timeout ? "timeout" : `exit ${r.exit_code}`}</span>
                    <span>{formatDuration(r.duration_ms)}</span>
                  </div>
                  <Terminal command={r.command.join(" ")} output={r.stdout_summary} />
                </div>
              ))
            : !b.run?.error && <Terminal command={b.command} output={b.output ?? (b.status === "unknown" ? "(lượt đã dừng trước khi lệnh xong)" : undefined)} running={b.status === "running"} />}
          {b.runs.length > 0 && b.output && b.status === "error" && <pre className="whitespace-pre-wrap font-mono text-[11.5px] text-rose-300">{b.output}</pre>}
        </div>
      )}
    </div>
  );
}

export function TerminalTab({ turns, userRuns, live, onExec }: { turns: Turn[]; userRuns: UserRun[]; live: boolean; onExec: (command: string) => Promise<void> }) {
  const blocks = useMemo(() => [...agentBlocks(turns), ...userRuns.map(userBlock)].sort((a, b) => a.at.localeCompare(b.at)), [turns, userRuns]);
  const [toggled, setToggled] = useState<Set<string>>(new Set());
  const [input, setInput] = useState("");
  const [historyAt, setHistoryAt] = useState<number | null>(null);
  const list = useRef<HTMLDivElement>(null);
  const busy = userRuns.some((r) => !r.result && !r.error);
  // Lệnh của chính người dùng mở sẵn; khối của agent thu gọn, bấm để xem.
  const isOpen = (b: Block) => toggled.has(b.key) !== (b.by === "user");

  useEffect(() => {
    const el = list.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [blocks.length, userRuns]);

  const submit = async () => {
    const command = input.trim();
    if (!command || live || busy) return;
    setInput("");
    setHistoryAt(null);
    await onExec(command);
  };
  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") return void submit();
    if (e.key !== "ArrowUp" && e.key !== "ArrowDown") return;
    const history = userRuns.map((r) => r.command);
    if (!history.length) return;
    e.preventDefault();
    const at = Math.max(0, Math.min(history.length, (historyAt ?? history.length) + (e.key === "ArrowUp" ? -1 : 1)));
    setHistoryAt(at);
    setInput(history[at] ?? "");
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div ref={list} className="min-h-0 flex-1 space-y-2 overflow-y-auto p-3">
        {blocks.length ? (
          blocks.map((b) => <BlockRow key={b.key} b={b} open={isOpen(b)} onToggle={() => setToggled((s) => (s.has(b.key) ? new Set([...s].filter((k) => k !== b.key)) : new Set([...s, b.key])))} />)
        ) : (
          <div className="py-8 text-center text-[12.5px] text-zinc-500">Chưa có lệnh nào. Lệnh agent chạy sẽ hiện ở đây; bạn cũng có thể tự chạy lệnh bên dưới.</div>
        )}
      </div>
      <div className="shrink-0 border-[#232327] border-t p-2">
        <label className={`flex items-center gap-2 rounded-lg border bg-[#0a0a0c] px-3 py-2 font-mono text-[12.5px] ${live ? "border-[#232327] opacity-60" : "border-[#2f2f35] focus-within:border-zinc-500"}`}>
          <span className="text-sky-300">$</span>
          <input
            value={input}
            disabled={live}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder={live ? "Agent đang chạy — đợi xong để chạy lệnh" : busy ? "Đang chạy…" : "Lệnh shell trong sandbox (không mạng), VD: python -m pytest -q"}
            className="min-w-0 flex-1 bg-transparent text-zinc-100 placeholder-zinc-600 focus:outline-none"
          />
          {busy && <span className="size-3.5 shrink-0 animate-spin rounded-full border-2 border-amber-400 border-t-transparent" />}
        </label>
      </div>
    </div>
  );
}

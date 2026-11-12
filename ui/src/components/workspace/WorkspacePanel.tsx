import { CodeIcon, GitCompareIcon, PanelRightCloseIcon, SquareTerminalIcon } from "lucide-react";
import type { Turn } from "@/api";
import { COMMANDS } from "@/components/chat/ToolRow";
import { ErrorBanner } from "@/components/common";
import { resultsOf, stepsOf } from "@/lib/agent";
import { relativeTime } from "@/lib/format";
import { DiffViewer } from "./DiffViewer";
import { FileTree } from "./FileTree";
import { FileView } from "./FileView";
import { TerminalTab } from "./TerminalTab";
import { WORKING, type Workspace } from "./useWorkspace";

function Empty({ children }: { children: React.ReactNode }) {
  return <div className="flex h-full items-center justify-center p-6 text-center text-[12.5px] text-zinc-500">{children}</div>;
}

function CodeTab({ ws, live }: { ws: Workspace; live: boolean }) {
  return (
    <div className="h-full overflow-y-auto">
      <FileTree files={ws.files} open={ws.openPath} changed={ws.recentlyChanged} onToggle={ws.toggleFile} renderOpen={(path) => <FileView file={ws.file?.path === path ? ws.file : null} locked={live} onSave={(c) => ws.saveFile(path, c)} />} />
    </div>
  );
}

function ChangesTab({ ws }: { ws: Workspace }) {
  return (
    <div className="flex h-full min-h-0">
      <div className="w-56 shrink-0 overflow-y-auto border-[#232327] border-r p-1.5">
        {ws.changes.length > 0 && (
          <button
            type="button"
            onClick={ws.openWorking}
            className={`mb-1 w-full rounded-md px-2.5 py-2 text-left ${ws.selectedCommit === WORKING ? "bg-[#26262c]" : "hover:bg-[#1c1c20]"}`}
          >
            <div className="font-medium text-[12.5px] text-amber-300">Chưa commit</div>
            <div className="text-[11px] text-zinc-500">{ws.changes.length} file thay đổi</div>
          </button>
        )}
        {ws.commits.map((c) => (
          <button
            key={c.hash}
            type="button"
            onClick={() => ws.openCommit(c.hash)}
            className={`w-full rounded-md px-2.5 py-2 text-left ${ws.selectedCommit === c.hash ? "bg-[#26262c]" : "hover:bg-[#1c1c20]"}`}
          >
            <div className="line-clamp-2 text-[12.5px] text-zinc-200">{c.message}</div>
            <div className="mt-0.5 flex gap-2 text-[11px] text-zinc-500">
              <span className="font-mono text-sky-400">{c.hash.slice(0, 7)}</span>
              <span>{c.files.length} file</span>
              <span className="ml-auto">{relativeTime(c.date)}</span>
            </div>
          </button>
        ))}
        {!ws.commits.length && !ws.changes.length && <div className="px-2 py-4 text-[12px] text-zinc-500">Chưa có commit.</div>}
      </div>
      <div className="min-w-0 flex-1 overflow-auto">
        {ws.diff !== null ? (
          <>
            {ws.diffTruncated && <div className="px-3 pt-3 text-[11.5px] text-amber-400">Diff quá dài, đã cắt bớt.</div>}
            <DiffViewer diff={ws.diff} />
          </>
        ) : (
          <Empty>Chọn một commit để xem diff.</Empty>
        )}
      </div>
    </div>
  );
}

export function WorkspacePanel({ ws, turns, live }: { ws: Workspace; turns: Turn[]; live: boolean }) {
  const commandCalls = turns.flatMap((t) => stepsOf(t.events).flatMap((s) => s.tool_calls.filter((c) => COMMANDS[c.name])));
  const last = turns.at(-1);
  const finished = last ? resultsOf(last.events) : new Map();
  const running = live && !!last && stepsOf(last.events).some((s) => s.tool_calls.some((c) => COMMANDS[c.name] && !finished.has(c.id)));
  const tabs = [
    { key: "code", label: "Code", icon: CodeIcon, badge: ws.files.length ? String(ws.files.length) : "" },
    { key: "changes", label: "Thay đổi", icon: GitCompareIcon, badge: ws.changes.length ? `${ws.changes.length}*` : ws.commits.length ? String(ws.commits.length) : "" },
    { key: "terminal", label: "Terminal", icon: SquareTerminalIcon, badge: commandCalls.length + ws.userRuns.length ? String(commandCalls.length + ws.userRuns.length) : "" },
  ] as const;
  return (
    <div className="flex h-full min-w-0 flex-col bg-[#111113]">
      <div className="flex h-12 shrink-0 items-center gap-1 border-[#232327] border-b px-2">
        {tabs.map((t) => (
          <button
            key={t.key}
            type="button"
            onClick={() => ws.setTab(t.key)}
            className={`flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-[12.5px] ${ws.tab === t.key ? "bg-[#232328] text-zinc-100" : "text-zinc-400 hover:bg-[#1c1c20] hover:text-zinc-200"}`}
          >
            <t.icon className="size-3.5" />
            {t.label}
            {t.badge && <span className={`font-mono text-[10.5px] ${t.badge.endsWith("*") ? "text-amber-400" : "text-zinc-500"}`}>{t.badge}</span>}
            {t.key === "terminal" && running && <span className="size-1.5 animate-pulse rounded-full bg-amber-400" />}
          </button>
        ))}
        <button type="button" onClick={() => ws.setOpen(false)} title="Đóng workspace (⌘/Ctrl ⇧ O)" className="ml-auto rounded-md p-1.5 text-zinc-500 hover:bg-[#1c1c20] hover:text-zinc-200">
          <PanelRightCloseIcon className="size-4" />
        </button>
      </div>
      {ws.error && (
        <div className="p-2">
          <ErrorBanner error={ws.error} />
        </div>
      )}
      <div className="min-h-0 flex-1">
        {ws.tab === "code" ? <CodeTab ws={ws} live={live} /> : ws.tab === "changes" ? <ChangesTab ws={ws} /> : <TerminalTab turns={turns} userRuns={ws.userRuns} live={live} onExec={ws.exec} />}
      </div>
    </div>
  );
}

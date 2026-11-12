import {
  ChevronDownIcon,
  ChevronRightIcon,
  CircleCheckIcon,
  CircleIcon,
  CircleXIcon,
  FilePenIcon,
  FilePlusIcon,
  FileSearchIcon,
  FileTextIcon,
  FileXIcon,
  FolderTreeIcon,
  MessageCircleQuestionIcon,
  PackageIcon,
  SquareTerminalIcon,
  TestTubeDiagonalIcon,
  WrenchIcon,
} from "lucide-react";
import { useState } from "react";
import type { ToolCall, ToolResult } from "@/api";
import { CodeCard, CopyButton } from "@/components/common";
import { formatDuration } from "@/lib/format";
import { type ToolStatus, toolStatus, toolTarget } from "@/lib/agent";

const ICONS: Record<string, typeof WrenchIcon> = {
  list_files: FolderTreeIcon,
  find_files: FolderTreeIcon,
  read_file: FileTextIcon,
  read_outline: FileTextIcon,
  search_text: FileSearchIcon,
  write_file: FilePlusIcon,
  edit_file: FilePenIcon,
  delete_file: FileXIcon,
  run_tests: TestTubeDiagonalIcon,
  run_command: SquareTerminalIcon,
  install_packages: PackageIcon,
  ask_user: MessageCircleQuestionIcon,
};

export const COMMANDS: Record<string, (a: Record<string, unknown>) => string> = {
  run_tests: (a) => `pytest ${String(a.pattern ?? "test_*.py")}`,
  run_command: (a) => (Array.isArray(a.argv) ? a.argv.join(" ") : ""),
  install_packages: (a) => `pip install ${Array.isArray(a.packages) ? a.packages.join(" ") : ""}`,
};

export function StatusIcon({ status }: { status: ToolStatus }) {
  if (status === "running") return <span className="size-3.5 shrink-0 animate-spin rounded-full border-2 border-amber-400 border-t-transparent" />;
  if (status === "done") return <CircleCheckIcon className="size-3.5 shrink-0 text-emerald-400" />;
  if (status === "error") return <CircleXIcon className="size-3.5 shrink-0 text-rose-400" />;
  return <CircleIcon className="size-3.5 shrink-0 text-zinc-600" />;
}

export function Terminal({ command, output, running }: { command: string; output?: string; running?: boolean }) {
  const lineClass = (line: string) =>
    /PASS|passed|\bOK\b/.test(line) ? "text-emerald-300" : /FAIL|Error|Traceback|failed/.test(line) ? "text-rose-300" : /WARN|Warning/.test(line) ? "text-amber-300" : "text-zinc-100";
  return (
    <div className="overflow-hidden rounded-lg border border-[#3f3f46] bg-[#0a0a0c] font-mono text-[11.5px]">
      <div className="flex items-center justify-between border-[#3f3f46] border-b bg-[#232326] px-2.5 py-1">
        <span className="truncate text-sky-300">$ {command}</span>
        <CopyButton text={`$ ${command}\n${output ?? ""}`} />
      </div>
      {(output !== undefined || running) && (
        <div className="max-h-80 overflow-y-auto p-3 leading-relaxed">
          {(output ?? "").split("\n").map((line, i) => (
            <div key={i} className={`whitespace-pre-wrap break-words ${lineClass(line)}`}>
              {line || " "}
            </div>
          ))}
          {running && <span className="inline-block h-3.5 w-2 animate-pulse bg-zinc-300" />}
        </div>
      )}
    </div>
  );
}

function EditDiff({ path, oldText, newText }: { path: string; oldText: string; newText: string }) {
  const rows = [...oldText.split("\n").map((t) => ["del", t]), ...newText.split("\n").map((t) => ["add", t])];
  return (
    <div className="overflow-hidden rounded-lg border border-[#27272a]">
      <div className="border-[#27272a] border-b bg-[#18181b] px-3 py-1.5 font-mono text-[12px] text-zinc-200">{path}</div>
      <div className="max-h-80 overflow-auto font-mono text-[12px] leading-5">
        {rows.map(([kind, text], i) => (
          <div key={i} className={`flex ${kind === "add" ? "bg-emerald-500/10" : "bg-rose-500/10"}`}>
            <span className={`w-5 shrink-0 select-none text-center ${kind === "add" ? "text-emerald-400" : "text-rose-400"}`}>{kind === "add" ? "+" : "−"}</span>
            <pre className={`whitespace-pre pr-4 ${kind === "add" ? "text-emerald-200" : "text-rose-200"}`}>{text}</pre>
          </div>
        ))}
      </div>
    </div>
  );
}

function Detail({ call, result, status }: { call: ToolCall; result?: ToolResult; status: ToolStatus }) {
  const a = call.args;
  const path = String(a.path ?? "");
  const out = result?.output;
  const note = out !== undefined && <pre className={`whitespace-pre-wrap font-mono text-[11.5px] ${status === "error" ? "text-rose-300" : "text-zinc-500"}`}>{out}</pre>;
  if (COMMANDS[call.name]) return <Terminal command={COMMANDS[call.name](a)} output={out} running={status === "running"} />;
  if (call.name === "write_file")
    return (
      <>
        <CodeCard code={String(a.content ?? "")} title={path} />
        {note}
      </>
    );
  if (call.name === "edit_file")
    return (
      <>
        <EditDiff path={path} oldText={String(a.old_text ?? "")} newText={String(a.new_text ?? "")} />
        {note}
      </>
    );
  if ((call.name === "read_file" || call.name === "read_outline") && out && status !== "error") return <CodeCard code={out} title={path} />;
  if (!Object.keys(a).length || call.name === "list_files" || call.name === "find_files") return note;
  return (
    <>
      <CodeCard code={JSON.stringify(a, null, 2)} title="tham số" />
      {note}
    </>
  );
}

export function ToolRow({ call, result, live }: { call: ToolCall; result?: ToolResult; live: boolean }) {
  const status = toolStatus(result, live);
  const [open, setOpen] = useState(status === "error" && live);
  const Icon = ICONS[call.name] ?? WrenchIcon;
  return (
    <div className={`overflow-hidden rounded-lg border bg-[#16161a] ${status === "error" ? "border-rose-500/30" : "border-[#27272a]"}`}>
      <button type="button" onClick={() => setOpen(!open)} className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left hover:bg-[#1b1b20]">
        <Icon className="size-3.5 shrink-0 text-zinc-500" />
        <span className="shrink-0 font-mono text-[12px] text-zinc-200">{call.name}</span>
        <span className="min-w-0 flex-1 truncate font-mono text-[11.5px] text-zinc-500">{toolTarget(call)}</span>
        {result && <span className="shrink-0 font-mono text-[10.5px] text-zinc-600">{formatDuration(result.duration_ms)}</span>}
        <StatusIcon status={status} />
        {open ? <ChevronDownIcon className="size-3.5 shrink-0 text-zinc-600" /> : <ChevronRightIcon className="size-3.5 shrink-0 text-zinc-600" />}
      </button>
      {open && <div className="space-y-2 border-[#232327] border-t p-2.5">{<Detail call={call} result={result} status={status} />}</div>}
    </div>
  );
}

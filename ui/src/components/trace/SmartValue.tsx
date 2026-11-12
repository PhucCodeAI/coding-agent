import { useState } from "react";
import { CodeCard, CopyButton } from "@/components/common";
import { Terminal } from "@/components/chat/ToolRow";

const TEXT_KEYS = ["explanation", "content", "message", "description", "summary", "text", "reasoning", "thought", "prompt", "note", "comment", "answer", "response", "output"];
const ROLE_CLASS: Record<string, string> = {
  system: "bg-violet-500/20 border-violet-400/50 text-violet-200",
  user: "bg-sky-500/20 border-sky-400/50 text-sky-200",
  assistant: "bg-emerald-500/20 border-emerald-400/50 text-emerald-200",
  tool: "bg-amber-500/20 border-amber-400/50 text-amber-200",
};
const ACTION_CLASS: Record<string, string> = {
  create: "bg-emerald-500/20 border-emerald-400/50 text-emerald-200",
  update: "bg-amber-500/20 border-amber-400/50 text-amber-200",
  delete: "bg-rose-500/20 border-rose-400/50 text-rose-200",
};

type Obj = Record<string, unknown>;
const isObj = (x: unknown): x is Obj => typeof x === "object" && x !== null && !Array.isArray(x);
const isArrayOf = (x: unknown, ok: (v: unknown) => boolean): x is unknown[] => Array.isArray(x) && x.length > 0 && x.every(ok);
const isTextual = (key: string, v: unknown): v is string => typeof v === "string" && v.length > 0 && (TEXT_KEYS.includes(key.toLowerCase()) || v.includes("\n") || v.length > 120);

function Json({ data }: { data: unknown }) {
  const text = JSON.stringify(data, null, 2) ?? String(data);
  return (
    <div className="group relative">
      <pre className="max-h-96 overflow-auto rounded-md border border-[#3f3f46] bg-[#0f0f11] p-2.5 font-mono text-[11.5px] text-zinc-100 leading-relaxed">{text}</pre>
      <CopyButton text={text} label={false} className="absolute top-1.5 right-1.5 opacity-0 group-hover:opacity-100" />
    </div>
  );
}

function Text({ label, content, showLabel }: { label: string; content: string; showLabel: boolean }) {
  return (
    <div className="space-y-1">
      {showLabel && <div className="font-mono font-semibold text-[10px] text-zinc-400 uppercase tracking-wider">{label}</div>}
      <div className="max-h-96 overflow-auto whitespace-pre-wrap break-words rounded-md border border-[#3f3f46] bg-[#0f0f11] p-2.5 text-[12.5px] text-zinc-50 leading-relaxed">{content}</div>
    </div>
  );
}

function Body({ label, value, depth }: { label: string; value: unknown; depth: number }): React.ReactNode {
  const k = label.toLowerCase();
  if (value === null || typeof value !== "object") {
    if (isTextual(label, value)) return <Text label={label} content={value} showLabel={depth > 0} />;
    return (
      <div className={`flex items-baseline gap-2 py-0.5 font-mono text-[12px] ${depth > 0 ? "pl-3" : ""}`}>
        <span className="shrink-0 text-zinc-400">{label}:</span>
        <span className="break-all text-zinc-50">{typeof value === "string" ? value : String(value)}</span>
      </div>
    );
  }
  if (isArrayOf(value, (m) => isObj(m) && typeof m.role === "string" && "content" in m))
    return (
      <div className="divide-y divide-[#3f3f46] overflow-hidden rounded-lg border border-[#52525b] bg-[#18181b]">
        {(value as Obj[]).map((m, i) => (
          <div key={i} className="grid grid-cols-[80px_1fr] gap-3 p-3">
            <span className={`h-fit w-fit rounded border px-1.5 py-0.5 font-bold font-mono text-[10px] uppercase tracking-wide ${ROLE_CLASS[String(m.role).toLowerCase()] ?? "border-zinc-400/50 bg-zinc-500/20 text-zinc-200"}`}>{String(m.role)}</span>
            <div className="min-w-0 whitespace-pre-wrap break-words text-[12.5px] text-zinc-50 leading-relaxed">{typeof m.content === "string" ? m.content : JSON.stringify(m.content, null, 2)}</div>
          </div>
        ))}
      </div>
    );
  if (isArrayOf(value, (f) => isObj(f) && typeof f.path === "string"))
    return (
      <div className="space-y-2">
        {(value as Obj[]).map((f, i) => {
          const action = String(f.action ?? "file").toLowerCase();
          const code = ["code", "test", "content", "source", "diff"].map((x) => f[x]).find((x) => typeof x === "string") as string | undefined;
          return (
            <div key={i} className="space-y-1.5">
              <div className="flex items-center gap-2">
                <span className={`rounded border px-1.5 py-0.5 font-bold font-mono text-[10px] ${ACTION_CLASS[action] ?? "border-zinc-400/50 bg-zinc-500/20 text-zinc-200"}`}>{action}</span>
                <span className="truncate font-mono text-[12px] text-zinc-100">{String(f.path)}</span>
              </div>
              {code && <CodeCard code={code} title={String(f.path)} />}
            </div>
          );
        })}
      </div>
    );
  if (isArrayOf(value, (v) => typeof v === "string") && (k.includes("command") || k === "cmd" || k === "args" || k === "argv")) return <Terminal command={(value as string[]).join(" ")} />;
  if (isObj(value) && depth < 3)
    return (
      <div className="space-y-1">
        <div className={`font-mono font-semibold text-[10px] uppercase tracking-wider ${depth === 0 ? "mb-1 text-zinc-300" : "mb-0.5 pl-3 text-zinc-400"}`}>{label}</div>
        {Object.entries(value).map(([ck, cv]) => (
          <Value key={ck} label={ck} value={cv} depth={depth + 1} />
        ))}
      </div>
    );
  return <Json data={value} />;
}

function StringValue({ label, value, depth }: { label: string; value: string; depth: number }) {
  const k = label.toLowerCase();
  if (k.includes("stdout") || k.includes("stderr") || k === "log") return <Terminal command={label} output={value} />;
  if (k.includes("code") || k === "diff" || k === "source") return <CodeCard code={value} title={label} />;
  return <Body label={label} value={value} depth={depth} />;
}

function Value({ label, value, depth }: { label: string; value: unknown; depth: number }) {
  return typeof value === "string" && value ? <StringValue label={label} value={value} depth={depth} /> : <Body label={label} value={value} depth={depth} />;
}

export function SmartValue({ label, value }: { label: string; value: unknown }) {
  const [mode, setMode] = useState<"smart" | "json">("smart");
  return (
    <div className="overflow-hidden rounded-lg border border-[#52525b] bg-[#18181b] shadow-black/40 shadow-sm">
      <div className="flex items-center justify-between border-[#52525b] border-b bg-[#232326] px-3 py-1.5">
        <span className="font-mono font-semibold text-[10px] text-zinc-200 uppercase tracking-wider">{label}</span>
        <div className="flex items-center gap-1 rounded border border-[#3f3f46] bg-[#0f0f11] p-0.5">
          {(["smart", "json"] as const).map((m) => (
            <button key={m} type="button" onClick={() => setMode(m)} className={`rounded px-1.5 py-0.5 font-mono font-semibold text-[9px] ${mode === m ? (m === "smart" ? "bg-amber-500/25 text-amber-200" : "bg-sky-500/25 text-sky-200") : "text-zinc-400 hover:text-zinc-100"}`}>
              {m === "smart" ? "Smart" : "JSON"}
            </button>
          ))}
        </div>
      </div>
      <div className="p-3">{mode === "json" ? <Json data={value} /> : <Value label={label} value={value} depth={0} />}</div>
    </div>
  );
}

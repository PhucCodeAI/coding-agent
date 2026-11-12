import { BotIcon, BoxIcon, ChevronDownIcon, ChevronRightIcon, CogIcon, GitBranchIcon, WrenchIcon, XIcon } from "lucide-react";
import { useState } from "react";
import type { Span } from "@/api";
import { formatDuration } from "@/lib/format";
import { SmartValue } from "./SmartValue";

export const SPAN_TYPES: Record<Span["span_type"], { icon: typeof BotIcon; label: string; color: string }> = {
  llm: { icon: BotIcon, label: "LLM", color: "text-sky-400" },
  tool: { icon: WrenchIcon, label: "Tool", color: "text-amber-400" },
  sandbox: { icon: BoxIcon, label: "Sandbox", color: "text-purple-400" },
  git: { icon: GitBranchIcon, label: "Git", color: "text-emerald-400" },
  internal: { icon: CogIcon, label: "Internal", color: "text-zinc-400" },
};

const statusBadge = (s: Span["status"]) => (s === "OK" ? "bg-emerald-500/10 border-emerald-500/40 text-emerald-400" : "bg-rose-500/10 border-rose-500/40 text-rose-400");

export interface SpanNode {
  span: Span;
  offset: number;
  children: SpanNode[];
}

/**
 * Gom span phẳng thành cây: mỗi "Bước" (span LLM) chứa các công cụ nó gọi; lệnh sandbox nằm trong công cụ chạy nó
 * (khoảng thời gian nằm trọn trong công cụ). Span đứng trước bước đầu tiên giữ ở gốc.
 */
export function groupSpans(spans: Span[]): SpanNode[] {
  // Trace cũ có thể thiếu start_offset_ms (toàn 0) → xếp nối tiếp theo thời lượng.
  const fromBackend = spans.length > 1 && spans.some((s) => s.start_offset_ms > 0);
  let acc = 0;
  const nodes = spans.map((span) => {
    const offset = fromBackend ? span.start_offset_ms : acc;
    acc = offset + span.duration_ms;
    return { span, offset, children: [] as SpanNode[] };
  });
  const roots: SpanNode[] = [];
  let step: SpanNode | null = null;
  for (const n of nodes) {
    if (n.span.span_type === "llm") {
      step = n;
      roots.push(n);
    } else if (step) step.children.push(n);
    else roots.push(n);
  }
  for (const r of roots) {
    const tools = r.children.filter((c) => c.span.span_type === "tool");
    r.children = r.children.filter((c) => {
      if (c.span.span_type === "tool") return true;
      const host = tools.find((t) => t.offset <= c.offset && c.offset + c.span.duration_ms <= t.offset + t.span.duration_ms + 5);
      host?.children.push(c);
      return !host;
    });
  }
  return roots;
}

export const flattenSpans = (nodes: SpanNode[], depth = 0): { n: SpanNode; depth: number }[] => nodes.flatMap((n) => [{ n, depth }, ...flattenSpans(n.children, depth + 1)]);

const endOf = (n: SpanNode): number => Math.max(n.offset + n.span.duration_ms, ...n.children.map(endOf));
const hasError = (n: SpanNode): boolean => n.span.status === "ERROR" || n.children.some(hasError);

interface ViewProps {
  spans: Span[];
  selected: string | null;
  onSelect: (id: string) => void;
}

/** Danh sách bước; bước thu gọn hiện tên các công cụ, mở ra thấy công cụ (và lệnh sandbox) lồng bên trong. */
function useSteps(spans: Span[]) {
  const roots = groupSpans(spans);
  const [open, setOpen] = useState<Set<string>>(new Set());
  const toggle = (id: string) => setOpen((o) => (o.has(id) ? new Set([...o].filter((x) => x !== id)) : new Set([...o, id])));
  const allOpen = roots.every((r) => !r.children.length || open.has(r.span.span_id));
  const setAll = () => setOpen(allOpen ? new Set() : new Set(roots.map((r) => r.span.span_id)));
  return { roots, open, toggle, allOpen, setAll };
}

function ToggleAll({ allOpen, onClick }: { allOpen: boolean; onClick: () => void }) {
  return (
    <div className="flex justify-end px-2 pt-1.5">
      <button type="button" onClick={onClick} className="text-[11px] text-zinc-500 hover:text-zinc-200">
        {allOpen ? "Thu gọn các bước" : "Mở tất cả bước"}
      </button>
    </div>
  );
}

const toolNames = (n: SpanNode) => n.children.map((c) => c.span.name).join(" · ");

export function SpanTree({ spans, selected, onSelect }: ViewProps) {
  const { roots, open, toggle, allOpen, setAll } = useSteps(spans);
  const row = (n: SpanNode, depth: number): React.ReactNode => {
    const s = n.span;
    const t = SPAN_TYPES[s.span_type] ?? SPAN_TYPES.internal;
    const expandable = depth === 0 && n.children.length > 0;
    const isOpen = open.has(s.span_id);
    const err = hasError(n);
    return (
      <div key={s.span_id}>
        <div className={`flex w-full items-center border-l-2 font-mono text-[12px] ${selected === s.span_id ? "border-amber-400 bg-amber-500/15" : "border-transparent hover:bg-[#1c1c20]"}`} style={{ paddingLeft: depth * 22 }}>
          <button type="button" onClick={() => expandable && toggle(s.span_id)} className="flex w-7 shrink-0 justify-center self-stretch items-center text-zinc-600 hover:text-zinc-300">
            {expandable ? isOpen ? <ChevronDownIcon className="size-3.5" /> : <ChevronRightIcon className="size-3.5" /> : depth > 0 && <span className="h-full w-px bg-[#2a2a2e]" />}
          </button>
          <button type="button" onClick={() => onSelect(s.span_id)} className="flex min-w-0 flex-1 items-center gap-2.5 py-2 pr-3 text-left">
            <t.icon className={`size-4 shrink-0 ${t.color}`} />
            <span className={`shrink-0 ${depth === 0 ? "font-semibold text-zinc-100" : "text-zinc-200"}`}>{s.name}</span>
            {expandable && !isOpen && <span className="min-w-0 truncate text-[11px] text-zinc-500">{toolNames(n)}</span>}
            <span className="ml-auto shrink-0 text-zinc-400">{formatDuration(s.duration_ms)}</span>
            <span className={`shrink-0 rounded border px-1.5 py-0.5 font-bold text-[10px] ${statusBadge(err ? "ERROR" : "OK")}`}>{err ? "ERROR" : "OK"}</span>
          </button>
        </div>
        {(depth > 0 || isOpen) && n.children.map((c) => row(c, depth + 1))}
      </div>
    );
  };
  return (
    <div>
      <ToggleAll allOpen={allOpen} onClick={setAll} />
      <div className="divide-y divide-[#27272a]">{roots.map((r) => row(r, 0))}</div>
    </div>
  );
}

export function SpanWaterfall({ spans, total, selected, onSelect }: ViewProps & { total: number }) {
  const { roots, open, toggle, allOpen, setAll } = useSteps(spans);
  const max = total > 0 ? total : Math.max(1, ...roots.map(endOf));
  const pct = (ms: number) => `${(ms / max) * 100}%`;
  const row = (n: SpanNode, depth: number): React.ReactNode => {
    const s = n.span;
    const t = SPAN_TYPES[s.span_type] ?? SPAN_TYPES.internal;
    const expandable = depth === 0 && n.children.length > 0;
    const isOpen = open.has(s.span_id);
    const err = hasError(n);
    const groupEnd = endOf(n);
    return (
      <div key={s.span_id}>
        <div className={`group relative flex h-9 items-center rounded ${selected === s.span_id ? "ring-1 ring-amber-400/60" : ""}`}>
          {expandable && groupEnd > n.offset + s.duration_ms && (
            // cả bước (LLM + công cụ) — nền mờ phía sau thanh LLM
            <div className="pointer-events-none absolute top-1.5 bottom-1.5 rounded-sm bg-sky-500/10" style={{ left: pct(n.offset), width: pct(groupEnd - n.offset) }} />
          )}
          <div
            className={`pointer-events-none absolute top-1.5 bottom-1.5 rounded-sm ${s.status === "ERROR" ? "bg-rose-500/60 group-hover:bg-rose-500/80" : depth === 0 ? "bg-sky-500/55 group-hover:bg-sky-500/75" : s.span_type === "sandbox" ? "bg-purple-500/45 group-hover:bg-purple-500/65" : "bg-amber-500/45 group-hover:bg-amber-500/65"}`}
            style={{ left: pct(n.offset), width: `max(3px, ${pct(s.duration_ms)})` }}
          />
          <button type="button" onClick={() => expandable && toggle(s.span_id)} className="relative z-10 flex w-7 shrink-0 justify-center text-zinc-500 hover:text-zinc-200" style={{ marginLeft: depth * 20 }}>
            {expandable && (isOpen ? <ChevronDownIcon className="size-3.5" /> : <ChevronRightIcon className="size-3.5" />)}
          </button>
          <button type="button" onClick={() => onSelect(s.span_id)} title={`${s.name} · ${s.duration_ms}ms · +${n.offset}ms`} className="relative z-10 flex h-full min-w-0 flex-1 items-center gap-2 pr-2 text-left font-mono text-[11px]">
            <t.icon className={`size-4 shrink-0 ${t.color}`} />
            <span className={`shrink-0 truncate ${depth === 0 ? "font-semibold text-zinc-100" : "text-zinc-200"}`}>{s.name}</span>
            {expandable && !isOpen && <span className="min-w-0 truncate text-zinc-400">{toolNames(n)}</span>}
            <span className="ml-auto flex shrink-0 items-center gap-2 text-zinc-300">
              <span className={`font-semibold ${err ? "text-rose-400" : "text-emerald-400"}`}>{err ? "ERROR" : "OK"}</span>
              <span>{formatDuration(expandable && !isOpen ? groupEnd - n.offset : s.duration_ms)}</span>
            </span>
          </button>
        </div>
        {(depth > 0 || isOpen) && n.children.map((c) => row(c, depth + 1))}
      </div>
    );
  };
  return (
    <div className="p-2">
      <ToggleAll allOpen={allOpen} onClick={setAll} />
      <div className="space-y-0.5">{roots.map((r) => row(r, 0))}</div>
    </div>
  );
}

export function SpanDetail({ span, onClose }: { span: Span; onClose: () => void }) {
  const t = SPAN_TYPES[span.span_type] ?? SPAN_TYPES.internal;
  return (
    <div className="overflow-hidden rounded-lg border border-[#2c2c31] bg-[#18181b]">
      <div className="flex items-center gap-2 border-[#2c2c31] border-b px-3 py-1.5 text-[11px]">
        <t.icon className={`size-3.5 ${t.color}`} />
        <span className="truncate font-mono text-zinc-200">{span.name}</span>
        <span className={`rounded border px-1.5 font-bold text-[10px] ${statusBadge(span.status)}`}>{span.status}</span>
        <span className="text-zinc-500">
          {t.label} · {formatDuration(span.duration_ms)} · +{formatDuration(span.start_offset_ms)}
        </span>
        <button type="button" onClick={onClose} className="ml-auto text-zinc-500 hover:text-zinc-200">
          <XIcon className="size-3.5" />
        </button>
      </div>
      <div className="space-y-3 p-3">
        {span.input_data != null && <SmartValue label="input" value={span.input_data} />}
        {span.output_data != null && <SmartValue label="output" value={span.output_data} />}
        {Object.keys(span.metadata ?? {}).length > 0 && <SmartValue label="metadata" value={span.metadata} />}
      </div>
    </div>
  );
}

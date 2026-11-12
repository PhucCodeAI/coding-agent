import { ChevronDownIcon, ChevronRightIcon, CircleCheckIcon, CircleXIcon, SquareIcon } from "lucide-react";
import { useEffect, useState } from "react";
import { ApiError, api, type ErrorData, type Span, type TraceTurn } from "@/api";
import { ErrorBanner } from "@/components/common";
import { parsePrompt } from "@/lib/commands";
import { absoluteTime, formatCost, formatDuration, formatNumber, relativeTime } from "@/lib/format";
import { SpanDetail, SpanTree, SpanWaterfall } from "./Spans";

export const TURN_STATUS = {
  SUCCESS: { icon: CircleCheckIcon, cls: "text-emerald-400", label: "Thành công", bar: "bg-emerald-500/70 hover:bg-emerald-400" },
  FAILED: { icon: CircleXIcon, cls: "text-rose-400", label: "Thất bại", bar: "bg-rose-500/70 hover:bg-rose-400" },
  CANCELLED: { icon: SquareIcon, cls: "text-zinc-500", label: "Đã dừng", bar: "bg-zinc-600 hover:bg-zinc-500" },
};

export function TurnCard({ turn, open, focused, onToggle }: { turn: TraceTurn; open: boolean; focused: boolean; onToggle: () => void }) {
  const [detail, setDetail] = useState<{ spans: Span[]; duration_ms: number } | null>(null);
  const [error, setError] = useState<ErrorData | null>(null);
  const [view, setView] = useState<"waterfall" | "tree">("waterfall");
  const [selected, setSelected] = useState<string | null>(null);
  const status = TURN_STATUS[turn.status] ?? TURN_STATUS.CANCELLED;
  const prompt = parsePrompt(turn.prompt);
  const span = detail?.spans.find((s) => s.span_id === selected);

  useEffect(() => {
    if (open && !detail && !error)
      api.trace(turn.trace_id).then(setDetail, (e) => setError(e instanceof ApiError ? e.error : { code: "CLIENT_NETWORK_ERROR", title: "Lỗi", message: String(e), hint: "" }));
  }, [open, detail, error, turn.trace_id]);

  return (
    <div id={`turn-${turn.trace_id}`} className={`overflow-hidden rounded-xl border bg-[#141417] ${focused ? "border-amber-500/50 ring-1 ring-amber-500/30" : "border-[#27272a]"}`}>
      <button type="button" onClick={onToggle} aria-expanded={open} className="flex w-full items-center gap-3 px-3 py-2.5 text-left hover:bg-[#19191d]">
        <span className="w-7 shrink-0 text-center font-mono text-[11px] text-zinc-500">#{turn.turn_index}</span>
        <status.icon className={`size-4 shrink-0 ${status.cls}`} strokeWidth={2.5} />
        <span className="flex min-w-0 flex-1 items-center gap-2">
          {prompt.command && <span className="shrink-0 rounded border border-amber-500/25 bg-amber-500/10 px-1.5 py-0.5 font-mono text-[11px] text-amber-300">/{prompt.command}</span>}
          <span className="truncate text-[13px] text-zinc-200">{prompt.text || turn.prompt}</span>
        </span>
        <span className="hidden shrink-0 items-center gap-3 font-mono text-[11.5px] text-zinc-500 md:flex">
          <span>{formatDuration(turn.duration_ms)}</span>
          <span title={`Input: ${formatNumber(turn.input_tokens)}\nOutput: ${formatNumber(turn.output_tokens)}\nReasoning: ${formatNumber(turn.reasoning_tokens)}\nCache: ${formatNumber(turn.cache_tokens)}`}>{formatNumber(turn.total_tokens)} tok</span>
          <span>{formatCost(turn.cost)}</span>
          <span className="w-16 text-right" title={absoluteTime(turn.created_at)}>
            {relativeTime(turn.created_at)}
          </span>
        </span>
        {open ? <ChevronDownIcon className="size-3.5 shrink-0 text-zinc-500" /> : <ChevronRightIcon className="size-3.5 shrink-0 text-zinc-500" />}
      </button>
      {open && (
        <div className="space-y-3 border-[#232327] border-t p-3">
          <div className="flex flex-wrap gap-x-4 gap-y-1 font-mono text-[11.5px] text-zinc-500">
            <span>
              trace <span className="text-zinc-300">{turn.trace_id}</span>
            </span>
            <span className={status.cls}>{status.label}</span>
            <span>
              in {formatNumber(turn.input_tokens)} · out {formatNumber(turn.output_tokens)} · reasoning {formatNumber(turn.reasoning_tokens)} · cache {formatNumber(turn.cache_tokens)}
            </span>
            <span>{absoluteTime(turn.created_at)}</span>
          </div>
          {turn.status === "FAILED" && turn.error_message && (
            <ErrorBanner error={{ code: turn.error_code ?? "SYSTEM_ERROR", title: turn.error_code ?? "Lỗi", message: turn.error_message.replace(/^\[[A-Z0-9_]+\]\s*/, ""), hint: "" }} />
          )}
          {error ? (
            <ErrorBanner error={error} />
          ) : !detail ? (
            <div className="py-4 text-[12px] text-zinc-500">Đang tải spans...</div>
          ) : (
            <>
              <div className="flex items-center justify-between">
                <span className="text-[11px] text-zinc-500 uppercase tracking-wider">Spans ({detail.spans.length})</span>
                <div className="flex items-center gap-0.5 rounded-md border border-[#2c2c31] bg-[#1c1c20] p-0.5">
                  {(["waterfall", "tree"] as const).map((m) => (
                    <button key={m} type="button" onClick={() => setView(m)} className={`rounded px-2 py-0.5 text-[11px] ${view === m ? "bg-[#2a2a30] text-zinc-100" : "text-zinc-500 hover:text-zinc-200"}`}>
                      {m === "waterfall" ? "Waterfall" : "Tree"}
                    </button>
                  ))}
                </div>
              </div>
              <div className="overflow-hidden rounded-lg border border-[#2c2c31] bg-[#18181b]">
                {!detail.spans.length ? (
                  <div className="p-8 text-center text-xs text-zinc-400">Trace không có span nào.</div>
                ) : view === "tree" ? (
                  <SpanTree spans={detail.spans} selected={selected} onSelect={setSelected} />
                ) : (
                  <SpanWaterfall spans={detail.spans} total={detail.duration_ms} selected={selected} onSelect={setSelected} />
                )}
              </div>
              {span && <SpanDetail span={span} onClose={() => setSelected(null)} />}
            </>
          )}
        </div>
      )}
    </div>
  );
}

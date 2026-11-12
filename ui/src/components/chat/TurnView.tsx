import { ActivityIcon, ChevronDownIcon, CircleAlertIcon, CircleCheckIcon, CircleXIcon, FileIcon, GitCommitHorizontalIcon, RefreshCwIcon, SquareIcon } from "lucide-react";
import { useEffect, useState } from "react";
import type { StepData, ToolResult, Turn } from "@/api";
import { ErrorBanner, Markdown } from "@/components/common";
import { commitOf, doneOf, errorOf, finalOf, pendingQuestion, resultsOf, stepsOf } from "@/lib/agent";
import { clock, formatCost, formatDuration, formatNumber, formatTokens } from "@/lib/format";
import { linkHandler } from "@/lib/router";
import { QuestionCard } from "./QuestionCard";
import { ToolRow } from "./ToolRow";

function Step({ step, results, live, switched }: { step: StepData; results: Map<string, ToolResult>; live: boolean; switched: boolean }) {
  const calls = step.tool_calls.filter((c) => c.name !== "finish");
  const finishes = calls.length < step.tool_calls.length;
  return (
    <div className="relative grid gap-1.5 pl-5">
      <span className="absolute top-1.5 left-0 size-2 rounded-full bg-[#3f3f46] ring-4 ring-[#141416]" />
      <div className="flex flex-wrap items-center gap-x-2 text-[11px] text-zinc-500 tabular-nums">
        <span className="font-medium text-zinc-300">Turn {step.step}</span>
        {step.model && <span>· {step.model.split("/").pop()}</span>}
        {switched && (
          <span className="rounded bg-amber-500/15 px-1.5 text-[10px] text-amber-300" title="Model trước lỗi hoặc hết hạn mức nên chuyển sang model dự phòng; mỗi model đếm token theo tokenizer riêng.">
            đổi model
          </span>
        )}
        <span title="Token input (toàn bộ ngữ cảnh gửi lên) và output của lời gọi này">
          · {formatTokens(step.input_tokens)} vào · {formatTokens(step.output_tokens)} ra
        </span>
        <span>· {formatDuration(step.duration_ms)}</span>
      </div>
      {step.content && <Markdown content={step.content} />}
      {finishes && !calls.length && <p className="text-[11.5px] text-zinc-500">Gửi kết quả cuối</p>}
      {calls.map((c) => (
        <ToolRow key={c.id} call={c} result={results.get(c.id)} live={live} />
      ))}
    </div>
  );
}

function useElapsed(since: string, active: boolean): number {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [active]);
  return Math.max(0, now - new Date(since).getTime());
}

export function TurnView({
  turn,
  sessionId,
  isLast,
  onRetry,
  onOpenFile,
  onOpenCommit,
}: {
  turn: Turn;
  sessionId: string | null;
  isLast: boolean;
  onRetry: () => void;
  onOpenFile: (path: string) => void;
  onOpenCommit: (hash: string) => void;
}) {
  const { events, live } = turn;
  const steps = stepsOf(events);
  const results = resultsOf(events);
  const tools = steps.flatMap((s) => s.tool_calls).filter((c) => c.name !== "finish");
  const errors = tools.filter((c) => results.get(c.id)?.ok === false).length;
  const tokens = steps.reduce((n, s) => n + s.input_tokens + s.output_tokens, 0);
  const [open, setOpen] = useState<boolean | null>(null);
  const expanded = open ?? live;
  const final = finalOf(events);
  const error = errorOf(events);
  const done = doneOf(events);
  const commit = commitOf(events);
  const question = live ? pendingQuestion(events) : null;
  const elapsed = useElapsed(turn.at, live);
  const files = [...new Set(events.flatMap((e) => (e.event === "file" && e.data.action !== "delete" ? [e.data.path] : [])))];
  const usage = done && `Input: ${formatNumber(done.input_tokens)}\nOutput: ${formatNumber(done.output_tokens)}\nReasoning: ${formatNumber(done.reasoning_tokens)}\nCache: ${formatNumber(done.cache_tokens)}`;

  return (
    <div className="space-y-3">
      {tools.length > 0 && (
        <div>
          <button type="button" onClick={() => setOpen(!expanded)} className="flex w-full items-center gap-2 py-1 text-left text-[13px] hover:text-zinc-100">
            <ChevronDownIcon className={`size-4 text-zinc-500 transition-transform ${expanded ? "" : "-rotate-90"}`} />
            <span className={`font-medium ${live ? "animate-pulse text-amber-200" : "text-zinc-200"}`}>{live ? "Đang làm việc…" : "Đã làm"}</span>
            <span className="text-[11.5px] text-zinc-500 tabular-nums">
              {steps.length} turn · {tools.length} công cụ · {formatTokens(tokens)} token
            </span>
            {errors > 0 && (
              <span className="flex items-center gap-1 text-[11.5px] text-rose-400">
                <CircleAlertIcon className="size-3.5" />
                {errors} lỗi
              </span>
            )}
          </button>
          {expanded && (
            <div className="relative mt-2 ml-2 grid gap-4 before:absolute before:top-2 before:bottom-2 before:left-[3px] before:w-px before:bg-[#27272a]">
              {steps.map((s, i) => (
                <Step key={s.step} step={s} results={results} live={live} switched={i > 0 && s.model !== steps[i - 1].model} />
              ))}
            </div>
          )}
        </div>
      )}
      {!tools.length && steps.map((s) => s.content && <Markdown key={s.step} content={s.content} />)}
      {live && !steps.length && <div className="animate-pulse text-[13px] text-zinc-400">Agent đang bắt đầu…</div>}
      {question && sessionId && <QuestionCard question={question} sessionId={sessionId} />}

      {final && (
        <div className={`space-y-3 rounded-2xl rounded-tl-md border bg-[#18181c] px-4 py-3 ${final.status === "done" ? "border-[#2a2a30]" : "border-rose-500/30"}`}>
          <div className={`flex items-center gap-1.5 font-medium text-[12px] ${final.status === "done" ? "text-emerald-400" : "text-rose-400"}`}>
            {final.status === "done" ? <CircleCheckIcon className="size-3.5" /> : <CircleXIcon className="size-3.5" />}
            {final.status === "done" ? "Hoàn thành" : "Không hoàn thành"}
          </div>
          <Markdown content={final.text} />
          {(commit || files.length > 0) && (
            <div className="flex flex-wrap gap-1.5">
              {commit && (
                <button type="button" onClick={() => onOpenCommit(commit.hash)} title={commit.message} className="inline-flex items-center gap-1 rounded-md border border-sky-500/25 bg-sky-500/10 px-2 py-0.5 font-mono text-[11.5px] text-sky-300 hover:border-sky-400/50">
                  <GitCommitHorizontalIcon className="size-3" />
                  {commit.hash}
                </button>
              )}
              {files.map((f) => (
                <button key={f} type="button" onClick={() => onOpenFile(f)} className="inline-flex items-center gap-1 rounded-md border border-[#34343a] bg-[#1a1a1e] px-2 py-0.5 font-mono text-[11.5px] text-zinc-300 hover:border-zinc-500">
                  <FileIcon className="size-3 text-zinc-500" />
                  {f}
                </button>
              ))}
            </div>
          )}
        </div>
      )}
      {error && error.code !== final?.code && <ErrorBanner error={error} />}

      {live ? (
        <div className="flex items-center gap-3 text-[11.5px] text-zinc-500">
          <span className="font-mono">{clock(elapsed)}</span>
          {sessionId && (
            <a href={`/traces/${sessionId}`} onClick={linkHandler(`/traces/${sessionId}`)} className="inline-flex items-center gap-1 hover:text-zinc-200" title="Xem trace đang chạy: đang chờ LLM hay công cụ, chờ bao lâu">
              <ActivityIcon className="size-3" />
              Trace live
            </a>
          )}
        </div>
      ) : (
        <div className={`mt-4 flex items-center gap-3 text-[11px] text-zinc-500 transition-opacity ${isLast ? "" : "opacity-70 hover:opacity-100"}`}>
          <span className="h-px flex-1 bg-[#232327]" />
          <span className="flex flex-wrap items-center justify-center gap-1.5">
            {done?.status === "CANCELLED" && (
              <span className="inline-flex items-center gap-1">
                <SquareIcon className="size-3" />
                Đã dừng ·
              </span>
            )}
            {done && <span>{formatDuration(done.duration_ms)}</span>}
            {done && done.total_tokens > 0 && (
              <span className="cursor-help" title={usage || ""}>
                · {formatNumber(done.total_tokens)} tokens · {formatCost(done.cost)}
              </span>
            )}
            {done?.trace_id && sessionId && (
              <>
                ·
                <a href={`/traces/${sessionId}?turn=${done.trace_id}`} onClick={linkHandler(`/traces/${sessionId}?turn=${done.trace_id}`)} className="inline-flex items-center gap-0.5 hover:text-zinc-200">
                  <ActivityIcon className="size-3" />
                  Trace lượt
                </a>
              </>
            )}
            {isLast && (done?.status === "FAILED" || done?.status === "CANCELLED") && (
              <>
                ·
                <button type="button" onClick={onRetry} className="inline-flex items-center gap-0.5 text-amber-300/90 hover:text-amber-200">
                  <RefreshCwIcon className="size-3" />
                  Thử lại
                </button>
              </>
            )}
          </span>
          <span className="h-px flex-1 bg-[#232327]" />
        </div>
      )}
    </div>
  );
}

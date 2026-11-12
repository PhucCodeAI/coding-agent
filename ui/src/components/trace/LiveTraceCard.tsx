import { CheckIcon, TriangleAlertIcon, XIcon } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api, type LiveTrace } from "@/api";
import { clock, formatDuration } from "@/lib/format";
import { flattenSpans, groupSpans } from "./Spans";

const POLL_MS = 2000;
const SLOW_MS = 120_000;
const STUCK_MS = 300_000;

/** Lượt đang chạy của phiên — trace chỉ lưu khi lượt kết thúc nên poll endpoint live. */
export function LiveTraceCard({ sessionId, onFinished }: { sessionId: string; onFinished: () => void }) {
  const [data, setData] = useState<LiveTrace | null>(null);
  const [fetchedAt, setFetchedAt] = useState(Date.now());
  const [now, setNow] = useState(Date.now());
  const wasLive = useRef(false);
  const finished = useRef(onFinished);
  finished.current = onFinished;

  useEffect(() => {
    let alive = true;
    wasLive.current = false;
    setData(null);
    const poll = () =>
      api.liveTrace(sessionId).then(
        (d) => {
          if (!alive) return;
          setData(d);
          setFetchedAt(Date.now());
          if (wasLive.current && !d) finished.current();
          wasLive.current = d !== null;
        },
        () => alive && setData(null),
      );
    void poll();
    const timer = setInterval(poll, POLL_MS);
    const ticker = setInterval(() => setNow(Date.now()), 1000);
    return () => {
      alive = false;
      clearInterval(timer);
      clearInterval(ticker);
    };
  }, [sessionId]);

  if (!data) return null;
  const drift = Math.max(0, now - fetchedAt);
  const tone = (ms: number) => (ms > STUCK_MS ? "rose" : ms > SLOW_MS ? "amber" : "");
  return (
    <section aria-live="polite" className="space-y-3 rounded-xl border border-amber-500/30 bg-amber-500/[0.04] p-3">
      <div className="flex items-center gap-2">
        <span className="size-3.5 shrink-0 animate-spin rounded-full border-2 border-amber-400 border-t-transparent" />
        <span className="font-medium text-[13px] text-amber-200">Lượt đang chạy</span>
        <span className="font-mono text-[12px] text-amber-300/90">{clock(data.elapsed_ms + drift)}</span>
        <span className="ml-auto font-mono text-[11px] text-zinc-500">{data.trace_id}</span>
      </div>
      <p className="line-clamp-2 text-[12.5px] text-zinc-300">{data.prompt}</p>
      <div>
        <div className="mb-1 text-[10.5px] text-zinc-500 uppercase tracking-wider">Đang chờ ({data.running.length})</div>
        {!data.running.length ? (
          <div className="text-[12px] text-zinc-500">Không có lời gọi nào đang chờ (đang xử lý nội bộ / chờ bạn trả lời câu hỏi).</div>
        ) : (
          <ul className="space-y-1">
            {data.running.map((r, i) => {
              const waited = r.elapsed_ms + drift;
              const t = tone(waited);
              return (
                <li key={`${r.name}-${r.start_offset_ms}-${i}`} className={`flex items-center gap-2 rounded-md border bg-[#18181b] px-2 py-1 text-[12.5px] ${t === "rose" ? "border-rose-500/40" : t === "amber" ? "border-amber-500/40" : "border-[#2a2a30]"}`}>
                  <span className="size-3 shrink-0 animate-spin rounded-full border-2 border-amber-400 border-t-transparent" />
                  <span className="truncate text-zinc-200">{r.name}</span>
                  <span className={`ml-auto font-mono ${t === "rose" ? "text-rose-300" : t === "amber" ? "text-amber-300" : "text-zinc-400"}`}>{clock(waited)}</span>
                </li>
              );
            })}
          </ul>
        )}
        {data.running.some((r) => r.elapsed_ms + drift > SLOW_MS) && (
          <p className="mt-1.5 flex items-start gap-1.5 text-[11.5px] text-amber-300/80">
            <TriangleAlertIcon className="mt-0.5 size-3.5 shrink-0" />
            Model phản hồi chậm bất thường (provider quá tải / xếp hàng). Quá deadline sẽ tự thử lại 1 lần rồi báo lỗi LLM_TIMEOUT.
          </p>
        )}
      </div>
      {data.spans.length > 0 && (
        <div>
          <div className="mb-1 text-[10.5px] text-zinc-500 uppercase tracking-wider">Đã xong ({data.spans.length})</div>
          <ul className="max-h-56 space-y-0.5 overflow-y-auto">
            {flattenSpans(groupSpans(data.spans)).map(({ n: { span: s }, depth }) => (
                <li key={s.span_id} className="flex items-center gap-2 py-0.5 pr-2 text-[12px]" style={{ paddingLeft: 8 + depth * 16 }}>
                  {s.status === "ERROR" ? <XIcon className="size-3 shrink-0 text-rose-400" /> : <CheckIcon className="size-3 shrink-0 text-emerald-400" />}
                  <span className={`truncate ${depth ? "text-zinc-400" : "text-zinc-200"}`}>{s.name}</span>
                  <span className="ml-auto font-mono text-zinc-500">{formatDuration(s.duration_ms)}</span>
                </li>
              ))}
          </ul>
        </div>
      )}
    </section>
  );
}

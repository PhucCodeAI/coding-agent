import { ActivityIcon, ArrowLeftIcon, SearchIcon, Trash2Icon } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { ApiError, api, type ErrorData, type TraceSession, type TraceTurn } from "@/api";
import { ErrorBanner, useCopy } from "@/components/common";
import { parsePrompt } from "@/lib/commands";
import { absoluteTime, formatCost, formatDuration, formatNumber, relativeTime, STATUS_DOT } from "@/lib/format";
import { linkHandler, navigate } from "@/lib/router";
import { LiveTraceCard } from "./LiveTraceCard";
import { TURN_STATUS, TurnCard } from "./TurnCard";

const PAGE_SIZE = 30;
const asError = (e: unknown): ErrorData => (e instanceof ApiError ? e.error : { code: "CLIENT_NETWORK_ERROR", title: "Lỗi", message: String(e), hint: "" });
type Detail = TraceSession & { turns: TraceTurn[] };

function SessionList({ items, selectedId, loading, filtered }: { items: TraceSession[]; selectedId: string | null; loading: boolean; filtered: boolean }) {
  if (loading && !items.length)
    return (
      <div className="animate-pulse space-y-2 p-3">
        {[1, 2, 3, 4].map((i) => (
          <div key={i} className="h-14 rounded-lg bg-[#16161a]" />
        ))}
      </div>
    );
  if (!items.length) return <div className="p-6 text-center text-[12px] text-zinc-500">{filtered ? "Không có phiên khớp bộ lọc." : "Chưa có trace nào. Chạy một yêu cầu trong chat để tạo trace."}</div>;
  return (
    <ul className="space-y-0.5 p-1.5">
      {items.map((s) => (
        <li key={s.session_id}>
          <a href={`/traces/${s.session_id}`} onClick={linkHandler(`/traces/${s.session_id}`)} className={`block rounded-lg px-2.5 py-2 ${selectedId === s.session_id ? "bg-[#232328]" : "hover:bg-[#18181c]"}`}>
            <div className="flex items-start gap-2">
              <span className={`mt-1.5 size-1.5 shrink-0 rounded-full ${STATUS_DOT[s.last_status ?? ""] ?? "bg-zinc-700"}`} />
              <span className="line-clamp-2 min-w-0 flex-1 break-words text-[12.5px] text-zinc-200">{s.title || s.session_id}</span>
              <span className="shrink-0 text-[11px] text-zinc-500" title={absoluteTime(s.last_activity_at)}>
                {relativeTime(s.last_activity_at)}
              </span>
            </div>
            <div className="mt-1 flex items-center gap-2 pl-3.5 font-mono text-[11px] text-zinc-500">
              <span>{s.turn_count} lượt</span>
              {s.failed_count > 0 && <span className="text-rose-400/80">✗{s.failed_count}</span>}
              <span>· {formatNumber(s.total_tokens)} tok</span>
              <span>· {formatCost(s.cost)}</span>
            </div>
          </a>
        </li>
      ))}
    </ul>
  );
}

const Stat = ({ label, value, sub, title, valueClass = "text-zinc-100" }: { label: string; value: string; sub?: React.ReactNode; title?: string; valueClass?: string }) => (
  <div className={`rounded-xl border border-[#27272a] bg-[#141417] px-3 py-2 ${title ? "cursor-help" : ""}`} title={title}>
    <div className="text-[11px] text-zinc-500">{label}</div>
    <div className={`font-medium text-[15px] ${valueClass}`}>{value}</div>
    {sub && <div className="font-mono text-[11px] text-zinc-500">{sub}</div>}
  </div>
);

function SessionPanel({ s, focusTurn, onDelete }: { s: Detail; focusTurn: string | null; onDelete: () => void }) {
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [confirming, setConfirming] = useState(false);
  const [copied, copy] = useCopy();
  const total = Math.max(1, s.turns.reduce((n, t) => n + Math.max(t.duration_ms, 1), 0));

  const jumpTo = useCallback((id: string, smooth = true) => {
    setOpen((o) => new Set([...o, id]));
    requestAnimationFrame(() => document.getElementById(`turn-${id}`)?.scrollIntoView({ behavior: smooth ? "smooth" : "auto", block: "start" }));
  }, []);

  useEffect(() => {
    setOpen(new Set());
    setConfirming(false);
    const target = (focusTurn && s.turns.find((t) => t.trace_id === focusTurn)) || s.turns.at(-1);
    if (target) jumpTo(target.trace_id, false);
  }, [s.session_id, focusTurn, s.turns, jumpTo]);

  const toggle = (id: string) => setOpen((o) => (o.has(id) ? new Set([...o].filter((x) => x !== id)) : new Set([...o, id])));

  return (
    <div className="mx-auto max-w-5xl space-y-5 p-5">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1 space-y-1">
          <h2 className="break-words font-semibold text-lg text-zinc-100">{s.title || s.session_id}</h2>
          <div className="flex flex-wrap items-center gap-2 text-[12px] text-zinc-500">
            <button type="button" onClick={() => copy(s.session_id)} className="font-mono hover:text-zinc-300" title="Copy session_id">
              {s.session_id}
              {copied ? " ✓" : ""}
            </button>
            <span>·</span>
            <span title={absoluteTime(s.started_at)}>bắt đầu {relativeTime(s.started_at)}</span>
            <span>·</span>
            <span title={absoluteTime(s.last_activity_at)}>hoạt động {relativeTime(s.last_activity_at)}</span>
          </div>
        </div>
        <a href={`/c/${s.session_id}`} onClick={linkHandler(`/c/${s.session_id}`)} className="inline-flex h-8 shrink-0 items-center gap-1.5 rounded-lg border border-[#2c2c31] px-3 text-[12px] text-zinc-300 hover:bg-[#1f1f24]">
          Mở phiên chat
        </a>
        {confirming ? (
          <span className="inline-flex h-8 shrink-0 items-center gap-1 rounded-lg border border-rose-500/40 px-2 text-[12px]">
            <span className="text-rose-300">Xóa {s.turn_count} trace?</span>
            <button type="button" onClick={onDelete} className="rounded px-1.5 text-rose-300 hover:bg-rose-500/20">
              Xóa
            </button>
            <button type="button" onClick={() => setConfirming(false)} className="rounded px-1.5 text-zinc-400 hover:bg-zinc-700">
              Hủy
            </button>
          </span>
        ) : (
          <button type="button" onClick={() => setConfirming(true)} title="Xóa toàn bộ trace của phiên (không xóa lịch sử chat)" className="flex size-8 shrink-0 items-center justify-center rounded-lg text-zinc-500 hover:bg-[#1f1f24] hover:text-rose-300">
            <Trash2Icon className="size-4" />
          </button>
        )}
      </div>

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Stat
          label="Lượt"
          value={String(s.turn_count)}
          sub={
            <>
              <span className="text-emerald-400">✓{s.success_count}</span> <span className="ml-1 text-rose-400">✗{s.failed_count}</span>
              {s.cancelled_count > 0 && <span className="ml-1 text-zinc-500">■{s.cancelled_count}</span>}
            </>
          }
        />
        <Stat label="Thời gian chạy" value={formatDuration(s.duration_ms)} sub={`TB ${formatDuration(Math.round(s.duration_ms / Math.max(1, s.turn_count)))}/lượt`} />
        <Stat
          label="Tokens"
          value={formatNumber(s.total_tokens)}
          sub={`in ${formatNumber(s.input_tokens)} · out ${formatNumber(s.output_tokens)}`}
          title={`Input: ${formatNumber(s.input_tokens)}\nOutput: ${formatNumber(s.output_tokens)}\nReasoning: ${formatNumber(s.reasoning_tokens)}\nCache: ${formatNumber(s.cache_tokens)}`}
        />
        <Stat label="Chi phí" value={formatCost(s.cost)} valueClass="text-emerald-300" />
      </div>

      <div className="space-y-1.5">
        <div className="flex items-center justify-between text-[11px] text-zinc-500">
          <span className="uppercase tracking-wider">Timeline lượt</span>
          <span className="space-x-2">
            <button type="button" className="hover:text-zinc-300" onClick={() => setOpen(new Set(s.turns.map((t) => t.trace_id)))}>
              Mở tất cả
            </button>
            <button type="button" className="hover:text-zinc-300" onClick={() => setOpen(new Set())}>
              Thu gọn
            </button>
          </span>
        </div>
        <div className="flex h-3 gap-0.5 overflow-hidden rounded">
          {s.turns.map((t) => {
            const p = parsePrompt(t.prompt);
            return (
              <button
                key={t.trace_id}
                type="button"
                onClick={() => jumpTo(t.trace_id)}
                aria-label={`Lượt ${t.turn_index}`}
                title={`#${t.turn_index} ${p.command ? `/${p.command} ` : ""}${p.text}\n${t.status} · ${formatDuration(t.duration_ms)}`}
                className={`h-full min-w-1.5 transition-colors ${(TURN_STATUS[t.status] ?? TURN_STATUS.CANCELLED).bar} ${focusTurn === t.trace_id ? "ring-2 ring-amber-400" : ""}`}
                style={{ flex: `${Math.max(t.duration_ms, 1) / total} 1 0%` }}
              />
            );
          })}
        </div>
      </div>

      <div className="space-y-2">
        {s.turns.map((t) => (
          <TurnCard key={t.trace_id} turn={t} open={open.has(t.trace_id)} focused={focusTurn === t.trace_id} onToggle={() => toggle(t.trace_id)} />
        ))}
      </div>
    </div>
  );
}

export function TracePage({ sessionId, focusTurn }: { sessionId: string | null; focusTurn: string | null }) {
  const [input, setInput] = useState("");
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const [list, setList] = useState<{ items: TraceSession[]; total: number }>({ items: [], total: 0 });
  const [listLoading, setListLoading] = useState(true);
  const [listError, setListError] = useState<ErrorData | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<ErrorData | null>(null);

  useEffect(() => {
    const t = setTimeout(() => (setQuery(input.trim()), setPage(1)), 300);
    return () => clearTimeout(t);
  }, [input]);

  const loadList = useCallback(() => {
    setListLoading(true);
    api
      .traceSessions({ q: query, status, limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE })
      .then((r) => (setList(r), setListError(null)), (e) => setListError(asError(e)))
      .finally(() => setListLoading(false));
  }, [query, status, page]);
  useEffect(loadList, [loadList]);

  const loadDetail = useCallback((id: string) => {
    setDetailLoading(true);
    api
      .traceSession(id)
      .then((d) => (setDetail(d), setDetailError(null)), (e) => (setDetail(null), setDetailError(asError(e))))
      .finally(() => setDetailLoading(false));
  }, []);
  useEffect(() => {
    setDetail(null);
    setDetailError(null);
    if (sessionId) loadDetail(sessionId);
  }, [sessionId, loadDetail]);

  const remove = async () => {
    if (!sessionId) return;
    try {
      await api.deleteTraceSession(sessionId);
      navigate("/traces");
      loadList();
    } catch (e) {
      setDetailError(asError(e));
    }
  };

  const pages = Math.max(1, Math.ceil(list.total / PAGE_SIZE));
  const noTraceYet = detailError?.code === "TRACE_NOT_FOUND";

  return (
    <div className="flex h-dvh flex-col bg-[#121214] text-zinc-300">
      <header className="flex h-12 shrink-0 items-center gap-3 border-[#232327] border-b px-4">
        <a href="/" onClick={linkHandler("/")} className="inline-flex items-center gap-1.5 text-[12.5px] text-zinc-400 hover:text-zinc-100">
          <ArrowLeftIcon className="size-3.5" />
          Về Chat
        </a>
        <span className="h-4 w-px bg-[#2a2a2e]" />
        <ActivityIcon className="size-4 text-amber-400" />
        <span className="font-semibold text-[13.5px] text-zinc-100">Trace Explorer</span>
        <span className="hidden truncate text-[12px] text-zinc-500 sm:inline">· mỗi phiên là một trace, mỗi lượt chat có spans riêng (LLM, công cụ, sandbox, git)</span>
      </header>
      <div className="flex min-h-0 flex-1">
        <aside className={`${sessionId ? "hidden md:flex" : "flex"} w-full shrink-0 flex-col border-[#232327] border-r bg-[#0d0d0f] md:w-80`}>
          <div className="space-y-2 border-[#232327] border-b p-2">
            <label className="flex items-center gap-2 rounded-lg border border-[#303036] bg-[#1c1c20] px-2.5 py-1.5 focus-within:border-zinc-500">
              <SearchIcon className="size-3.5 text-zinc-500" />
              <input type="search" value={input} onChange={(e) => setInput(e.target.value)} placeholder="Tìm theo tiêu đề hoặc session_id..." className="min-w-0 flex-1 bg-transparent text-[12px] text-zinc-200 placeholder-zinc-500 focus:outline-none" />
            </label>
            <select value={status} onChange={(e) => (setStatus(e.target.value), setPage(1))} className="w-full rounded-lg border border-[#303036] bg-[#1c1c20] px-2 py-1.5 text-[12px] text-zinc-300 focus:outline-none">
              <option value="">Mọi trạng thái (lượt cuối)</option>
              <option value="SUCCESS">Lượt cuối thành công</option>
              <option value="FAILED">Lượt cuối thất bại</option>
              <option value="CANCELLED">Lượt cuối đã dừng</option>
            </select>
          </div>
          {listError && (
            <div className="p-2">
              <ErrorBanner error={listError} />
            </div>
          )}
          <div className="min-h-0 flex-1 overflow-y-auto">
            <SessionList items={list.items} selectedId={sessionId} loading={listLoading} filtered={Boolean(query || status)} />
          </div>
          {list.total > PAGE_SIZE ? (
            <div className="flex shrink-0 items-center justify-between border-[#232327] border-t px-3 py-2 text-[11px] text-zinc-500">
              <button type="button" onClick={() => setPage(page - 1)} disabled={page <= 1} className="rounded px-2 py-0.5 hover:bg-[#1f1f24] disabled:opacity-40">
                ‹ Trước
              </button>
              <span>
                {page}/{pages} · {list.total} phiên
              </span>
              <button type="button" onClick={() => setPage(page + 1)} disabled={page >= pages} className="rounded px-2 py-0.5 hover:bg-[#1f1f24] disabled:opacity-40">
                Sau ›
              </button>
            </div>
          ) : (
            list.total > 0 && <div className="shrink-0 border-[#232327] border-t px-3 py-2 text-[11px] text-zinc-500">{list.total} phiên</div>
          )}
        </aside>
        <main className={`${sessionId ? "block" : "hidden md:block"} min-w-0 flex-1 overflow-y-auto bg-[#0f0f11]`}>
          {sessionId && (
            <>
              <div className="px-5 pt-3 md:hidden">
                <a href="/traces" onClick={linkHandler("/traces")} className="text-[12px] text-zinc-400 hover:text-zinc-200">
                  ‹ Danh sách phiên
                </a>
              </div>
              <div className="mx-auto max-w-5xl px-5 pt-5 empty:hidden">
                <LiveTraceCard sessionId={sessionId} onFinished={() => (loadDetail(sessionId), loadList())} />
              </div>
            </>
          )}
          {!sessionId ? (
            <div className="flex h-full flex-col items-center justify-center space-y-2 px-6 text-center text-zinc-500">
              <ActivityIcon className="size-8 text-zinc-600" />
              <p className="text-sm text-zinc-300">Chọn một phiên ở bên trái</p>
              <p className="max-w-md text-[12px]">Mỗi phiên là một trace; mỗi lượt chat là một phần của trace với spans riêng (LLM, test, git).</p>
            </div>
          ) : detailLoading && !detail ? (
            <div className="animate-pulse space-y-3 p-6">
              <div className="h-7 w-1/2 rounded bg-[#1c1c20]" />
              <div className="h-20 rounded-xl bg-[#18181b]" />
              <div className="h-12 rounded-xl bg-[#18181b]" />
            </div>
          ) : noTraceYet ? (
            <div className="p-6 text-center text-[12.5px] text-zinc-500">Phiên chưa có lượt nào kết thúc — trace lưu khi lượt chạy xong.</div>
          ) : detailError ? (
            <div className="m-6">
              <ErrorBanner error={detailError} />
            </div>
          ) : (
            detail && <SessionPanel s={detail} focusTurn={focusTurn} onDelete={remove} />
          )}
        </main>
      </div>
    </div>
  );
}

import { type AgentEvent, runStream, type Turn } from "@/api";

/** Lượt đang stream của 1 phiên — sống ở cấp module nên vẫn chạy khi chuyển phiên hay rời trang chat. */
interface LiveStream {
  turns: Turn[];
  controller: AbortController;
}

export type StreamChange = { kind: "update" | "end"; key: string; turns: Turn[]; event?: AgentEvent } | { kind: "rename"; from: string; to: string };

const streams = new Map<string, LiveStream>();
const listeners = new Set<(c: StreamChange) => void>();
const emit = (c: StreamChange) => {
  for (const l of listeners) l(c);
};

export const liveTurns = (key: string) => streams.get(key)?.turns ?? null;
export const liveSessionIds = () => [...streams.keys()].filter((k) => !k.startsWith("pending:"));
export const stopStream = (key: string | null) => key && streams.get(key)?.controller.abort();

export function subscribeStreams(fn: (c: StreamChange) => void): () => void {
  listeners.add(fn);
  return () => void listeners.delete(fn);
}

/**
 * Gửi 1 lượt và stream sự kiện vào store.
 *
 * `key` là mã phiên, hoặc `pending:...` cho phiên mới — đổi sang mã thật khi nhận event `session`.
 */
export async function startStream(key: string, sessionId: string | null, history: Turn[], prompt: string): Promise<void> {
  const s: LiveStream = { turns: [...history, { prompt, events: [], live: true, at: new Date().toISOString() }], controller: new AbortController() };
  streams.set(key, s);
  const push = (e: AgentEvent) => {
    s.turns = s.turns.map((t, i) => (i === s.turns.length - 1 ? { ...t, events: [...t.events, e] } : t));
    if (e.event === "session" && e.data.session_id !== key) {
      streams.delete(key);
      emit({ kind: "rename", from: key, to: e.data.session_id });
      key = e.data.session_id;
      streams.set(key, s);
    }
    emit({ kind: "update", key, turns: s.turns, event: e });
  };
  emit({ kind: "update", key, turns: s.turns });
  try {
    await runStream(prompt, sessionId, push, s.controller.signal);
  } catch (e) {
    if (!s.controller.signal.aborted)
      push({ event: "error", data: { code: "CLIENT_STREAM_ERROR", title: "Mất kết nối stream", message: String(e), hint: "Kiểm tra backend rồi thử lại.", retryable: true } });
    else push({ event: "done", data: { status: "CANCELLED", duration_ms: 0, total_tokens: 0, input_tokens: 0, output_tokens: 0, reasoning_tokens: 0, cache_tokens: 0, cost: 0, trace_id: "" } });
  } finally {
    s.turns = s.turns.map((t, i) => (i === s.turns.length - 1 ? { ...t, live: false } : t));
    streams.delete(key);
    emit({ kind: "end", key, turns: s.turns });
  }
}

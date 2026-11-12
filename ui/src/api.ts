export const API_URL = `${window.location.origin}/api`;

export interface ToolCall {
  id: string;
  name: string;
  args: Record<string, unknown>;
}

export interface StepData {
  step: number;
  model: string;
  provider: string;
  input_tokens: number;
  output_tokens: number;
  reasoning_tokens: number;
  duration_ms: number;
  content: string;
  tool_calls: ToolCall[];
}

export interface ToolResult {
  id: string;
  name: string;
  ok: boolean;
  output: string;
  duration_ms: number;
}

export interface SandboxRun {
  command: string[];
  exit_code: number;
  duration_ms: number;
  is_timeout: boolean;
  stdout_summary: string;
}

export interface QuestionData {
  request_id: string;
  timeout_sec: number;
  questions: { id: string; question: string; other_placeholder: string; options: { label: string; description: string; recommended: boolean }[] }[];
}

export interface ErrorData {
  code: string;
  title: string;
  message: string;
  hint: string;
  retryable?: boolean;
  detail?: string | null;
}

export interface DoneData {
  status: "SUCCESS" | "FAILED" | "CANCELLED";
  duration_ms: number;
  total_tokens: number;
  input_tokens: number;
  output_tokens: number;
  reasoning_tokens: number;
  cache_tokens: number;
  cost: number;
  trace_id: string;
}

export type AgentEvent =
  | { event: "session"; data: { session_id: string; title: string } }
  | { event: "step"; data: StepData }
  | { event: "tool"; data: ToolResult }
  | { event: "file"; data: { path: string; action: "create" | "update" | "delete"; lines_added: number; lines_removed: number } }
  | { event: "sandbox"; data: SandboxRun }
  | { event: "commit"; data: { hash: string; message: string; files: string[] } }
  | { event: "question"; data: QuestionData }
  | { event: "answer"; data: { request_id: string; timed_out: boolean } }
  | { event: "final"; data: { status: "done" | "failed"; text: string; code: string | null } }
  | { event: "error"; data: ErrorData }
  | { event: "done"; data: DoneData };

export interface Turn {
  prompt: string;
  events: AgentEvent[];
  live: boolean;
  at: string;
}

export interface SessionSummary {
  session_id: string;
  title: string;
  last_status: string | null;
  updated_at: string;
}

export interface SlashCommand {
  name: string;
  title: string;
  description: string;
  mode: "code" | "chat";
  arg_hint: string;
}

export interface Commit {
  hash: string;
  message: string;
  date: string;
  files: string[];
}

export interface FileContent {
  path: string;
  content: string;
  size: number;
  truncated: boolean;
  binary: boolean;
}

export interface ExecResult {
  exit_code: number;
  stdout: string;
  stderr: string;
  duration_ms: number;
  is_timeout: boolean;
}

export interface Span {
  span_id: string;
  name: string;
  span_type: "llm" | "tool" | "sandbox" | "git" | "internal";
  duration_ms: number;
  start_offset_ms: number;
  status: "OK" | "ERROR";
  input_data: unknown;
  output_data: unknown;
  metadata: Record<string, unknown>;
}

export interface TraceTurn {
  trace_id: string;
  session_id: string;
  turn_index: number;
  prompt: string;
  status: DoneData["status"];
  duration_ms: number;
  total_tokens: number;
  input_tokens: number;
  output_tokens: number;
  reasoning_tokens: number;
  cache_tokens: number;
  cost: number;
  error_message: string | null;
  error_code: string | null;
  created_at: string;
}

export interface TraceSession {
  session_id: string;
  title: string;
  turn_count: number;
  success_count: number;
  failed_count: number;
  cancelled_count: number;
  last_status: DoneData["status"] | null;
  duration_ms: number;
  total_tokens: number;
  input_tokens: number;
  output_tokens: number;
  reasoning_tokens: number;
  cache_tokens: number;
  cost: number;
  started_at: string;
  last_activity_at: string;
}

export interface LiveTrace {
  trace_id: string;
  prompt: string;
  elapsed_ms: number;
  spans: Span[];
  running: { name: string; span_type: string; start_offset_ms: number; elapsed_ms: number }[];
}

export class ApiError extends Error {
  constructor(public error: ErrorData) {
    super(error.message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, { headers: { "content-type": "application/json" }, ...init });
  } catch (e) {
    throw new ApiError({ code: "CLIENT_NETWORK_ERROR", title: "Không kết nối được backend", message: String(e), hint: "Kiểm tra backend đang chạy." });
  }
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new ApiError(body?.detail?.code ? body.detail : { code: `HTTP_${res.status}`, title: `Lỗi HTTP ${res.status}`, message: res.statusText, hint: "" });
  }
  return (res.status === 204 ? undefined : await res.json()) as T;
}

const q = encodeURIComponent;

export const api = {
  sessions: () => request<SessionSummary[]>("/sessions?limit=200"),
  renameSession: (id: string, title: string) => request<SessionSummary>(`/sessions/${id}`, { method: "PATCH", body: JSON.stringify({ title }) }),
  deleteSession: (id: string) => request<void>(`/sessions/${id}`, { method: "DELETE" }),
  async turns(id: string): Promise<Turn[]> {
    const detail = await request<{ messages: { role: string; content: string; events: AgentEvent[]; created_at: string }[] }>(`/sessions/${id}`);
    const turns: Turn[] = [];
    for (const m of detail.messages) {
      if (m.role === "user") turns.push({ prompt: m.content, events: [], live: false, at: m.created_at });
      else if (turns.length) turns[turns.length - 1].events = m.events;
    }
    return turns;
  },
  files: (id: string) => request<{ path: string; size: number }[]>(`/sessions/${id}/files`),
  file: (id: string, path: string) => request<FileContent>(`/sessions/${id}/files/content?path=${q(path)}`),
  writeFile: (id: string, path: string, content: string) => request<{ path: string; size: number }>(`/sessions/${id}/files/content`, { method: "PUT", body: JSON.stringify({ path, content }) }),
  exec: (id: string, command: string) => request<ExecResult>(`/sessions/${id}/exec`, { method: "POST", body: JSON.stringify({ command }) }),
  commits: (id: string) => request<{ commits: Commit[] }>(`/sessions/${id}/commits`),
  changes: (id: string) => request<{ files: { path: string; status: "added" | "modified" | "deleted" }[]; diff: string; truncated: boolean }>(`/sessions/${id}/changes`),
  commitDiff: (id: string, hash: string) => request<{ diff: string; truncated: boolean }>(`/sessions/${id}/commits/${hash}/diff`),
  commands: () => request<SlashCommand[]>("/chat/commands"),
  answer: (sessionId: string, requestId: string, questionId: string, answer: { choice?: string; text?: string }) =>
    request<void>("/chat/answers", { method: "POST", body: JSON.stringify({ session_id: sessionId, request_id: requestId, answers: [{ question_id: questionId, ...answer }] }) }),
  traceSessions: (params: { q: string; status: string; limit: number; offset: number }) =>
    request<{ items: TraceSession[]; total: number }>(`/traces/sessions?q=${q(params.q)}&status=${params.status}&limit=${params.limit}&offset=${params.offset}`),
  traceSession: (id: string) => request<TraceSession & { turns: TraceTurn[] }>(`/traces/sessions/${id}`),
  deleteTraceSession: (id: string) => request<void>(`/traces/sessions/${id}`, { method: "DELETE" }),
  trace: (traceId: string) => request<{ spans: Span[]; duration_ms: number }>(`/traces/${traceId}`),
  liveTrace: (id: string) => request<LiveTrace | null>(`/traces/sessions/${id}/live`),
};

export async function runStream(prompt: string, sessionId: string | null, onEvent: (e: AgentEvent) => void, signal: AbortSignal): Promise<void> {
  const res = await fetch(`${API_URL}/chat/run`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ prompt, session_id: sessionId }), signal });
  if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return;
    buffer += value;
    let end: number;
    while ((end = buffer.indexOf("\n\n")) >= 0) {
      const block = buffer.slice(0, end);
      buffer = buffer.slice(end + 2);
      const event = block.match(/^event: (.+)$/m)?.[1];
      const data = [...block.matchAll(/^data: (.*)$/gm)].map((m) => m[1]).join("\n");
      if (event && data) onEvent({ event, data: JSON.parse(data) } as AgentEvent);
    }
  }
}

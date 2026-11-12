import type { AgentEvent, DoneData, ErrorData, QuestionData, SandboxRun, StepData, ToolCall, ToolResult, Turn } from "@/api";

export type ToolStatus = "running" | "done" | "error" | "unknown";

type DataOf<K extends AgentEvent["event"]> = Extract<AgentEvent, { event: K }>["data"];

function pick<K extends AgentEvent["event"]>(events: AgentEvent[], name: K): DataOf<K>[] {
  return events.flatMap((e) => (e.event === name ? [e.data as DataOf<K>] : []));
}

export const stepsOf = (events: AgentEvent[]): StepData[] => pick(events, "step");
export const finalOf = (events: AgentEvent[]) => pick(events, "final").at(-1) ?? null;
export const errorOf = (events: AgentEvent[]): ErrorData | null => pick(events, "error").at(-1) ?? null;
export const doneOf = (events: AgentEvent[]): DoneData | null => pick(events, "done").at(-1) ?? null;
export const commitOf = (events: AgentEvent[]) => pick(events, "commit").at(-1) ?? null;
export const sandboxRuns = (turns: Turn[]): SandboxRun[] => turns.flatMap((t) => pick(t.events, "sandbox"));
export const hasCodeActivity = (turns: Turn[]) => turns.some((t) => t.events.some((e) => e.event === "file" || e.event === "commit" || e.event === "sandbox"));

export function resultsOf(events: AgentEvent[]): Map<string, ToolResult> {
  return new Map(pick(events, "tool").map((t) => [t.id, t]));
}

export function pendingQuestion(events: AgentEvent[]): QuestionData | null {
  const answered = new Set(pick(events, "answer").map((a) => a.request_id));
  return pick(events, "question").find((q) => !answered.has(q.request_id)) ?? null;
}

export function toolStatus(result: ToolResult | undefined, live: boolean): ToolStatus {
  if (!result) return live ? "running" : "unknown";
  return result.ok ? "done" : "error";
}

export function toolTarget(call: ToolCall): string {
  const a = call.args;
  const value = a.path ?? a.path_prefix ?? a.pattern ?? a.query ?? a.argv ?? a.packages ?? a.paths;
  return Array.isArray(value) ? value.join(" ") : String(value ?? "");
}

export function diffOf(oldText: string, newText: string): string {
  const lines = (text: string, sign: string) => text.split("\n").map((l) => `${sign} ${l}`);
  return [...lines(oldText, "-"), ...lines(newText, "+")].join("\n");
}

export interface Usage {
  turns: number;
  contextTokens: number;
  inputTokens: number;
  outputTokens: number;
  totalTokens: number;
  cost: number;
  model?: string;
}

export function usageOf(turns: Turn[]): Usage {
  const usage: Usage = { turns: 0, contextTokens: 0, inputTokens: 0, outputTokens: 0, totalTokens: 0, cost: 0 };
  for (const t of turns) {
    for (const s of stepsOf(t.events)) {
      usage.turns += 1;
      usage.model = s.model || usage.model;
      usage.totalTokens += s.input_tokens + s.output_tokens;
      usage.inputTokens = s.input_tokens;
      usage.outputTokens = s.output_tokens;
      usage.contextTokens = s.input_tokens + s.output_tokens;
    }
    usage.cost += doneOf(t.events)?.cost ?? 0;
  }
  return usage;
}

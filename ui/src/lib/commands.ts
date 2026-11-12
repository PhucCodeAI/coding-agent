import { ActivityIcon, CheckIcon, CodeIcon, FileIcon, FolderIcon, type LucideIcon, RefreshCwIcon, SearchIcon, SparklesIcon, TriangleAlertIcon } from "lucide-react";

export const COMMAND_ICONS: Record<string, LucideIcon> = {
  test: CheckIcon,
  fix: TriangleAlertIcon,
  refactor: RefreshCwIcon,
  api: CodeIcon,
  sql: FolderIcon,
  perf: ActivityIcon,
  docs: FileIcon,
  explain: SparklesIcon,
  review: SearchIcon,
};

export interface ParsedPrompt {
  command: string | null;
  text: string;
}

export function parsePrompt(prompt: string): ParsedPrompt {
  const m = prompt.match(/^\s*\/([a-z][a-z0-9_-]*)\b\s*/);
  return m ? { command: m[1], text: prompt.slice(m[0].length) } : { command: null, text: prompt };
}

export function mentionsOf(text: string): string[] {
  return [...new Set([...text.matchAll(/(?:^|\s)@([\w./-]+)/g)].map((m) => m[1].replace(/[.,]$/, "")))];
}

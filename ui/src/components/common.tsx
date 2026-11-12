import { CheckIcon, CopyIcon } from "lucide-react";
import { isValidElement, type ReactNode, useState } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import type { ErrorData } from "@/api";
import { highlight } from "@/lib/highlight";

export function useCopy(): [boolean, (text: string) => void] {
  const [copied, setCopied] = useState(false);
  const copy = (text: string) => {
    void navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };
  return [copied, copy];
}

export function CopyButton({ text, label = true, className = "" }: { text: string; label?: boolean; className?: string }) {
  const [copied, copy] = useCopy();
  return (
    <button
      type="button"
      onClick={() => copy(text)}
      className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10.5px] text-zinc-400 hover:bg-[#2b2b30] hover:text-zinc-100 ${className}`}
    >
      {copied ? <CheckIcon className="size-3.5 text-emerald-400" /> : <CopyIcon className="size-3.5" />}
      {label && (copied ? "Đã copy" : "Copy")}
    </button>
  );
}

export function ErrorBanner({ error }: { error: ErrorData | string }) {
  const e = typeof error === "string" ? { code: "CLIENT_ERROR", title: "Lỗi", message: error, hint: "" } : error;
  return (
    <div role="alert" className="flex w-full items-start gap-3 rounded-xl border border-rose-500/40 bg-rose-500/10 p-3">
      <span className="shrink-0 text-lg text-rose-400">⚠</span>
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-semibold text-rose-300 text-xs">{e.title || e.code}</span>
          {e.retryable && <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] text-amber-300">Có thể thử lại</span>}
        </div>
        <div className="text-xs text-zinc-300">{e.message}</div>
        {e.hint && (
          <div className="text-[11px] text-zinc-400">
            <span className="text-zinc-500">Cách xử lý:</span> {e.hint}
          </div>
        )}
        {e.detail && (
          <details className="font-mono text-[10px] text-zinc-500">
            <summary className="cursor-pointer select-none">Mã lỗi {e.code}</summary>
            <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap break-all">{e.detail}</pre>
          </details>
        )}
      </div>
    </div>
  );
}

export function CodeLines({ code, className = "" }: { code: string; className?: string }) {
  const lines = code.split("\n").length;
  return (
    <div className={`flex font-mono text-[12px] leading-5 ${className}`}>
      <div className="shrink-0 select-none border-[#202024] border-r px-3 text-right text-[11px] text-zinc-600">
        {Array.from({ length: lines }, (_, i) => (
          <div key={i}>{i + 1}</div>
        ))}
      </div>
      <pre className="whitespace-pre pr-4 pl-3.5 text-zinc-200">
        <code>{highlight(code)}</code>
      </pre>
    </div>
  );
}

export function CodeCard({ code, lang = "code", title }: { code: string; lang?: string; title?: string }) {
  return (
    <div className="overflow-hidden rounded-lg border border-[#2a2a2e] bg-[#0f0f11] font-mono shadow-xl">
      <div className="flex select-none items-center justify-between border-[#242428] border-b bg-[#161618] px-3 py-1.5 text-[11px] text-zinc-400">
        <div className="flex min-w-0 items-center gap-2">
          <span className="size-2 shrink-0 rounded-full bg-zinc-600" />
          <span className="truncate font-semibold text-zinc-300 tracking-wider">{title ?? lang}</span>
        </div>
        <CopyButton text={code} />
      </div>
      <div className="max-h-96 overflow-auto py-2.5">
        <CodeLines code={code} className="min-w-max" />
      </div>
    </div>
  );
}

// Khối ``` do `pre` lo (thành CodeCard); `code` ở đây chỉ còn là code inline.
const MD: Components = {
  h1: ({ children }) => <h1 className="mt-4 mb-2 font-bold text-base text-zinc-100">{children}</h1>,
  h2: ({ children }) => <h2 className="mt-4 mb-1.5 font-semibold text-sm text-zinc-100">{children}</h2>,
  h3: ({ children }) => <h3 className="mt-3 mb-1 font-mono font-semibold text-xs text-zinc-200 uppercase tracking-wide">{children}</h3>,
  h4: ({ children }) => <h4 className="mt-3 mb-1 font-semibold text-[13px] text-zinc-200">{children}</h4>,
  p: ({ children }) => <p className="my-2 leading-relaxed">{children}</p>,
  ul: ({ children }) => <ul className="my-2 ml-5 list-disc space-y-0.5 marker:text-zinc-500">{children}</ul>,
  ol: ({ children }) => <ol className="my-2 ml-5 list-decimal space-y-0.5 marker:font-mono marker:text-amber-400">{children}</ol>,
  li: ({ children }) => <li className="pl-0.5 text-zinc-300">{children}</li>,
  strong: ({ children }) => <strong className="font-semibold text-zinc-100">{children}</strong>,
  em: ({ children }) => <em className="text-zinc-300 italic">{children}</em>,
  del: ({ children }) => <del className="text-zinc-500">{children}</del>,
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noreferrer" className="text-sky-400 underline decoration-sky-400/40 underline-offset-2 hover:decoration-sky-400">
      {children}
    </a>
  ),
  blockquote: ({ children }) => <blockquote className="my-2 border-amber-500/50 border-l-2 bg-amber-500/5 py-1 pl-3 text-zinc-300">{children}</blockquote>,
  hr: () => <hr className="my-4 border-[#27272a]" />,
  table: ({ children }) => (
    <div className="my-3 overflow-x-auto rounded-lg border border-[#27272a]">
      <table className="w-full border-collapse text-[13px]">{children}</table>
    </div>
  ),
  thead: ({ children }) => <thead className="bg-[#18181b] text-zinc-200">{children}</thead>,
  th: ({ children, style }) => (
    <th style={style} className="border-[#27272a] border-b px-3 py-1.5 text-left font-semibold">
      {children}
    </th>
  ),
  td: ({ children, style }) => (
    <td style={style} className="border-[#202024] border-t px-3 py-1.5 align-top text-zinc-300">
      {children}
    </td>
  ),
  input: ({ checked }) => <input type="checkbox" checked={checked} readOnly className="mr-1.5 accent-emerald-500" />,
  pre: ({ children }) => {
    const el = isValidElement<{ className?: string; children?: ReactNode }>(children) ? children : null;
    const lang = /language-(\S+)/.exec(el?.props.className ?? "")?.[1];
    return <CodeCard code={String(el?.props.children ?? "").replace(/\n$/, "")} lang={lang ?? "code"} />;
  },
  code: ({ children }) => <code className="rounded border border-[#2e2e33] bg-[#202023] px-1.5 py-0.5 font-mono text-[12.5px] text-amber-300">{children}</code>,
};

export function Markdown({ content }: { content: string }) {
  return (
    <div className="min-w-0 select-text wrap-break-word text-sm text-zinc-200 leading-6 [&>*:first-child]:mt-0 [&>*:last-child]:mb-0">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={MD}>
        {content}
      </ReactMarkdown>
    </div>
  );
}

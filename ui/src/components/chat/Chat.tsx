import { ArrowDownIcon, BugIcon, FlaskConicalIcon, RefreshCwIcon, SparklesIcon } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { SlashCommand, Turn } from "@/api";
import { type ComposerHandle, Composer } from "./Composer";
import { TurnView } from "./TurnView";
import { UserBubble } from "./UserBubble";

const SUGGESTIONS = [
  { icon: SparklesIcon, title: "Viết hàm mới", text: "Viết hàm is_prime(n) trong math_utils.py kèm test pytest" },
  { icon: FlaskConicalIcon, title: "Viết test", text: "/test " },
  { icon: BugIcon, title: "Sửa lỗi", text: "/fix " },
  { icon: RefreshCwIcon, title: "Refactor", text: "/refactor " },
];

export interface ChatProps {
  sessionId: string | null;
  turns: Turn[];
  live: boolean;
  commands: SlashCommand[];
  files: string[];
  onSend: (prompt: string) => void;
  onStop: () => void;
  onOpenFile: (path: string) => void;
  onOpenCommit: (hash: string) => void;
}

export function Chat({ sessionId, turns, live, commands, files, onSend, onStop, onOpenFile, onOpenCommit }: ChatProps) {
  const composer = useRef<ComposerHandle>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const [atBottom, setAtBottom] = useState(true);
  const stick = useRef(true);

  const onScroll = () => {
    const el = scroller.current;
    if (!el) return;
    stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    setAtBottom(stick.current);
  };
  const toBottom = (smooth = false) => scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: smooth ? "smooth" : "auto" });

  useLayoutEffect(() => {
    if (stick.current) toBottom();
  });
  useEffect(() => {
    stick.current = true;
    toBottom();
  }, [sessionId]);

  const composerEl = <Composer ref={composer} commands={commands} files={files} live={live} autofocus menuBelow={!turns.length} onSend={(p) => ((stick.current = true), onSend(p))} onStop={onStop} />;

  if (!turns.length)
    return (
      <div className="flex min-h-0 flex-1 flex-col items-center justify-center overflow-y-auto px-4">
        <div className="w-full max-w-2xl space-y-6">
          <div className="space-y-2 text-center">
            <h2 className="font-semibold text-3xl text-zinc-100 tracking-tight">Hôm nay mình code gì?</h2>
            <p className="text-sm text-zinc-500">Agent đọc, sửa code và chạy test trong sandbox riêng của phiên — mỗi lượt có commit để xem lại.</p>
          </div>
          {composerEl}
          <div className="grid grid-cols-2 gap-2">
            {SUGGESTIONS.map((s) => (
              <button key={s.title} type="button" onClick={() => composer.current?.fill(s.text)} className="group flex items-start gap-2.5 rounded-xl border border-[#232327] bg-[#16161a] p-3 text-left hover:border-[#34343a] hover:bg-[#1a1a1e]">
                <s.icon className="mt-0.5 size-4 shrink-0 text-zinc-500 group-hover:text-amber-400" />
                <span className="min-w-0">
                  <span className="block font-medium text-[13px] text-zinc-200">{s.title}</span>
                  <span className="block truncate text-[12px] text-zinc-500">{s.text.trim() || "…"}</span>
                </span>
              </button>
            ))}
          </div>
        </div>
      </div>
    );

  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
      <div ref={scroller} onScroll={onScroll} className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-3xl space-y-6 px-4 py-6">
          {turns.map((t, i) => (
            <div key={`${t.at}-${i}`} className="space-y-4">
              <UserBubble prompt={t.prompt} at={t.at} commands={commands} files={files} onOpenFile={onOpenFile} />
              <TurnView turn={t} sessionId={sessionId} isLast={i === turns.length - 1} onRetry={() => onSend(t.prompt)} onOpenFile={onOpenFile} onOpenCommit={onOpenCommit} />
            </div>
          ))}
        </div>
      </div>
      {!atBottom && (
        <button type="button" onClick={() => toBottom(true)} className="absolute bottom-28 left-1/2 flex -translate-x-1/2 items-center gap-1 rounded-full border border-[#2f2f35] bg-[#1a1a1e] px-3 py-1 text-[12px] text-zinc-300 shadow-lg hover:bg-[#222227]">
          <ArrowDownIcon className="size-3.5" />
          Mới nhất
        </button>
      )}
      <div className="mx-auto w-full max-w-3xl px-4 pb-4">{composerEl}</div>
    </div>
  );
}

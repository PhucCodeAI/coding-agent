import { MessageCircleQuestionIcon } from "lucide-react";
import { useState } from "react";
import { ApiError, api, type QuestionData } from "@/api";
import { Markdown } from "@/components/common";

export function QuestionCard({ question, sessionId }: { question: QuestionData; sessionId: string }) {
  const q = question.questions[0];
  const [other, setOther] = useState("");
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const send = async (answer: { choice?: string; text?: string }) => {
    setSent(true);
    try {
      await api.answer(sessionId, question.request_id, q.id, answer);
    } catch (e) {
      setSent(false);
      setError(e instanceof ApiError ? e.error.message : String(e));
    }
  };

  return (
    <div className="space-y-2.5 rounded-xl border border-amber-500/30 bg-amber-500/[0.04] p-3.5">
      <div className="flex items-start gap-2 font-medium text-[13px] text-amber-100">
        <MessageCircleQuestionIcon className="mt-0.5 size-4 shrink-0 text-amber-400" />
        <div className="min-w-0 flex-1">
          <Markdown content={q.question} />
        </div>
      </div>
      <div className="grid gap-1.5">
        {q.options.map((o) => (
          <button
            key={o.label}
            type="button"
            disabled={sent}
            onClick={() => send({ choice: o.label })}
            className={`rounded-lg border bg-[#18181c] px-3 py-2 text-left text-[13px] text-zinc-200 hover:bg-[#1f1f24] disabled:opacity-50 ${o.recommended ? "border-amber-500/40" : "border-[#2c2c31]"}`}
          >
            {o.label}
            {o.recommended && <span className="ml-2 text-[11px] text-amber-300/80">khuyến nghị</span>}
            {o.description && <span className="block text-[11.5px] text-zinc-500">{o.description}</span>}
          </button>
        ))}
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (other.trim()) void send({ text: other.trim() });
          }}
        >
          <input
            value={other}
            disabled={sent}
            placeholder={q.other_placeholder}
            onChange={(e) => setOther(e.target.value)}
            className="min-w-0 flex-1 rounded-lg border border-[#303036] bg-[#1c1c20] px-2.5 py-1.5 text-[12.5px] text-zinc-200 placeholder-zinc-500 focus:border-amber-500/60 focus:outline-none"
          />
          <button type="submit" disabled={sent || !other.trim()} className="rounded-lg bg-amber-500 px-3 font-semibold text-[12px] text-zinc-950 hover:bg-amber-400 disabled:bg-zinc-700 disabled:text-zinc-500">
            Gửi
          </button>
        </form>
      </div>
      <p className="text-[11px] text-zinc-500">{error ?? `Không trả lời trong ${Math.round(question.timeout_sec / 60)} phút thì agent dùng lựa chọn khuyến nghị.`}</p>
    </div>
  );
}

import { CopyIcon, FileIcon } from "lucide-react";
import type { SlashCommand } from "@/api";
import { useCopy } from "@/components/common";
import { COMMAND_ICONS, mentionsOf, parsePrompt } from "@/lib/commands";

export function UserBubble({ prompt, at, commands, files, onOpenFile }: { prompt: string; at: string; commands: SlashCommand[]; files: string[]; onOpenFile: (path: string) => void }) {
  const parsed = parsePrompt(prompt);
  const command = commands.find((c) => c.name === parsed.command);
  const Icon = (command && COMMAND_ICONS[command.name]) || null;
  const text = command ? parsed.text : prompt;
  const attached = mentionsOf(text).filter((p) => files.includes(p));
  const [copied, copy] = useCopy();
  return (
    <div className="group/user flex justify-end">
      <div className="max-w-[80%] space-y-1">
        <div className="space-y-1.5 break-words rounded-2xl rounded-tr-md border border-amber-500/20 bg-[#2a2418] px-4 py-2.5 text-sm text-zinc-100 leading-6">
          {command && (
            <div className="flex items-center gap-1.5">
              <span className="inline-flex items-center gap-1 rounded-md border border-amber-500/25 bg-amber-500/10 px-1.5 py-0.5 font-mono text-[12px] text-amber-300">
                {Icon && <Icon className="size-3" />}/{command.name}
              </span>
              <span className="text-[12px] text-zinc-400">{command.title}</span>
            </div>
          )}
          {text && (
            <p className="whitespace-pre-wrap">
              {text.split(/((?:^|(?<=\s))@[\w./-]+)/g).map((part, i) =>
                part.startsWith("@") ? (
                  <span key={i} className="font-mono text-[13px] text-sky-300">
                    {part}
                  </span>
                ) : (
                  part
                ),
              )}
            </p>
          )}
          {attached.length > 0 && (
            <div className="flex flex-wrap gap-1.5 pt-0.5">
              {attached.map((p) => (
                <button key={p} type="button" onClick={() => onOpenFile(p)} title="Đã đính kèm nội dung file — bấm để mở" className="inline-flex items-center gap-1 rounded-md border border-[#34343a] bg-[#1a1a1e] px-2 py-0.5 font-mono text-[11.5px] text-zinc-300 hover:border-zinc-500">
                  <FileIcon className="size-3 text-zinc-500" />
                  {p}
                </button>
              ))}
            </div>
          )}
        </div>
        <div className="flex items-center justify-end gap-1 text-[11px] text-zinc-500 opacity-0 transition-opacity group-hover/user:opacity-100">
          <span>{new Date(at).toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit" })}</span>
          <button type="button" onClick={() => copy(prompt)} className="rounded p-1 hover:bg-[#1f1f24] hover:text-zinc-300" title="Copy">
            <CopyIcon className={`size-3.5 ${copied ? "text-emerald-400" : ""}`} />
          </button>
        </div>
      </div>
    </div>
  );
}

import { ArrowUpIcon, FileIcon, SquareIcon } from "lucide-react";
import { type Ref, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";
import type { SlashCommand } from "@/api";
import { COMMAND_ICONS, mentionsOf, parsePrompt } from "@/lib/commands";

type Item = { kind: "command"; key: string; command: SlashCommand } | { kind: "file"; key: string; path: string };

export interface ComposerHandle {
  fill: (text: string) => void;
}

function Menu({ items, active, below, onPick, onHover }: { items: Item[]; active: number; below?: boolean; onPick: (i: Item) => void; onHover: (i: number) => void }) {
  const list = useRef<HTMLDivElement>(null);
  useEffect(() => {
    list.current?.querySelector(`[data-index="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [active]);
  const first = { command: items.findIndex((i) => i.kind === "command"), file: items.findIndex((i) => i.kind === "file") };
  return (
    <div role="listbox" className={`absolute right-0 left-0 z-30 overflow-hidden ${below ? "top-full mt-2" : "bottom-full mb-2"} rounded-xl border border-[#2f2f35] bg-[#18181c] shadow-2xl shadow-black/40`}>
      <div ref={list} className="max-h-80 overflow-y-auto overscroll-contain py-1">
        {items.map((item, i) => (
          <div key={item.key}>
            {first[item.kind] === i && (
              <div className={`px-3 pt-1.5 pb-1 text-[10.5px] text-zinc-500 uppercase tracking-wider ${i > 0 ? "mt-1 border-[#26262b] border-t" : ""}`}>
                {item.kind === "command" ? "Lệnh hệ thống" : "File trong workspace"}
              </div>
            )}
            <button
              type="button"
              role="option"
              aria-selected={i === active}
              data-index={i}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => onPick(item)}
              onMouseMove={() => onHover(i)}
              className={`flex w-full items-start gap-2.5 px-3 py-1.5 text-left ${i === active ? "bg-[#26262c]" : ""}`}
            >
              {item.kind === "command" ? (
                <CommandItem command={item.command} active={i === active} />
              ) : (
                <>
                  <span className="mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-md bg-[#232328] text-zinc-400">
                    <FileIcon className="size-3.5" />
                  </span>
                  <span className="min-w-0 flex-1 self-center truncate font-mono text-[12.5px] text-zinc-200">{item.path}</span>
                </>
              )}
            </button>
          </div>
        ))}
      </div>
      <div className="flex gap-3 border-[#26262b] border-t px-3 py-1.5 text-[10.5px] text-zinc-500">
        <span>↑↓ chọn</span>
        <span>Enter / Tab chèn</span>
        <span>Esc đóng</span>
        <span className="ml-auto">@ đính kèm file · / lệnh</span>
      </div>
    </div>
  );
}

function CommandItem({ command: c, active }: { command: SlashCommand; active: boolean }) {
  const Icon = COMMAND_ICONS[c.name];
  return (
    <>
      <span className="mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-md border border-amber-500/25 bg-amber-500/10 text-amber-400">{Icon && <Icon className="size-3.5" />}</span>
      <span className="min-w-0 flex-1">
        <span className="flex items-baseline gap-2">
          <span className="font-mono text-[12.5px] text-zinc-100">/{c.name}</span>
          <span className="truncate text-[12.5px] text-zinc-300">{c.title}</span>
          <span className={`ml-auto shrink-0 rounded px-1.5 text-[10.5px] ${c.mode === "code" ? "bg-emerald-500/10 text-emerald-300" : "bg-sky-500/10 text-sky-300"}`}>{c.mode === "code" ? "sửa code" : "chỉ đọc"}</span>
        </span>
        <span className="block truncate text-[11.5px] text-zinc-500">{c.description}</span>
        {active && (
          <span className="mt-0.5 block text-[11px] text-zinc-600">
            /{c.name} <span className="italic">{c.arg_hint}</span>
          </span>
        )}
      </span>
    </>
  );
}

export function Composer({
  ref,
  commands,
  files,
  live,
  autofocus,
  menuBelow,
  onSend,
  onStop,
}: {
  ref?: Ref<ComposerHandle>;
  commands: SlashCommand[];
  files: string[];
  live: boolean;
  autofocus?: boolean;
  menuBelow?: boolean;
  onSend: (text: string) => void;
  onStop: () => void;
}) {
  const [value, setValue] = useState("");
  const [caret, setCaret] = useState(0);
  const [active, setActive] = useState(0);
  const [dismissedAt, setDismissedAt] = useState<number | null>(null);
  const area = useRef<HTMLTextAreaElement>(null);

  const focusAt = (text: string, pos: number) => {
    setValue(text);
    queueMicrotask(() => {
      area.current?.focus();
      area.current?.setSelectionRange(pos, pos);
      setCaret(pos);
      resize();
    });
  };
  useImperativeHandle(ref, () => ({ fill: (text) => focusAt(text, text.length) }));
  useEffect(() => {
    if (autofocus) area.current?.focus();
  }, [autofocus]);

  const resize = () => {
    const el = area.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  };

  const trigger = useMemo(() => {
    const m = value.slice(0, caret).match(/(^|\s)([/@])([^\s/@]*)$/);
    return m ? { char: m[2], query: m[3], start: caret - m[2].length - m[3].length } : null;
  }, [value, caret]);
  const items = useMemo<Item[]>(() => {
    if (!trigger || trigger.start === dismissedAt) return [];
    const out: Item[] = [];
    const q = trigger.query.toLowerCase();
    if (trigger.char === "/" && value.slice(0, trigger.start).trim() === "")
      for (const c of commands.filter((c) => c.name.startsWith(q))) out.push({ kind: "command", key: `c:${c.name}`, command: c });
    for (const p of files.filter((p) => p.toLowerCase().includes(q)).slice(0, 8)) out.push({ kind: "file", key: `f:${p}`, path: p });
    return out;
  }, [trigger, dismissedAt, commands, files, value]);
  const parsed = parsePrompt(value);
  const command = commands.find((c) => c.name === parsed.command);
  const attached = mentionsOf(value).filter((p) => files.includes(p));
  const canSend = value.trim().length > 0 && !live;

  const pick = (item: Item) => {
    if (!trigger) return;
    const insert = item.kind === "command" ? `/${item.command.name} ` : `@${item.path} `;
    setActive(0);
    focusAt(value.slice(0, trigger.start) + insert + value.slice(caret), trigger.start + insert.length);
  };
  const submit = () => {
    if (!canSend) return;
    onSend(value.trim());
    focusAt("", 0);
    setDismissedAt(null);
  };
  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.nativeEvent.isComposing) return;
    if (items.length) {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        setActive((a) => (a + (e.key === "ArrowDown" ? 1 : items.length - 1)) % items.length);
        return;
      }
      if (e.key === "Enter" || e.key === "Tab") {
        e.preventDefault();
        pick(items[Math.min(active, items.length - 1)]);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        setDismissedAt(trigger?.start ?? null);
        return;
      }
    }
    if (e.key === "Enter" && !e.shiftKey && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      submit();
    }
  };
  const syncCaret = () => setCaret(area.current?.selectionStart ?? value.length);
  const CommandIcon = command && COMMAND_ICONS[command.name];

  return (
    <div className="relative">
      {items.length > 0 && <Menu items={items} active={active} below={menuBelow} onPick={pick} onHover={setActive} />}
      <div className="rounded-2xl border border-[#2f2f35] bg-[#1a1a1e] shadow-black/20 shadow-lg transition-colors focus-within:border-zinc-500">
        <textarea
          ref={area}
          value={value}
          rows={1}
          onChange={(e) => {
            setValue(e.target.value);
            setCaret(e.target.selectionStart);
            resize();
          }}
          onKeyDown={onKeyDown}
          onKeyUp={syncCaret}
          onClick={syncCaret}
          onBlur={() => setDismissedAt(trigger?.start ?? null)}
          onFocus={() => setDismissedAt(null)}
          placeholder={live ? "Agent đang làm việc — bạn có thể soạn trước yêu cầu tiếp theo..." : "Mô tả việc cần làm… gõ / để chọn lệnh, @ để đính kèm file"}
          className="block max-h-[200px] w-full resize-none bg-transparent px-4 pt-3.5 pb-1 text-sm text-zinc-100 leading-6 placeholder-zinc-500 focus:outline-none"
        />
        <div className="flex items-center justify-between gap-2 px-3 pt-1 pb-2.5">
          <div className="flex min-w-0 select-none items-center gap-1.5 text-[11px] text-zinc-500">
            {command && (
              <>
                <span className="inline-flex items-center gap-1 rounded-md border border-amber-500/25 bg-amber-500/10 px-1.5 py-0.5 text-amber-300">
                  {CommandIcon && <CommandIcon className="size-3" />}/{command.name}
                </span>
                <span className="truncate">{command.title}</span>
              </>
            )}
            {attached.length > 0 && (
              <span className="inline-flex items-center gap-1 rounded-md bg-[#232328] px-1.5 py-0.5 text-zinc-400" title={attached.join(", ")}>
                <FileIcon className="size-3" />
                {attached.length} file đính kèm
              </span>
            )}
            {!command && !attached.length && (
              <span className="truncate">
                <kbd className="font-sans">/</kbd> lệnh · <kbd className="font-sans">@</kbd> file · <kbd className="font-sans">Enter</kbd> gửi · <kbd className="font-sans">Shift+Enter</kbd> xuống dòng
              </span>
            )}
          </div>
          {live ? (
            <button type="button" onClick={onStop} title="Dừng agent" className="flex size-8 shrink-0 items-center justify-center rounded-full bg-zinc-200 text-zinc-900 hover:bg-white">
              <SquareIcon className="size-3.5" fill="currentColor" />
            </button>
          ) : (
            <button
              type="button"
              onClick={submit}
              disabled={!canSend}
              title="Gửi (Enter)"
              className="flex size-8 shrink-0 items-center justify-center rounded-full bg-amber-500 text-zinc-950 hover:bg-amber-400 disabled:cursor-not-allowed disabled:bg-zinc-700 disabled:text-zinc-500"
            >
              <ArrowUpIcon className="size-4" strokeWidth={2.5} />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

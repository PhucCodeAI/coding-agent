import { PencilIcon, SaveIcon, XIcon } from "lucide-react";
import { useEffect, useState } from "react";
import { ApiError, type FileContent } from "@/api";
import { CodeLines, CopyButton } from "@/components/common";

const formatSize = (n: number) => (n < 1024 ? `${n} B` : `${(n / 1024).toFixed(1)} KB`);

/** Nội dung 1 file mở trong cây: xem có tô màu, hoặc sửa rồi lưu (⌘/Ctrl S) khi agent không chạy. */
export function FileView({ file, locked, onSave }: { file: FileContent | null; locked: boolean; onSave: (content: string) => Promise<void> }) {
  const [draft, setDraft] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setDraft(null);
    setError(null);
  }, [file?.path]);

  if (!file) return <div className="px-3 py-2 text-[12px] text-zinc-500">Đang tải…</div>;
  const editable = !file.binary && !file.truncated;
  const dirty = draft !== null && draft !== file.content;
  const save = async () => {
    if (draft === null || saving) return;
    setSaving(true);
    try {
      await onSave(draft);
      setDraft(null);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.error.message : String(e));
    } finally {
      setSaving(false);
    }
  };
  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") (e.preventDefault(), void save());
    else if (e.key === "Escape") setDraft(null);
    else if (e.key === "Tab") {
      e.preventDefault();
      const el = e.currentTarget;
      const { selectionStart: a, selectionEnd: b } = el;
      setDraft(`${el.value.slice(0, a)}    ${el.value.slice(b)}`);
      requestAnimationFrame(() => el.setSelectionRange(a + 4, a + 4));
    }
  };

  return (
    <div className={`overflow-hidden rounded-lg border bg-[#0f0f11] ${draft !== null ? "border-amber-500/40" : "border-[#2a2a2e]"}`}>
      <div className="flex items-center gap-2 border-[#242428] border-b bg-[#161618] px-3 py-1 text-[11px] text-zinc-500">
        <span>{formatSize(file.size)}</span>
        {file.truncated && <span className="text-amber-400">đã cắt bớt — không sửa được</span>}
        {draft !== null && <span className="text-amber-300">{dirty ? "chưa lưu · ⌘/Ctrl S lưu · Esc hủy" : "đang sửa"}</span>}
        {error && <span className="truncate text-rose-400">{error}</span>}
        <span className="ml-auto flex items-center gap-1">
          {draft === null ? (
            <>
              <CopyButton text={file.content} />
              {editable && (
                <button type="button" disabled={locked} onClick={() => setDraft(file.content)} title={locked ? "Agent đang chạy trong phiên — đợi xong để sửa" : "Sửa file"} className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10.5px] text-zinc-400 hover:bg-[#2b2b30] hover:text-zinc-100 disabled:opacity-40">
                  <PencilIcon className="size-3" />
                  Sửa
                </button>
              )}
            </>
          ) : (
            <>
              <button type="button" onClick={() => setDraft(null)} className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10.5px] text-zinc-400 hover:bg-[#2b2b30] hover:text-zinc-100">
                <XIcon className="size-3" />
                Hủy
              </button>
              <button type="button" onClick={save} disabled={!dirty || saving || locked} className="inline-flex items-center gap-1 rounded bg-amber-500 px-2 py-0.5 font-semibold text-[10.5px] text-zinc-950 hover:bg-amber-400 disabled:bg-zinc-700 disabled:text-zinc-500">
                <SaveIcon className="size-3" />
                {saving ? "Đang lưu…" : "Lưu"}
              </button>
            </>
          )}
        </span>
      </div>
      {file.binary ? (
        <div className="px-3 py-3 text-[12px] text-zinc-500">File nhị phân — không hiển thị.</div>
      ) : draft !== null ? (
        <textarea
          autoFocus
          spellCheck={false}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKeyDown}
          rows={Math.max(4, draft.split("\n").length + 1)}
          className="block w-full resize-none whitespace-pre bg-transparent px-3.5 py-2.5 font-mono text-[12px] text-zinc-100 leading-5 focus:outline-none"
        />
      ) : (
        <div className="overflow-x-auto py-2.5">
          <CodeLines code={file.content} className="min-w-max" />
        </div>
      )}
    </div>
  );
}

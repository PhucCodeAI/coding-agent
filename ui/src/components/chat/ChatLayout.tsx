import { useCallback, useEffect, useRef, useState } from "react";
import { api, type SessionSummary, type SlashCommand, type Turn } from "@/api";
import { ErrorBanner } from "@/components/common";
import { Sidebar } from "@/components/sessions/Sidebar";
import { WorkspacePanel } from "@/components/workspace/WorkspacePanel";
import { useWorkspace } from "@/components/workspace/useWorkspace";
import { hasCodeActivity, usageOf } from "@/lib/agent";
import { navigate } from "@/lib/router";
import { liveSessionIds, liveTurns, startStream, stopStream, subscribeStreams } from "@/lib/streams";
import { Chat } from "./Chat";
import { Header } from "./Header";

function useStored<T>(key: string, initial: T): [T, (v: T) => void] {
  const [value, setValue] = useState<T>(() => {
    try {
      const raw = localStorage.getItem(key);
      return raw === null ? initial : (JSON.parse(raw) as T);
    } catch {
      return initial;
    }
  });
  const set = useCallback(
    (v: T) => {
      setValue(v);
      try {
        localStorage.setItem(key, JSON.stringify(v));
      } catch {}
    },
    [key],
  );
  return [value, set];
}

export function ChatLayout({ routeId }: { routeId: string | null }) {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [commands, setCommands] = useState<SlashCommand[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(routeId);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [running, setRunning] = useState<string[]>(liveSessionIds);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);
  const [sidebarOpen, setSidebarOpen] = useStored("sidebar-open", true);
  const [split, setSplit] = useStored("workspace-split", 0.5);
  // Khóa của cuộc hội thoại đang hiển thị: mã phiên, hoặc `pending:...` khi phiên mới chưa có mã.
  const shownKey = useRef<string | null | undefined>(undefined);
  const container = useRef<HTMLDivElement>(null);
  const live = turns.at(-1)?.live ?? false;
  const ws = useWorkspace(sessionId, version);

  const refreshSessions = useCallback(() => void api.sessions().then(setSessions, () => {}), []);
  useEffect(() => {
    refreshSessions();
    api.commands().then(setCommands, () => {});
  }, [refreshSessions]);

  useEffect(
    () =>
      subscribeStreams((c) => {
        if (c.kind === "rename") {
          if (shownKey.current === c.from) {
            shownKey.current = c.to;
            setSessionId(c.to);
            navigate(`/c/${c.to}`, true);
          }
          refreshSessions();
        } else if (shownKey.current === c.key) {
          setTurns(c.turns);
          if (c.kind === "end" || c.event?.event === "file" || c.event?.event === "commit") setVersion((v) => v + 1);
        }
        setRunning(liveSessionIds());
        if (c.kind === "end") refreshSessions();
      }),
    [refreshSessions],
  );

  useEffect(() => {
    // URL vừa đổi theo phiên mới của chính lượt đang hiển thị → giữ nguyên.
    if (routeId && routeId === shownKey.current) return;
    shownKey.current = routeId;
    setSessionId(routeId);
    setLoadError(null);
    const liveNow = routeId ? liveTurns(routeId) : null;
    setTurns(liveNow ?? []);
    if (routeId && !liveNow)
      api.turns(routeId).then(
        (t) => shownKey.current === routeId && setTurns(t),
        (e) => setLoadError(String(e?.message ?? e)),
      );
  }, [routeId]);

  useEffect(() => {
    if (hasCodeActivity(turns)) ws.setOpen(true);
  }, [turns.length > 0 && hasCodeActivity(turns)]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.metaKey || e.ctrlKey)) return;
      if (e.key.toLowerCase() === "b" && !e.shiftKey) (e.preventDefault(), setSidebarOpen(!sidebarOpen));
      if (e.key.toLowerCase() === "o" && e.shiftKey) (e.preventDefault(), ws.setOpen(!ws.open));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [sidebarOpen, setSidebarOpen, ws]);

  const send = (prompt: string) => {
    if (live) return;
    const key = sessionId ?? `pending:${Date.now()}`;
    shownKey.current = key;
    void startStream(key, sessionId, turns, prompt);
  };

  const startDrag = (e: React.PointerEvent) => {
    e.preventDefault();
    const move = (ev: PointerEvent) => {
      const rect = container.current?.getBoundingClientRect();
      if (rect) setSplit(Math.min(0.75, Math.max(0.3, (ev.clientX - rect.left) / rect.width)));
    };
    const up = () => (window.removeEventListener("pointermove", move), window.removeEventListener("pointerup", up));
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };

  const title = sessions.find((s) => s.session_id === sessionId)?.title ?? (turns[0]?.prompt.slice(0, 60) || "Phiên mới");

  return (
    <div className="flex h-dvh bg-[#121214] text-zinc-300">
      {sidebarOpen && (
        <Sidebar
          sessions={sessions}
          activeId={sessionId}
          runningIds={running}
          onClose={() => setSidebarOpen(false)}
          onChanged={refreshSessions}
          onDeleted={(id) => (id === sessionId && navigate("/"), refreshSessions())}
        />
      )}
      <div ref={container} className="flex min-w-0 flex-1">
        <main className="flex min-w-0 flex-col bg-[#141416]" style={{ width: ws.open && sessionId ? `${split * 100}%` : "100%" }}>
          <Header title={title} live={live} usage={usageOf(turns)} sidebarOpen={sidebarOpen} workspaceOpen={ws.open || !sessionId} onOpenSidebar={() => setSidebarOpen(true)} onOpenWorkspace={() => ws.setOpen(true)} />
          {loadError && (
            <div className="mx-auto w-full max-w-3xl px-4 pt-4">
              <ErrorBanner error={loadError} />
            </div>
          )}
          <Chat sessionId={sessionId} turns={turns} live={live} commands={commands} files={ws.files} onSend={send} onStop={() => stopStream(shownKey.current ?? null)} onOpenFile={ws.openFile} onOpenCommit={ws.openCommit} />
        </main>
        {ws.open && sessionId && (
          <>
            <div onPointerDown={startDrag} className="w-1 shrink-0 cursor-col-resize bg-[#232327] transition-colors hover:bg-amber-500/60" />
            <div className="min-w-0 flex-1">
              <WorkspacePanel ws={ws} turns={turns} live={live} />
            </div>
          </>
        )}
      </div>
    </div>
  );
}

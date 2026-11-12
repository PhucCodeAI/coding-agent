import { useCallback, useEffect, useState } from "react";
import { ApiError, api, type Commit, type ErrorData, type ExecResult, type FileContent } from "@/api";

export type WorkspaceTab = "code" | "changes" | "terminal";
export const WORKING = "__working__";

export interface UserRun {
  id: number;
  command: string;
  at: string;
  result: ExecResult | null;
  error: ErrorData | null;
}

export interface Workspace {
  open: boolean;
  setOpen: (open: boolean) => void;
  tab: WorkspaceTab;
  setTab: (tab: WorkspaceTab) => void;
  files: string[];
  openPath: string | null;
  file: FileContent | null;
  commits: Commit[];
  changes: { path: string; status: "added" | "modified" | "deleted" }[];
  selectedCommit: string | null;
  diff: string | null;
  diffTruncated: boolean;
  recentlyChanged: Set<string>;
  error: ErrorData | null;
  userRuns: UserRun[];
  openFile: (path: string) => void;
  toggleFile: (path: string) => void;
  saveFile: (path: string, content: string) => Promise<void>;
  exec: (command: string) => Promise<void>;
  openCommit: (hash: string) => void;
  openWorking: () => void;
}

const asError = (e: unknown): ErrorData => (e instanceof ApiError ? e.error : { code: "CLIENT_ERROR", title: "Lỗi", message: String(e), hint: "" });

// Lệnh người dùng tự chạy, giữ theo phiên khi chuyển qua lại (không lưu backend).
const runsBySession = new Map<string, UserRun[]>();
let nextRunId = 1;

export function useWorkspace(sessionId: string | null, version: number): Workspace {
  const [open, setOpen] = useState(true);
  const [tab, setTab] = useState<WorkspaceTab>("code");
  const [files, setFiles] = useState<string[]>([]);
  const [openPath, setOpenPath] = useState<string | null>(null);
  const [file, setFile] = useState<FileContent | null>(null);
  const [commits, setCommits] = useState<Commit[]>([]);
  const [changes, setChanges] = useState<Workspace["changes"]>([]);
  const [workingDiff, setWorkingDiff] = useState("");
  const [selectedCommit, setSelectedCommit] = useState<string | null>(null);
  const [diff, setDiff] = useState<string | null>(null);
  const [diffTruncated, setDiffTruncated] = useState(false);
  const [error, setError] = useState<ErrorData | null>(null);
  const [userRuns, setUserRuns] = useState<UserRun[]>([]);
  const [reloads, setReloads] = useState(0);

  useEffect(() => {
    setFile(null);
    setOpenPath(null);
    setDiff(null);
    setSelectedCommit(null);
    setFiles([]);
    setCommits([]);
    setChanges([]);
    setError(null);
    setUserRuns(sessionId ? (runsBySession.get(sessionId) ?? []) : []);
  }, [sessionId]);

  useEffect(() => {
    if (!sessionId) return;
    Promise.all([api.files(sessionId), api.commits(sessionId), api.changes(sessionId)]).then(
      ([f, c, ch]) => {
        setFiles(f.map((x) => x.path).filter((p) => p !== ".gitignore"));
        setCommits(c.commits);
        setChanges(ch.files);
        setWorkingDiff(ch.diff);
        setError(null);
      },
      (e) => setError(asError(e)),
    );
  }, [sessionId, version, reloads]);

  useEffect(() => {
    if (openPath && sessionId) api.file(sessionId, openPath).then(setFile, () => setFile(null));
    else setFile(null);
  }, [version, reloads, sessionId, openPath]);

  const openFile = useCallback((path: string) => {
    setOpen(true);
    setTab("code");
    setOpenPath(path);
    requestAnimationFrame(() => document.getElementById(`ws-file-${path}`)?.scrollIntoView({ block: "start" }));
  }, []);
  const toggleFile = useCallback((path: string) => setOpenPath((p) => (p === path ? null : path)), []);

  const saveFile = useCallback(
    async (path: string, content: string) => {
      if (!sessionId) return;
      await api.writeFile(sessionId, path, content);
      setReloads((r) => r + 1);
    },
    [sessionId],
  );

  const exec = useCallback(
    async (command: string) => {
      if (!sessionId) return;
      const id = nextRunId++;
      const update = (fn: (runs: UserRun[]) => UserRun[]) => {
        const runs = fn(runsBySession.get(sessionId) ?? []);
        runsBySession.set(sessionId, runs);
        setUserRuns(runs);
      };
      update((r) => [...r, { id, command, at: new Date().toISOString(), result: null, error: null }]);
      try {
        const result = await api.exec(sessionId, command);
        update((r) => r.map((x) => (x.id === id ? { ...x, result } : x)));
      } catch (e) {
        update((r) => r.map((x) => (x.id === id ? { ...x, error: asError(e) } : x)));
      }
      setReloads((n) => n + 1);
    },
    [sessionId],
  );

  const openCommit = useCallback(
    (hash: string) => {
      if (!sessionId) return;
      setOpen(true);
      setTab("changes");
      setSelectedCommit(hash);
      api.commitDiff(sessionId, hash).then(
        (d) => {
          setDiff(d.diff);
          setDiffTruncated(d.truncated);
        },
        (e) => setError(asError(e)),
      );
    },
    [sessionId],
  );

  const openWorking = useCallback(() => {
    setSelectedCommit(WORKING);
    setDiff(workingDiff);
    setDiffTruncated(false);
  }, [workingDiff]);

  return {
    open, setOpen, tab, setTab, files, openPath, file, commits, changes, selectedCommit, diff, diffTruncated,
    recentlyChanged: new Set(commits[0]?.files ?? []), error, userRuns, openFile, toggleFile, saveFile, exec, openCommit, openWorking,
  };
}

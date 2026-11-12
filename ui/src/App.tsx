import { ChatLayout } from "@/components/chat/ChatLayout";
import { TracePage } from "@/components/trace/TracePage";
import { useLocation } from "@/lib/router";

export default function App() {
  const url = useLocation();
  const trace = url.pathname.match(/^\/traces(?:\/([\w-]+))?\/?$/);
  if (trace) return <TracePage sessionId={trace[1] ?? null} focusTurn={url.searchParams.get("turn")} />;
  const chat = url.pathname.match(/^\/c\/([\w-]+)/);
  return <ChatLayout routeId={chat?.[1] ?? null} />;
}

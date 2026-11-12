import { useSyncExternalStore } from "react";

const subscribe = (fn: () => void) => {
  window.addEventListener("popstate", fn);
  return () => window.removeEventListener("popstate", fn);
};

export function useLocation(): URL {
  const href = useSyncExternalStore(subscribe, () => window.location.href);
  return new URL(href);
}

export function navigate(to: string, replace = false): void {
  if (to === window.location.pathname + window.location.search) return;
  window.history[replace ? "replaceState" : "pushState"](null, "", to);
  window.dispatchEvent(new PopStateEvent("popstate"));
}

export function linkHandler(to: string) {
  return (e: React.MouseEvent) => {
    if (e.metaKey || e.ctrlKey || e.button !== 0) return;
    e.preventDefault();
    navigate(to);
  };
}

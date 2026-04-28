import { useEffect, useState, useCallback } from "react";

const STORAGE_KEY = "michelle.currentProjectId";
const URL_PARAM = "project_id";

/** Read URL `?project_id=...` first (deep-link friendly), fall back to
 * localStorage (sticky across reloads). Empty string means "no project
 * selected yet" so callers can show the empty state. */
function readInitial(): string {
  if (typeof window === "undefined") return "";
  const urlVal = new URLSearchParams(window.location.search).get(URL_PARAM);
  if (urlVal) return urlVal;
  return localStorage.getItem(STORAGE_KEY) ?? "";
}

/** Sync URL search param without triggering a navigation — bare history
 * replace so TanStack Router doesn't re-run loaders just because we
 * changed the global project. */
function syncUrl(projectId: string) {
  if (typeof window === "undefined") return;
  const url = new URL(window.location.href);
  if (projectId) {
    url.searchParams.set(URL_PARAM, projectId);
  } else {
    url.searchParams.delete(URL_PARAM);
  }
  window.history.replaceState({}, "", url.toString());
}

/** Global "current project" hook. Persisted in localStorage, mirrored to
 * the URL search param so links/refreshes preserve the selection. All
 * pages use this so swapping project in one place reflects everywhere. */
export function useCurrentProject(): {
  projectId: string;
  setProjectId: (id: string) => void;
} {
  const [projectId, setProjectIdState] = useState<string>(readInitial);

  const setProjectId = useCallback((id: string) => {
    setProjectIdState(id);
    if (id) {
      localStorage.setItem(STORAGE_KEY, id);
    } else {
      localStorage.removeItem(STORAGE_KEY);
    }
    syncUrl(id);
    // Notify other hook instances in the same tab.
    window.dispatchEvent(new CustomEvent("michelle:projectchange", { detail: id }));
  }, []);

  // Keep multiple hook instances (different mounted components) in sync.
  useEffect(() => {
    const onChange = (e: Event) => {
      const next = (e as CustomEvent<string>).detail ?? "";
      setProjectIdState(next);
    };
    const onStorage = (e: StorageEvent) => {
      if (e.key === STORAGE_KEY) setProjectIdState(e.newValue ?? "");
    };
    window.addEventListener("michelle:projectchange", onChange);
    window.addEventListener("storage", onStorage);
    return () => {
      window.removeEventListener("michelle:projectchange", onChange);
      window.removeEventListener("storage", onStorage);
    };
  }, []);

  return { projectId, setProjectId };
}

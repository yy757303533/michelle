import { createFileRoute } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useCurrentProject } from "../lib/useCurrentProject";

const PRD_URL_KEY = "prd_id";

/** Read `?prd_id=` from the URL without going through the router so we
 * don't have to declare a search schema on the file route. */
function readPrdIdFromUrl(): string {
  if (typeof window === "undefined") return "";
  return new URLSearchParams(window.location.search).get(PRD_URL_KEY) ?? "";
}

function writePrdIdToUrl(prdId: string) {
  if (typeof window === "undefined") return;
  const url = new URL(window.location.href);
  if (prdId) url.searchParams.set(PRD_URL_KEY, prdId);
  else url.searchParams.delete(PRD_URL_KEY);
  window.history.replaceState({}, "", url.toString());
}

export const Route = createFileRoute("/prd")({
  component: PrdPage,
});

interface ChapterMeta {
  position: number;
  level: number;
  title: string;
  normalized_title: string;
  hash: string;
  body_chars: number;
}

interface UploadResponse {
  data: {
    prd_id: string;
    version: number;
    title: string;
    chapters: ChapterMeta[];
    prior_version_id: string | null;
    diff_summary: Record<string, number> | null;
  };
}

interface GenerateResponse {
  data: {
    prd_id: string;
    total_cases: number;
    results: Array<{
      chapter_index: number;
      chapter_title: string;
      saved_count: number;
      saved_case_ids?: string[];
      coverage_notes?: string;
      error?: string;
    }>;
  };
}

interface PRDListItem {
  prd_id: string;
  project_id: string;
  name: string;
  version: number;
  chapter_count: number;
  uploaded_at: string;
}

function PrdPage() {
  const qc = useQueryClient();
  // Project comes from the global header selector — single source of truth.
  // The page no longer owns a `projectId` state; that lived here only because
  // the original UX hard-coded "michelle".
  const { projectId } = useCurrentProject();
  const [name, setName] = useState("");
  const [markdown, setMarkdown] = useState("");
  const [uploaded, setUploaded] = useState<UploadResponse["data"] | null>(null);
  const [genResult, setGenResult] = useState<GenerateResponse["data"] | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  // `?prd_id=` survives navigation away and back. Without it, the
  // user uploads a 90-chapter PRD, hops to /cases to check a result,
  // returns, and the chapter list is gone — all transient state
  // disappeared with the unmounted component. Persisting just the id
  // is enough: the chapters live on the server, we re-fetch on mount.
  const [activePrdId, setActivePrdIdState] = useState<string>(readPrdIdFromUrl);

  const setActivePrdId = (id: string) => {
    setActivePrdIdState(id);
    writePrdIdToUrl(id);
  };

  const list = useQuery({
    queryKey: ["prd-list", projectId],
    enabled: Boolean(projectId),
    queryFn: async (): Promise<{ data: PRDListItem[] }> => {
      const r = await fetch(`/api/prd/?project_id=${encodeURIComponent(projectId)}`);
      return r.json();
    },
  });

  // Re-hydrate `uploaded` whenever `activePrdId` changes (mount, URL deep
  // link, history-table click). Doesn't run unless we have an id, so the
  // "upload first" empty state stays clean.
  const hydrate = useQuery({
    queryKey: ["prd-detail", activePrdId],
    enabled: Boolean(activePrdId),
    queryFn: async (): Promise<UploadResponse> => {
      const r = await fetch(`/api/prd/${activePrdId}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    },
  });

  useEffect(() => {
    if (hydrate.data) {
      setUploaded(hydrate.data.data);
      setSelected(new Set(hydrate.data.data.chapters.map((c) => c.position)));
      setGenResult(null);
    }
    if (!activePrdId) {
      setUploaded(null);
      setSelected(new Set());
      setGenResult(null);
    }
  }, [hydrate.data, activePrdId]);

  const upload = useMutation({
    mutationFn: async (): Promise<UploadResponse> => {
      const r = await fetch("/api/prd/upload", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          project_id: projectId,
          name: name || undefined,
          markdown,
        }),
      });
      if (!r.ok) throw new Error(await r.text());
      return r.json();
    },
    onSuccess: (resp) => {
      setUploaded(resp.data);
      setGenResult(null);
      setSelected(new Set(resp.data.chapters.map((c) => c.position)));
      setActivePrdId(resp.data.prd_id);
      qc.invalidateQueries({ queryKey: ["prd-list"] });
    },
  });

  const deletePrd = useMutation({
    mutationFn: async (prdId: string) => {
      const r = await fetch(`/api/prd/${prdId}`, { method: "DELETE" });
      if (!r.ok && r.status !== 204) throw new Error(`HTTP ${r.status}: ${await r.text()}`);
    },
    onSuccess: (_void, prdId) => {
      // If the deleted row was the active one, clear the page state.
      if (prdId === activePrdId) setActivePrdId("");
      qc.invalidateQueries({ queryKey: ["prd-list"] });
      qc.invalidateQueries({ queryKey: ["prd-detail"] });
    },
  });

  const generate = useMutation({
    mutationFn: async (): Promise<GenerateResponse> => {
      if (!uploaded) throw new Error("upload first");
      const r = await fetch(`/api/prd/${uploaded.prd_id}/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          // Sort numerically — Set iteration order is insertion order, which
          // produces an unstable list when the user toggles checkboxes off
          // and back on.
          chapter_indices: [...selected].sort((a, b) => a - b),
          max_cases_per_chapter: 8,
        }),
      });
      if (!r.ok) throw new Error(await r.text());
      return r.json();
    },
    onSuccess: (resp) => {
      setGenResult(resp.data);
      qc.invalidateQueries({ queryKey: ["cases"] });
      // Dashboard widget keys on ["cases-summary"] — invalidate explicitly so
      // the home page reflects the new generated cases without a manual reload.
      qc.invalidateQueries({ queryKey: ["cases-summary"] });
    },
  });

  const toggleChapter = (pos: number) => {
    const next = new Set(selected);
    if (next.has(pos)) next.delete(pos);
    else next.add(pos);
    setSelected(next);
  };

  const onFilePicked = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (!f) return;
    // Cap at 2 MB so a stray binary doesn't lock the browser; PRDs are
    // markdown text so the cap is generous.
    if (f.size > 2 * 1024 * 1024) {
      alert(`file too large (${(f.size / 1024 / 1024).toFixed(1)} MB) — paste contents instead`);
      e.target.value = "";
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      const text = typeof reader.result === "string" ? reader.result : "";
      setMarkdown(text);
      // If `name` is empty, default to filename without extension so the
      // user doesn't have to type it.
      if (!name) {
        const stem = f.name.replace(/\.(md|markdown|txt)$/i, "");
        setName(stem);
      }
    };
    reader.readAsText(f);
    // Reset input so picking the same file again still triggers onChange.
    e.target.value = "";
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">PRD ingest</h1>
        <p className="text-slate-500 text-sm mt-1">
          Paste markdown, see chapters detected, pick which to AI-generate cases for.
        </p>
      </div>

      {!projectId ? (
        <div className="bg-white border border-slate-200 rounded-lg p-8 text-center text-sm text-slate-500">
          Pick a project from the header dropdown to upload PRDs.
        </div>
      ) : null}

      {/* Upload form */}
      <div className={"bg-white border border-slate-200 rounded-lg p-4 space-y-3 " + (projectId ? "" : "opacity-50 pointer-events-none")}>
        <div className="text-xs text-slate-500">
          uploading to project <code className="font-mono">{projectId || "—"}</code>
        </div>
        <Field label="name (optional, defaults to first H1)">
          <input
            className="border border-slate-200 rounded px-2 py-1 w-full text-sm"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Michelle PRD v0.5"
          />
        </Field>
        <Field label="markdown">
          <div className="flex items-center gap-2 mb-1.5 text-xs text-slate-500">
            <label className="bg-slate-100 hover:bg-slate-200 text-slate-700 px-2 py-0.5 rounded cursor-pointer">
              📄 choose .md file
              <input
                type="file"
                accept=".md,.markdown,.txt,text/markdown,text/plain"
                onChange={onFilePicked}
                className="hidden"
              />
            </label>
            <span>or paste contents below</span>
            {markdown && (
              <span className="ml-auto font-mono">
                {markdown.length.toLocaleString()} chars
              </span>
            )}
          </div>
          <textarea
            className="border border-slate-200 rounded p-2 w-full text-sm font-mono"
            rows={10}
            value={markdown}
            onChange={(e) => setMarkdown(e.target.value)}
            placeholder="# My PRD&#10;&#10;## Goals&#10;&#10;..."
          />
        </Field>
        <button
          className="bg-slate-900 text-white text-sm px-3 py-1.5 rounded hover:bg-slate-700 disabled:opacity-50"
          disabled={!markdown.trim() || upload.isPending}
          onClick={() => upload.mutate()}
        >
          {upload.isPending ? "uploading…" : "Upload + parse"}
        </button>
        {upload.error && (
          <pre className="text-xs text-red-600 whitespace-pre-wrap">
            {(upload.error as Error).message}
          </pre>
        )}
      </div>

      {/* After upload: chapter list + diff + generate */}
      {uploaded && (
        <div className="bg-white border border-slate-200 rounded-lg p-4 space-y-3">
          <div className="flex justify-between items-baseline">
            <div>
              <span className="text-xs uppercase tracking-wide text-slate-400">
                uploaded
              </span>{" "}
              <code className="text-sm">{uploaded.title}</code>
              <span className="ml-2 text-xs text-slate-500">v{uploaded.version}</span>
              <span className="ml-2 text-xs text-slate-400">
                · prd_id <code>{uploaded.prd_id.slice(0, 8)}</code>…
              </span>
            </div>
            {uploaded.diff_summary && (
              <div className="text-xs">
                {Object.entries(uploaded.diff_summary).map(([k, v]) => (
                  <span
                    key={k}
                    className={
                      "ml-2 px-2 py-0.5 rounded " +
                      (k === "added"
                        ? "bg-emerald-50 text-emerald-700"
                        : k === "modified"
                          ? "bg-amber-50 text-amber-700"
                          : k === "removed"
                            ? "bg-red-50 text-red-700"
                            : "bg-slate-50 text-slate-600")
                    }
                  >
                    {k}: {v}
                  </span>
                ))}
              </div>
            )}
          </div>

          <div className="text-sm">
            <div className="flex items-center gap-3 mb-2">
              <span className="text-slate-500">
                {uploaded.chapters.length} chapters · {selected.size} selected
              </span>
              <button
                className="text-xs text-slate-500 underline hover:text-slate-700"
                onClick={() => setSelected(new Set(uploaded.chapters.map((c) => c.position)))}
              >
                select all
              </button>
              <button
                className="text-xs text-slate-500 underline hover:text-slate-700"
                onClick={() => setSelected(new Set())}
              >
                clear
              </button>
            </div>
            <table className="w-full text-sm">
              <thead className="text-left text-slate-400">
                <tr>
                  <th className="pb-1 w-8"></th>
                  <th className="pb-1 w-12">#</th>
                  <th className="pb-1">title</th>
                  <th className="pb-1 w-16">level</th>
                  <th className="pb-1 w-20">chars</th>
                  <th className="pb-1 w-24">hash</th>
                </tr>
              </thead>
              <tbody>
                {uploaded.chapters.map((c) => (
                  <tr key={c.position} className="border-t border-slate-100">
                    <td className="py-1">
                      <input
                        type="checkbox"
                        checked={selected.has(c.position)}
                        onChange={() => toggleChapter(c.position)}
                      />
                    </td>
                    <td className="text-slate-400 font-mono text-xs">{c.position}</td>
                    <td>
                      <code>{c.title}</code>{" "}
                      <span className="text-xs text-slate-400">
                        ({c.normalized_title})
                      </span>
                    </td>
                    <td className="text-slate-500">H{c.level}</td>
                    <td className="text-slate-500 font-mono text-xs">{c.body_chars}</td>
                    <td className="text-slate-400 font-mono text-xs">{c.hash}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div>
            <button
              className="bg-emerald-700 text-white text-sm px-3 py-1.5 rounded hover:bg-emerald-800 disabled:opacity-50"
              disabled={selected.size === 0 || generate.isPending}
              onClick={() => generate.mutate()}
            >
              {generate.isPending
                ? "generating (≤5 min)…"
                : `Generate cases for ${selected.size} chapter${selected.size === 1 ? "" : "s"}`}
            </button>
            {generate.error && (
              <pre className="text-xs text-red-600 whitespace-pre-wrap mt-2">
                {(generate.error as Error).message}
              </pre>
            )}
          </div>

          {genResult && (
            <div className="bg-slate-50 rounded p-3 text-sm">
              <div className="font-medium mb-1">
                Generated <span className="text-emerald-700">{genResult.total_cases}</span> cases.
              </div>
              <ul className="space-y-1">
                {genResult.results.map((r) => (
                  <li key={r.chapter_index} className="text-xs">
                    <code>#{r.chapter_index}</code> {r.chapter_title}: {" "}
                    {r.error ? (
                      <span className="text-red-600">error: {r.error}</span>
                    ) : (
                      <span className="text-slate-600">
                        {r.saved_count} cases saved → review them on{" "}
                        <a className="text-blue-700 underline" href="/cases">
                          Cases
                        </a>
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      {/* PRD history */}
      <div className="bg-white border border-slate-200 rounded-lg p-4">
        <div className="text-xs uppercase tracking-wide text-slate-400 mb-2">
          PRD history (project: <code>{projectId}</code>)
        </div>
        {list.data?.data?.length ? (
          <table className="w-full text-sm">
            <thead className="text-left text-slate-400">
              <tr>
                <th className="pb-1">name</th>
                <th className="pb-1 w-16">v</th>
                <th className="pb-1 w-20">chapters</th>
                <th className="pb-1 w-44">uploaded_at</th>
                <th className="pb-1 w-20"></th>
              </tr>
            </thead>
            <tbody>
              {list.data.data.map((p) => {
                const isActive = p.prd_id === activePrdId;
                return (
                  <tr
                    key={p.prd_id}
                    className={
                      "border-t border-slate-100 cursor-pointer hover:bg-slate-50 " +
                      (isActive ? "bg-blue-50" : "")
                    }
                    onClick={() => setActivePrdId(p.prd_id)}
                  >
                    <td className="py-1">
                      <code className={isActive ? "text-blue-700 font-medium" : ""}>
                        {p.name}
                      </code>
                      <span className="text-xs text-slate-400 ml-2">
                        ({p.prd_id.slice(0, 8)})
                      </span>
                    </td>
                    <td className="text-slate-500">v{p.version}</td>
                    <td className="text-slate-500">{p.chapter_count}</td>
                    <td className="text-slate-400 font-mono text-xs">
                      {new Date(p.uploaded_at).toLocaleString()}
                    </td>
                    <td onClick={(e) => e.stopPropagation()}>
                      <button
                        className="text-xs text-slate-400 hover:text-red-600 disabled:opacity-50"
                        disabled={
                          deletePrd.isPending && deletePrd.variables === p.prd_id
                        }
                        onClick={() => {
                          if (
                            window.confirm(
                              `Delete ${p.name} v${p.version}?\n\n` +
                                `Generated cases keep living — only the PRD record itself is removed.`,
                            )
                          ) {
                            deletePrd.mutate(p.prd_id);
                          }
                        }}
                        title="delete this PRD"
                      >
                        🗑 delete
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        ) : (
          <span className="text-slate-400 text-sm">no PRDs yet</span>
        )}
        {deletePrd.error && (
          <div className="text-xs text-red-600 mt-2">
            delete error: {(deletePrd.error as Error).message}
          </div>
        )}
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block text-sm">
      <span className="text-slate-500 mb-1 block text-xs uppercase tracking-wide">
        {label}
      </span>
      {children}
    </label>
  );
}

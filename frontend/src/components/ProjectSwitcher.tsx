import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCurrentProject } from "../lib/useCurrentProject";

interface ProjectRow {
  project_id: string;
  name: string;
  base_url: string;
  description?: string;
}

interface ProjectsResponse {
  data: ProjectRow[];
}

/** Header dropdown for the active project. Auto-selects the only project
 * when there's exactly one (preserves the single-project happy path). When
 * there are zero, opens the inline create form so the user can't get
 * stuck on a blank state. */
export function ProjectSwitcher() {
  const { projectId, setProjectId } = useCurrentProject();
  const [creating, setCreating] = useState(false);
  const qc = useQueryClient();

  const projects = useQuery({
    queryKey: ["projects"],
    queryFn: async (): Promise<ProjectsResponse> => {
      const r = await fetch("/api/projects/");
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    },
  });

  // Once the projects list resolves: if the user hasn't picked one yet
  // (or picked one that no longer exists), default to the first.
  useEffect(() => {
    if (!projects.data) return;
    const ids = projects.data.data.map((p) => p.project_id);
    if (ids.length === 0) {
      if (projectId) setProjectId("");
      return;
    }
    if (!projectId || !ids.includes(projectId)) {
      setProjectId(ids[0]);
    }
    // intentional: only re-run when project list changes
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projects.data]);

  const list = projects.data?.data ?? [];

  if (projects.isLoading) {
    return <span className="text-xs text-slate-400">loading projects…</span>;
  }

  if (list.length === 0 || creating) {
    return <CreateProjectInline onDone={() => { setCreating(false); qc.invalidateQueries({ queryKey: ["projects"] }); }} onCancel={list.length > 0 ? () => setCreating(false) : undefined} />;
  }

  return (
    <div className="flex items-center gap-2">
      <span className="text-xs text-slate-400">project</span>
      <select
        className="text-sm font-mono border border-slate-200 rounded px-2 py-0.5 bg-white"
        value={projectId}
        onChange={(e) => setProjectId(e.target.value)}
      >
        {list.map((p) => (
          <option key={p.project_id} value={p.project_id}>
            {p.project_id}
            {p.name && p.name !== p.project_id ? ` · ${p.name}` : ""}
          </option>
        ))}
      </select>
      <button
        onClick={() => setCreating(true)}
        className="text-xs text-blue-700 hover:underline"
        title="create new project"
      >
        + new
      </button>
    </div>
  );
}

function CreateProjectInline({
  onDone,
  onCancel,
}: {
  onDone: (id: string) => void;
  onCancel?: () => void;
}) {
  const { setProjectId } = useCurrentProject();
  const [id, setId] = useState("");
  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");

  const create = useMutation({
    mutationFn: async () => {
      const r = await fetch("/api/projects/", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          project_id: id.trim(),
          name: name.trim() || id.trim(),
          base_url: baseUrl.trim(),
        }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`);
      return r.json();
    },
    onSuccess: () => {
      setProjectId(id.trim());
      onDone(id.trim());
    },
  });

  return (
    <div className="flex items-center gap-1.5 text-xs">
      <span className="text-slate-400">new project</span>
      <input
        className="border border-slate-200 rounded px-2 py-0.5 font-mono w-28"
        placeholder="id (slug)"
        value={id}
        onChange={(e) => setId(e.target.value.toLowerCase().replace(/[^a-z0-9-_]/g, ""))}
      />
      <input
        className="border border-slate-200 rounded px-2 py-0.5 w-32"
        placeholder="name"
        value={name}
        onChange={(e) => setName(e.target.value)}
      />
      <input
        className="border border-slate-200 rounded px-2 py-0.5 w-44"
        placeholder="base_url (optional)"
        value={baseUrl}
        onChange={(e) => setBaseUrl(e.target.value)}
      />
      <button
        disabled={!id || create.isPending}
        onClick={() => create.mutate()}
        className="bg-slate-900 text-white px-2 py-0.5 rounded hover:bg-slate-700 disabled:opacity-50"
      >
        {create.isPending ? "…" : "create"}
      </button>
      {onCancel && (
        <button onClick={onCancel} className="text-slate-500 hover:text-slate-900">
          cancel
        </button>
      )}
      {create.error && (
        <span className="text-red-600 ml-2">
          {(create.error as Error).message.slice(0, 60)}
        </span>
      )}
    </div>
  );
}

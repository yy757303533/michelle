import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCurrentProject } from "../lib/useCurrentProject";

interface ProjectRow {
  project_id: string;
  name: string;
  base_url: string;
  description?: string;
  default_username?: string;
  default_password?: string;
}

interface ProjectsResponse {
  data: ProjectRow[];
}

type Mode = "view" | "create" | "edit";

/** Header dropdown for the active project. Auto-selects the only project
 * when there's exactly one (preserves the single-project happy path). When
 * there are zero, opens the inline create form so the user can't get
 * stuck on a blank state. */
export function ProjectSwitcher() {
  const { projectId, setProjectId } = useCurrentProject();
  const [mode, setMode] = useState<Mode>("view");
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projects.data]);

  const list = projects.data?.data ?? [];
  const current = list.find((p) => p.project_id === projectId) ?? null;

  if (projects.isLoading) {
    return <span className="text-xs text-slate-400">loading projects…</span>;
  }

  // Empty list → force the user to create the first one.
  if (list.length === 0 || mode === "create") {
    return (
      <ProjectForm
        title="new project"
        onDone={(p) => {
          setMode("view");
          qc.invalidateQueries({ queryKey: ["projects"] });
          if (p) setProjectId(p.project_id);
        }}
        onCancel={list.length > 0 ? () => setMode("view") : undefined}
      />
    );
  }

  if (mode === "edit" && current) {
    return (
      <ProjectForm
        title="edit project"
        initial={current}
        onDone={() => {
          setMode("view");
          qc.invalidateQueries({ queryKey: ["projects"] });
        }}
        onCancel={() => setMode("view")}
      />
    );
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
            {p.name}
            {p.name !== p.project_id ? ` · ${p.project_id}` : ""}
          </option>
        ))}
      </select>
      {current && (
        <button
          onClick={() => setMode("edit")}
          className="text-xs text-slate-500 hover:text-slate-900"
          title={`edit ${current.name} (base_url, credentials)`}
        >
          ✎
        </button>
      )}
      <button
        onClick={() => setMode("create")}
        className="text-xs text-blue-700 hover:underline"
        title="create new project"
      >
        + new
      </button>
    </div>
  );
}

/** Inline form for create + edit. The server owns project_id; the user
 * owns name + base_url + credentials. base_url is marked required because
 * cases without a target won't actually run; credentials are optional
 * (some flows don't need login). */
function ProjectForm({
  title,
  initial,
  onDone,
  onCancel,
}: {
  title: string;
  initial?: ProjectRow;
  onDone: (p: ProjectRow | null) => void;
  onCancel?: () => void;
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [baseUrl, setBaseUrl] = useState(initial?.base_url ?? "");
  const [username, setUsername] = useState(initial?.default_username ?? "");
  const [password, setPassword] = useState(initial?.default_password ?? "");
  const [showPwd, setShowPwd] = useState(false);

  const save = useMutation({
    mutationFn: async (): Promise<ProjectRow> => {
      const body: Record<string, string> = {
        name: name.trim(),
        base_url: baseUrl.trim(),
        default_username: username.trim(),
        default_password: password,
      };
      // Sending project_id triggers update on the server; omitting it
      // triggers create + auto-id mint.
      if (initial?.project_id) body.project_id = initial.project_id;
      const r = await fetch("/api/projects/", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`);
      return (await r.json()).data;
    },
    onSuccess: (p) => onDone(p),
  });

  const required = name.trim().length > 0 && baseUrl.trim().length > 0;

  return (
    <div className="flex flex-wrap items-center gap-1.5 text-xs">
      <span className="text-slate-500 mr-1">{title}</span>
      <Required label="name">
        <input
          autoFocus
          className="border border-slate-200 rounded px-2 py-0.5 w-36"
          placeholder="My Web App"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
      </Required>
      <Required label="base_url">
        <input
          className="border border-slate-200 rounded px-2 py-0.5 w-52 font-mono"
          placeholder="http://localhost:5000/"
          value={baseUrl}
          onChange={(e) => setBaseUrl(e.target.value)}
        />
      </Required>
      <Optional label="user">
        <input
          className="border border-slate-200 rounded px-2 py-0.5 w-24"
          placeholder="admin"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
        />
      </Optional>
      <Optional label="password">
        <span className="inline-flex items-center gap-1">
          <input
            type={showPwd ? "text" : "password"}
            className="border border-slate-200 rounded px-2 py-0.5 w-28"
            placeholder="••••"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          <button
            type="button"
            onClick={() => setShowPwd((v) => !v)}
            className="text-slate-400 hover:text-slate-700"
          >
            {showPwd ? "hide" : "show"}
          </button>
        </span>
      </Optional>
      <button
        disabled={!required || save.isPending}
        onClick={() => save.mutate()}
        className="bg-slate-900 text-white px-2 py-0.5 rounded hover:bg-slate-700 disabled:opacity-50"
      >
        {save.isPending ? "…" : initial ? "save" : "create"}
      </button>
      {onCancel && (
        <button onClick={onCancel} className="text-slate-500 hover:text-slate-900 px-1">
          cancel
        </button>
      )}
      {save.error && (
        <span className="text-red-600 ml-1">
          {(save.error as Error).message.slice(0, 80)}
        </span>
      )}
    </div>
  );
}

function Required({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex items-center gap-1">
      <span className="text-slate-500">
        {label}
        <span className="text-red-500 ml-0.5">*</span>
      </span>
      {children}
    </label>
  );
}

function Optional({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex items-center gap-1">
      <span className="text-slate-400">{label}</span>
      {children}
    </label>
  );
}

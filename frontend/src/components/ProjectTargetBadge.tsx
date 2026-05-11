import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "../lib/adminAuth";

interface ProjectRow {
  project_id: string;
  name: string;
  base_url: string;
  login_url: string;
  default_username: string;
}

/** Inline breadcrumb-style badge: tells the user *what they're acting on*
 * without making them open the dashboard. Renders nothing if no project
 * picked or the projects fetch hasn't resolved — silent on the empty
 * state, the page already handles "no project". */
export function ProjectTargetBadge({ projectId }: { projectId: string }) {
  const projects = useQuery({
    queryKey: ["projects"],
    queryFn: async (): Promise<{ data: ProjectRow[] }> => {
      const r = await apiFetch("/api/projects/");
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    },
  });
  const proj = projects.data?.data.find((p) => p.project_id === projectId);
  if (!proj) return null;

  return (
    <span className="text-xs text-slate-500 inline-flex items-center gap-1.5">
      {proj.base_url ? (
        <>
          <span className="text-slate-400">→</span>
          <a
            href={proj.base_url}
            target="_blank"
            rel="noreferrer"
            className="text-slate-600 hover:text-blue-700 font-mono break-all"
            title="open target in new tab"
          >
            {proj.base_url}
          </a>
        </>
      ) : (
        <span className="text-amber-600 font-mono">(no base_url set)</span>
      )}
      {proj.default_username && (
        <>
          <span className="text-slate-300">·</span>
          <span className="font-mono">as {proj.default_username}</span>
        </>
      )}
      {proj.login_url && (
        <>
          <span className="text-slate-300">·</span>
          <span className="font-mono">login set</span>
        </>
      )}
    </span>
  );
}

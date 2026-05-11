import { createFileRoute, Link } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCurrentProject } from "../lib/useCurrentProject";
import { fmtDateTime, fmtMs } from "../lib/datetime";
import { apiFetch } from "../lib/adminAuth";

export const Route = createFileRoute("/queue")({
  component: RunQueuePage,
});

interface QueueRun {
  run_id: string;
  case_id: string;
  project_id: string;
  status: string;
  env: string;
  started_at: string | null;
  created_at: string;
  duration_ms: number | null;
  queue_position: number | null;
  has_live_task: boolean;
  cancelable: boolean;
  age_seconds: number;
  stuck_hint: boolean;
}

interface QueueResponse {
  data: QueueRun[];
  count: number;
  active_task_count: number;
}

function RunQueuePage() {
  const { projectId } = useCurrentProject();
  const qc = useQueryClient();
  const queue = useQuery({
    queryKey: ["run-queue", projectId],
    queryFn: async (): Promise<QueueResponse> => {
      const qs = projectId ? `?project_id=${encodeURIComponent(projectId)}` : "";
      const r = await apiFetch(`/api/runs/queue${qs}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    },
    refetchInterval: 3000,
  });

  const cancel = useMutation({
    mutationFn: async (runId: string) => {
      const r = await apiFetch(`/api/runs/${runId}/cancel`, { method: "POST" });
      if (!r.ok) throw new Error(await r.text());
      return r.json();
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["run-queue"] });
      qc.invalidateQueries({ queryKey: ["runs"] });
      qc.invalidateQueries({ queryKey: ["runs-recent"] });
    },
  });

  const rows = queue.data?.data ?? [];

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-semibold">
          Run Queue{" "}
          {projectId && <span className="text-slate-400 text-base font-normal">/ {projectId}</span>}
        </h1>
        <p className="text-sm text-slate-500 mt-1">
          Pending and running executions. Active tasks: {queue.data?.active_task_count ?? 0}.
        </p>
        <div className="mt-2 flex items-center gap-2">
          <Link
            to="/runs"
            className="text-xs rounded border border-slate-200 bg-white px-2 py-0.5 text-slate-700 hover:border-slate-400"
          >
            all runs →
          </Link>
          <Link
            to="/cases"
            className="text-xs rounded border border-slate-200 bg-white px-2 py-0.5 text-slate-700 hover:border-slate-400"
          >
            cases →
          </Link>
        </div>
        {rows.some((r) => r.stuck_hint) && (
          <div className="mt-3 rounded border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
            Some runs look stuck. Check the run detail or cancel and rerun after self-check passes.
          </div>
        )}
      </div>

      <div className="bg-white border border-slate-200 rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
            <tr>
              <th className="text-left px-3 py-2">run</th>
              <th className="text-left px-3 py-2">case</th>
              <th className="text-left px-3 py-2">status</th>
              <th className="text-left px-3 py-2">created</th>
              <th className="text-left px-3 py-2">duration</th>
              <th className="text-right px-3 py-2">action</th>
            </tr>
          </thead>
          <tbody>
            {queue.isLoading ? (
              <tr>
                <td colSpan={6} className="px-3 py-8 text-center text-slate-400">
                  loading…
                </td>
              </tr>
            ) : rows.length === 0 ? (
              <tr>
                <td colSpan={6} className="px-3 py-8 text-center text-slate-400">
                  queue is empty
                </td>
              </tr>
            ) : (
              rows.map((r) => (
                <tr key={r.run_id} className="border-t border-slate-100">
                  <td className="px-3 py-2 font-mono text-xs">
                    <Link to="/runs/$id" params={{ id: r.run_id }} className="text-blue-700 hover:underline">
                      {r.run_id.slice(0, 8)}…
                    </Link>
                  </td>
                  <td className="px-3 py-2 font-mono text-xs">
                    <Link to="/cases" className="hover:underline">
                      {r.case_id}
                    </Link>
                  </td>
                  <td className="px-3 py-2">
                    <span className="font-mono text-xs px-2 py-0.5 rounded bg-slate-100">
                      {r.status}
                    </span>
                    {r.status === "pending" && r.queue_position && (
                      <span className="ml-2 text-xs text-slate-400">#{r.queue_position}</span>
                    )}
                    {!r.has_live_task && r.status === "running" && (
                      <span className="ml-2 text-xs text-amber-700">orphan check pending</span>
                    )}
                    {r.stuck_hint && (
                      <span className="ml-2 text-xs text-red-700">stuck</span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-xs text-slate-500">{fmtDateTime(r.created_at)}</td>
                  <td className="px-3 py-2 text-xs text-slate-500">
                    {r.duration_ms == null ? fmtMs(r.age_seconds * 1000) : fmtMs(r.duration_ms)}
                  </td>
                  <td className="px-3 py-2 text-right">
                    <button
                      disabled={!r.cancelable || cancel.isPending}
                      onClick={() => cancel.mutate(r.run_id)}
                      className="text-xs px-2 py-0.5 rounded border border-red-200 text-red-700 hover:bg-red-50 disabled:opacity-50"
                    >
                      cancel
                    </button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
      {cancel.error && (
        <pre className="text-xs text-red-600 whitespace-pre-wrap">{(cancel.error as Error).message}</pre>
      )}
    </div>
  );
}

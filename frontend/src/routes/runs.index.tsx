import { createFileRoute, Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useCurrentProject } from "../lib/useCurrentProject";

export const Route = createFileRoute("/runs/")({
  component: RunsListPage,
});

interface RunRow {
  run_id: string;
  case_id: string;
  project_id: string;
  env: string;
  status: string;
  duration_ms: number | null;
  input_tokens: number;
  output_tokens: number;
  started_at: string | null;
  ended_at: string | null;
  created_at: string;
}

interface RunsResponse {
  data: RunRow[];
  count: number;
}

const STATUSES = ["", "pending", "running", "passed", "failed", "flaky", "aborted"];

function RunsListPage() {
  const { projectId } = useCurrentProject();
  const [filter, setFilter] = useState<string>("");

  const runs = useQuery({
    queryKey: ["runs", projectId, filter],
    enabled: Boolean(projectId),
    queryFn: async (): Promise<RunsResponse> => {
      const r = await fetch(`/api/runs/?limit=200&project_id=${encodeURIComponent(projectId)}`);
      const body = await r.json();
      if (!filter) return body;
      return {
        ...body,
        data: body.data.filter((x: RunRow) => x.status === filter),
        count: body.data.filter((x: RunRow) => x.status === filter).length,
      };
    },
    refetchInterval: 5000,
  });

  const grouped: Record<string, number> = {};
  for (const r of runs.data?.data ?? []) grouped[r.status] = (grouped[r.status] ?? 0) + 1;

  if (!projectId) {
    return (
      <div className="bg-white border border-slate-200 rounded-lg p-8 text-center text-sm text-slate-500">
        Pick a project from the header dropdown to see its runs.
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">
          Runs <span className="text-slate-400 text-base font-normal">/ {projectId}</span>
        </h1>
        <p className="text-slate-500 text-sm mt-1">
          Every execution of a test case lives here. Click a row for the live timeline.
        </p>
      </div>

      <div className="flex items-center gap-2">
        {STATUSES.map((s) => {
          const n = s ? grouped[s] ?? 0 : runs.data?.count ?? 0;
          const active = filter === s;
          return (
            <button
              key={s || "all"}
              onClick={() => setFilter(s)}
              className={
                "text-sm px-3 py-1 rounded border " +
                (active
                  ? "bg-slate-900 text-white border-slate-900"
                  : "bg-white text-slate-700 border-slate-200 hover:border-slate-400")
              }
            >
              {s || "all"} <span className="text-xs opacity-60">({n})</span>
            </button>
          );
        })}
      </div>

      <div className="bg-white border border-slate-200 rounded-lg overflow-hidden">
        {runs.isLoading ? (
          <div className="p-6 text-slate-400 text-sm">loading…</div>
        ) : (runs.data?.count ?? 0) === 0 ? (
          <div className="p-8 text-center text-slate-400 text-sm">
            no runs yet — head to{" "}
            <Link to="/cases" className="text-blue-700 underline">
              Cases
            </Link>{" "}
            and click ▶ Run on an approved case
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-left text-slate-400 border-b border-slate-100">
              <tr>
                <th className="p-2">run_id</th>
                <th className="p-2">case</th>
                <th className="p-2 w-20">env</th>
                <th className="p-2 w-24">status</th>
                <th className="p-2 w-20">duration</th>
                <th className="p-2 w-32">tokens</th>
                <th className="p-2 w-44">started</th>
              </tr>
            </thead>
            <tbody>
              {runs.data?.data.map((r) => (
                <tr key={r.run_id} className="border-t border-slate-100 hover:bg-slate-50">
                  <td className="p-2 font-mono text-xs">
                    <Link to="/runs/$id" params={{ id: r.run_id }} className="text-blue-700 hover:underline">
                      {r.run_id.slice(0, 12)}…
                    </Link>
                  </td>
                  <td className="p-2 font-mono text-xs">
                    <Link to="/cases" className="hover:underline">
                      {r.case_id}
                    </Link>
                  </td>
                  <td className="p-2 text-xs text-slate-500">{r.env}</td>
                  <td className="p-2">
                    <StatusPill status={r.status} />
                  </td>
                  <td className="p-2 font-mono text-xs">{fmtMs(r.duration_ms)}</td>
                  <td className="p-2 font-mono text-xs text-slate-500">
                    {r.input_tokens}/{r.output_tokens}
                  </td>
                  <td className="p-2 text-xs text-slate-500">
                    {r.started_at ? new Date(r.started_at).toLocaleString() : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

function StatusPill({ status }: { status: string }) {
  const m: Record<string, string> = {
    pending: "bg-slate-100 text-slate-600",
    running: "bg-blue-100 text-blue-700",
    passed: "bg-emerald-100 text-emerald-700",
    failed: "bg-red-100 text-red-700",
    flaky: "bg-amber-100 text-amber-700",
    aborted: "bg-slate-300 text-slate-700",
  };
  return (
    <span className={"text-xs px-2 py-0.5 rounded-full font-mono " + (m[status] || m.pending)}>
      {status === "running" && <span className="inline-block animate-pulse mr-1">●</span>}
      {status}
    </span>
  );
}

function fmtMs(ms: number | null): string {
  if (ms == null) return "—";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

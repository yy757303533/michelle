import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useCurrentProject } from "../lib/useCurrentProject";
import { ProjectTargetBadge } from "../components/ProjectTargetBadge";

const RERUNNABLE = new Set(["failed", "aborted", "flaky"]);

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
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [filter, setFilter] = useState<string>("");
  // Track per-row selection by run_id so the user can pick exactly which
  // failures to rerun. Cleared on filter change so a hidden run can't sneak
  // into the next bulk action (same guard as the cases page).
  const [selected, setSelected] = useState<Set<string>>(new Set());

  // Always fetch the full set for this project — filtering happens at
  // render time only. The previous version filtered inside `queryFn`,
  // so picking "running" left the cache holding 2 rows instead of all
  // 3, which collapsed every other pill's count to 0.
  const runs = useQuery({
    queryKey: ["runs", projectId],
    enabled: Boolean(projectId),
    queryFn: async (): Promise<RunsResponse> => {
      const r = await fetch(`/api/runs/?limit=200&project_id=${encodeURIComponent(projectId)}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    },
    refetchInterval: 5000,
  });

  /** Spawn fresh runs for the selected case_ids. We deliberately don't
   * mutate the original failed Run rows — they stay as-is for forensics
   * and so the diagnoser keeps its target. The new runs show up at the
   * top of the list with status=pending and run independently. */
  const rerun = useMutation({
    mutationFn: async (
      caseIds: string[],
    ): Promise<{ data: { run_ids: string[] } }> => {
      // Dedupe — a case might have failed multiple times; rerunning it
      // should fire ONE new run, not N.
      const unique = [...new Set(caseIds)];
      const r = await fetch("/api/runs/", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ case_ids: unique, env: "default" }),
      });
      if (!r.ok) throw new Error(await r.text());
      return r.json();
    },
    onSuccess: (resp) => {
      qc.invalidateQueries({ queryKey: ["runs"] });
      qc.invalidateQueries({ queryKey: ["runs-recent"] });
      // For a single rerun, jump to its detail page so the user can watch
      // the new attempt. For bulk, stay on the list and let the rows
      // appear via the 5s poll.
      if (resp.data.run_ids.length === 1) {
        navigate({ to: "/runs/$id", params: { id: resp.data.run_ids[0] } });
      }
    },
  });

  const allRuns = runs.data?.data ?? [];

  // Counts come from the full set so pills stay stable when the user
  // toggles between filters.
  const grouped = useMemo(() => {
    const out: Record<string, number> = {};
    for (const r of allRuns) out[r.status] = (out[r.status] ?? 0) + 1;
    return out;
  }, [allRuns]);

  const visible = useMemo(
    () => (filter ? allRuns.filter((r) => r.status === filter) : allRuns),
    [allRuns, filter],
  );

  const visibleRerunnable = useMemo(
    () => visible.filter((r) => RERUNNABLE.has(r.status)),
    [visible],
  );
  const allRerunnableSelected =
    visibleRerunnable.length > 0 &&
    visibleRerunnable.every((r) => selected.has(r.run_id));

  const toggleRow = (runId: string) => {
    const next = new Set(selected);
    if (next.has(runId)) next.delete(runId);
    else next.add(runId);
    setSelected(next);
  };
  const toggleSelectAll = () => {
    if (allRerunnableSelected) setSelected(new Set());
    else setSelected(new Set(visibleRerunnable.map((r) => r.run_id)));
  };

  const selectedRows = visible.filter((r) => selected.has(r.run_id));
  const selectedRerunnable = selectedRows.filter((r) => RERUNNABLE.has(r.status));
  const selectedUniqueCases = new Set(selectedRerunnable.map((r) => r.case_id));

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
        <div className="mt-1">
          <ProjectTargetBadge projectId={projectId} />
        </div>
        <p className="text-slate-500 text-sm mt-1">
          Every execution of a test case lives here. Click a row for the live timeline.
        </p>
      </div>

      <div className="flex items-center gap-2">
        {STATUSES.map((s) => {
          const n = s ? (grouped[s] ?? 0) : allRuns.length;
          const active = filter === s;
          return (
            <button
              key={s || "all"}
              onClick={() => {
                setFilter(s);
                setSelected(new Set());
              }}
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
        {/* Bulk rerun for all visible failed/aborted/flaky. Pulls case_ids
            out of the currently-rendered set so the filter pills double as
            scope selectors — pick "failed" then click rerun to retry just
            those, or "all" to retry every non-terminal-success run. */}
        {/* "All visible failed" rerun, only when nothing is selected.
            Switches to per-selection rerun in the bulk bar below once
            the user picks rows — having both at once was confusing. */}
        {selected.size === 0 && (() => {
          const rerunable = visible.filter((r) => RERUNNABLE.has(r.status));
          const uniqueCases = new Set(rerunable.map((r) => r.case_id));
          if (uniqueCases.size === 0) return null;
          return (
            <button
              disabled={rerun.isPending}
              onClick={() => {
                if (
                  window.confirm(
                    `Rerun ${uniqueCases.size} unique case${uniqueCases.size > 1 ? "s" : ""} from ${rerunable.length} failed/aborted/flaky run${rerunable.length > 1 ? "s" : ""}?\n\n` +
                      `New runs land in pending and execute under the current concurrency cap. The original failed runs stay untouched for forensics.`,
                  )
                ) {
                  rerun.mutate(rerunable.map((r) => r.case_id));
                }
              }}
              className="ml-auto text-sm px-3 py-1 rounded bg-amber-600 text-white hover:bg-amber-700 disabled:opacity-50"
              title={`spawn fresh runs for ${uniqueCases.size} case(s) that previously failed`}
            >
              {rerun.isPending
                ? "scheduling…"
                : `↻ Rerun ${uniqueCases.size} failed`}
            </button>
          );
        })()}
      </div>

      {/* Bulk action bar — only shows when at least one row is checked */}
      {selected.size > 0 && (
        <div className="flex items-center gap-3 bg-slate-900 text-white rounded px-3 py-2 text-sm">
          <span>{selected.size} selected</span>
          {selectedRows.length !== selectedRerunnable.length && (
            <span className="text-xs text-amber-300">
              ({selectedRows.length - selectedRerunnable.length} skipped: only failed/aborted/flaky can rerun)
            </span>
          )}
          <button
            disabled={selectedUniqueCases.size === 0 || rerun.isPending}
            onClick={() => {
              const ids = [...selectedUniqueCases];
              if (
                window.confirm(
                  `Rerun ${ids.length} unique case${ids.length > 1 ? "s" : ""} from ${selectedRerunnable.length} selected run${selectedRerunnable.length > 1 ? "s" : ""}?\n\n` +
                    `Originals stay untouched.`,
                )
              ) {
                rerun.mutate(ids);
                setSelected(new Set());
              }
            }}
            className="bg-amber-600 px-3 py-0.5 rounded hover:bg-amber-500 disabled:opacity-50"
            title={
              selectedUniqueCases.size === 0
                ? "no rerunnable rows in selection (only failed/aborted/flaky can rerun)"
                : `${selectedUniqueCases.size} unique cases will be rerun`
            }
          >
            {rerun.isPending
              ? "scheduling…"
              : `↻ Rerun ${selectedUniqueCases.size > 0 ? `(${selectedUniqueCases.size})` : ""}`}
          </button>
          <button
            onClick={() => setSelected(new Set())}
            className="text-slate-300 px-3 py-0.5 rounded hover:text-white ml-auto"
          >
            clear
          </button>
        </div>
      )}

      <div className="bg-white border border-slate-200 rounded-lg overflow-hidden">
        {runs.isLoading ? (
          <div className="p-6 text-slate-400 text-sm">loading…</div>
        ) : allRuns.length === 0 ? (
          <div className="p-8 text-center text-slate-400 text-sm">
            no runs yet — head to{" "}
            <Link to="/cases" className="text-blue-700 underline">
              Cases
            </Link>{" "}
            and click ▶ Run on an approved case
          </div>
        ) : visible.length === 0 ? (
          <div className="p-8 text-center text-slate-400 text-sm">
            no runs in <code>{filter}</code> state — pick another filter or
            wait for new runs
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-left text-slate-400 border-b border-slate-100">
              <tr>
                <th className="p-2 w-8">
                  {visibleRerunnable.length > 0 && (
                    <input
                      type="checkbox"
                      checked={allRerunnableSelected}
                      onChange={toggleSelectAll}
                      title={
                        allRerunnableSelected
                          ? "deselect all"
                          : `select all ${visibleRerunnable.length} rerunnable run${visibleRerunnable.length > 1 ? "s" : ""}`
                      }
                    />
                  )}
                </th>
                <th className="p-2">run_id</th>
                <th className="p-2">case</th>
                <th className="p-2 w-20">env</th>
                <th className="p-2 w-24">status</th>
                <th className="p-2 w-20">duration</th>
                <th className="p-2 w-32">tokens</th>
                <th className="p-2 w-44">started</th>
                <th className="p-2 w-16"></th>
              </tr>
            </thead>
            <tbody>
              {visible.map((r) => (
                <tr key={r.run_id} className="border-t border-slate-100 hover:bg-slate-50">
                  <td className="p-2">
                    {RERUNNABLE.has(r.status) && (
                      <input
                        type="checkbox"
                        checked={selected.has(r.run_id)}
                        onChange={() => toggleRow(r.run_id)}
                      />
                    )}
                  </td>
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
                  <td className="p-2 text-right">
                    {RERUNNABLE.has(r.status) && (
                      <button
                        disabled={rerun.isPending}
                        onClick={() => rerun.mutate([r.case_id])}
                        className="text-xs px-2 py-0.5 rounded bg-amber-600 text-white hover:bg-amber-700 disabled:opacity-50"
                        title={`rerun ${r.case_id} as a fresh run`}
                      >
                        ↻ rerun
                      </button>
                    )}
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

import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useCurrentProject } from "../lib/useCurrentProject";
import { ProjectTargetBadge } from "../components/ProjectTargetBadge";
import {
  isLLMRunnerBlocked,
  LLMRunnerStatusLight,
} from "../components/LLMRunnerStatusLight";
import { fmtDateTime, fmtMs } from "../lib/datetime";
import { useLLMRunnerStatus } from "../lib/useLLMRunnerStatus";
import { apiFetch } from "../lib/adminAuth";

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
  deleted_at?: string | null;
  source_case_deleted_at?: string | null;
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
  const llmRunner = useLLMRunnerStatus();
  const llmStatus = llmRunner.data?.status ?? "unknown";
  const llmDetail = llmRunner.data?.detail ?? "";
  const runnerBlocked = isLLMRunnerBlocked(llmStatus);
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
      const r = await apiFetch(`/api/runs/?limit=200&project_id=${encodeURIComponent(projectId)}`);
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
      const r = await apiFetch("/api/runs/", {
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

  // List shows the LATEST run per case (1 row per case_id) — historical
  // runs of the same case live behind the row's "× N" badge and on the
  // detail page's history section. Without dedup, a case that's been
  // rerun 5 times floods the list with 5 rows that all say roughly the
  // same thing, and bulk operations need awkward dedup math afterward.
  const { latestRows, historyCounts } = useMemo(() => {
    const byCase = new Map<string, RunRow>();
    const counts = new Map<string, number>();
    for (const r of allRuns) {
      counts.set(r.case_id, (counts.get(r.case_id) ?? 0) + 1);
      const prev = byCase.get(r.case_id);
      // allRuns is desc by created_at from the backend, so the first
      // row we see for a case_id is its latest.
      if (!prev) byCase.set(r.case_id, r);
    }
    return {
      latestRows: [...byCase.values()].sort(
        (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
      ),
      historyCounts: counts,
    };
  }, [allRuns]);

  // Counts on filter pills reflect the deduped set so "failed (3)" means
  // "3 cases whose latest run failed" — which is what you actually want
  // to act on. Total run rows in DB are visible only via the × N badges.
  const grouped = useMemo(() => {
    const out: Record<string, number> = {};
    for (const r of latestRows) out[r.status] = (out[r.status] ?? 0) + 1;
    return out;
  }, [latestRows]);

  const visible = useMemo(
    () => (filter ? latestRows.filter((r) => r.status === filter) : latestRows),
    [latestRows, filter],
  );

  const visibleRerunnable = useMemo(
    () => visible.filter((r) => RERUNNABLE.has(r.status) && !r.source_case_deleted_at),
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
  const selectedRerunnable = selectedRows.filter(
    (r) => RERUNNABLE.has(r.status) && !r.source_case_deleted_at,
  );
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
        <div className="mt-2 flex items-center gap-2">
          <LLMRunnerStatusLight data={llmRunner.data} loading={llmRunner.isLoading} />
          <a
            href={`/api/projects/${encodeURIComponent(projectId)}/report.html`}
            target="_blank"
            rel="noreferrer"
            className="text-xs rounded border border-slate-200 bg-white px-2 py-0.5 text-slate-700 hover:border-slate-400"
          >
            project aggregate report ↗
          </a>
          <Link
            to="/queue"
            className="text-xs rounded border border-slate-200 bg-white px-2 py-0.5 text-slate-700 hover:border-slate-400"
          >
            run queue →
          </Link>
          {runnerBlocked && (
            <span className="text-xs text-amber-700">
              Rerun is disabled until the selected execution loop is ready.
            </span>
          )}
        </div>
      </div>

      <div className="flex items-center gap-2">
        {STATUSES.map((s) => {
          const n = s ? (grouped[s] ?? 0) : latestRows.length;
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
              disabled={rerun.isPending || runnerBlocked}
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
              title={
                runnerBlocked
                  ? `executor ${llmStatus}: ${llmDetail || "not ready"}`
                  : `spawn fresh runs for ${uniqueCases.size} case(s) that previously failed`
              }
            >
              {rerun.isPending
                ? "scheduling…"
                : `↻ Rerun ${uniqueCases.size} failed`}
            </button>
          );
        })()}
      </div>

      {/* Bulk action bar — only shows when at least one row is checked */}
      {selected.size > 0 && (() => {
        // Make the dedup math explicit: same case appearing in 3 failed
        // runs reruns ONCE, not 3 times — but the user shouldn't have to
        // figure that out from a single "(N)" on the button.
        const dupCount = selectedRerunnable.length - selectedUniqueCases.size;
        const skippedCount = selectedRows.length - selectedRerunnable.length;
        return (
        <div className="flex flex-wrap items-center gap-3 bg-slate-900 text-white rounded px-3 py-2 text-sm">
          <span>{selected.size} selected</span>
          <span className="text-xs text-slate-400">·</span>
          <span className="text-xs">
            <span className="text-emerald-300">{selectedUniqueCases.size}</span> unique case{selectedUniqueCases.size === 1 ? "" : "s"} will rerun
          </span>
          {dupCount > 0 && (
            <span className="text-xs text-slate-400" title="multiple selected runs share the same case_id; rerun fires one new run per case">
              · {dupCount} duplicate{dupCount > 1 ? "s" : ""} merged
            </span>
          )}
          {skippedCount > 0 && (
            <span className="text-xs text-amber-300">
              · {skippedCount} skipped (only failed/aborted/flaky can rerun)
            </span>
          )}
          <button
            disabled={selectedUniqueCases.size === 0 || rerun.isPending || runnerBlocked}
            onClick={() => {
              const ids = [...selectedUniqueCases];
              const dupNote =
                dupCount > 0
                  ? `\n\n${selectedRerunnable.length} selected runs → ${ids.length} unique cases (same case selected ${dupCount > 1 ? "multiple times" : "twice"} fires only one new run).`
                  : "";
              if (
                window.confirm(
                  `Rerun ${ids.length} unique case${ids.length > 1 ? "s" : ""}?${dupNote}\n\n` +
                    `Originals stay untouched.`,
                )
              ) {
                rerun.mutate(ids);
                setSelected(new Set());
              }
            }}
            className="bg-amber-600 px-3 py-0.5 rounded hover:bg-amber-500 disabled:opacity-50"
            title={
              runnerBlocked
                ? `executor ${llmStatus}: ${llmDetail || "not ready"}`
                : selectedUniqueCases.size === 0
                ? "no rerunnable rows in selection (only failed/aborted/flaky can rerun)"
                : `${selectedUniqueCases.size} unique case_id(s) — same case_id selected multiple times fires one new run`
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
        );
      })()}

      <p className="text-xs text-slate-400 -mt-3">
        Showing the latest run per case. {allRuns.length > latestRows.length && (
          <>
            {allRuns.length - latestRows.length} earlier run{allRuns.length - latestRows.length > 1 ? "s" : ""} hidden — open a row to see its history.
          </>
        )}
      </p>

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
                    <a
                      href={`/cases?project_id=${encodeURIComponent(r.project_id)}&case_id=${encodeURIComponent(r.case_id)}`}
                      className="hover:underline"
                    >
                      {r.case_id}
                    </a>
                    {r.source_case_deleted_at && (
                      <span
                        className="ml-2 rounded bg-amber-50 px-1.5 py-0.5 text-[10px] text-amber-700"
                        title="source case has been deleted; restore the case before rerunning"
                      >
                        case deleted
                      </span>
                    )}
                    {(historyCounts.get(r.case_id) ?? 0) > 1 && (
                      <Link
                        to="/runs/$id"
                        params={{ id: r.run_id }}
                        className="ml-2 inline-block text-[10px] px-1.5 py-0.5 rounded bg-slate-100 text-slate-600 hover:bg-slate-200"
                        title={`${historyCounts.get(r.case_id)} total runs for this case — click to open detail page with full history`}
                      >
                        × {historyCounts.get(r.case_id)}
                      </Link>
                    )}
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
                    {fmtDateTime(r.started_at)}
                  </td>
                  <td className="p-2 text-right">
                    {RERUNNABLE.has(r.status) && (
                      <button
                        disabled={rerun.isPending || runnerBlocked || Boolean(r.source_case_deleted_at)}
                        onClick={() => rerun.mutate([r.case_id])}
                        className="text-xs px-2 py-0.5 rounded bg-amber-600 text-white hover:bg-amber-700 disabled:opacity-50"
                        title={
                          r.source_case_deleted_at
                            ? "restore the source case before rerunning"
                            : runnerBlocked
                            ? `executor ${llmStatus}: ${llmDetail || "not ready"}`
                            : `rerun ${r.case_id} as a fresh run`
                        }
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

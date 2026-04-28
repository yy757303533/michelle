import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useCurrentProject } from "../lib/useCurrentProject";
import { ProjectTargetBadge } from "../components/ProjectTargetBadge";
import { fmtDateTime, fmtMs } from "../lib/datetime";

export const Route = createFileRoute("/cases")({
  component: CasesPage,
});

interface CaseRow {
  case_id: string;
  project_id: string;
  name: string;
  intent: string;
  module: string;
  tags: string[];
  priority: string;
  auth_state: string;
  source: string;
  prompt_version: string;
  model_version: string;
  generated_from: string | null;
  review_status: string;
  manual_edited_fields: string[];
  steps: Array<{ intent: string; expected?: string }>;
  assertions: Array<{ description: string }>;
  preconditions: string[];
  version: number;
  created_at: string;
}

interface CasesResponse {
  data: CaseRow[];
  count: number;
  counts_by_status: Record<string, number>;
}

const STATUS_FILTERS: Array<{ key: string; label: string }> = [
  { key: "", label: "all" },
  { key: "pending", label: "pending" },
  { key: "approved", label: "approved" },
  { key: "rejected", label: "rejected" },
  { key: "stale", label: "stale" },
];

function CasesPage() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const { projectId } = useCurrentProject();
  const [filter, setFilter] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [creating, setCreating] = useState(false);
  // Free-text fuzzy filter on top of the status pills. Matches case-insensitive
  // substrings across the fields a human would actually search on (case_id,
  // name, intent, module, tags, auth_state). Pure client-side — the cases
  // query already returns the full project's set, no extra round trip.
  const [searchQuery, setSearchQuery] = useState("");

  const cases = useQuery({
    // Re-key on project so swapping the global selector re-fetches.
    queryKey: ["cases", projectId, filter],
    enabled: Boolean(projectId),
    queryFn: async (): Promise<CasesResponse> => {
      const params = new URLSearchParams({ project_id: projectId, limit: "200" });
      if (filter) params.set("status", filter);
      const r = await fetch(`/api/cases/?${params}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`);
      return r.json();
    },
  });

  // Latest run per case_id for the per-row "last run: failed · 138s · 5m ago"
  // summary. Polls every 5s so the user can watch a freshly-triggered batch
  // light up. Pulled from /api/runs/ rather than tracked locally because the
  // truth is in the run history — works after page reload, in another tab,
  // and for runs the user didn't trigger from this page.
  interface RunRow {
    run_id: string;
    case_id: string;
    status: string;
    duration_ms: number | null;
    started_at: string | null;
    created_at: string;
  }
  const projectRuns = useQuery({
    queryKey: ["cases-overlay-runs", projectId],
    enabled: Boolean(projectId),
    refetchInterval: 5000,
    queryFn: async (): Promise<{ data: RunRow[] }> => {
      const r = await fetch(
        `/api/runs/?project_id=${encodeURIComponent(projectId)}&limit=500`,
      );
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    },
  });

  /** Map case_id → latest run. /api/runs/ is desc by created_at, so the
   * first row we see for a case is its most-recent run. */
  const lastRunByCase = useMemo(() => {
    const out = new Map<string, RunRow>();
    if (!projectRuns.data) return out;
    for (const r of projectRuns.data.data) {
      if (!out.has(r.case_id)) out.set(r.case_id, r);
    }
    return out;
  }, [projectRuns.data]);

  const review = useMutation({
    mutationFn: async ({ id, action }: { id: string; action: "approve" | "reject" | "reset" }) => {
      const r = await fetch(`/api/cases/${id}/review`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action }),
      });
      if (!r.ok) throw new Error(await r.text());
      return r.json();
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["cases"] }),
  });

  const bulk = useMutation({
    mutationFn: async (action: "approve" | "reject" | "reset") => {
      const r = await fetch("/api/cases/bulk-review", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ case_ids: [...selected], action }),
      });
      if (!r.ok) throw new Error(await r.text());
      return r.json();
    },
    onSuccess: () => {
      setSelected(new Set());
      qc.invalidateQueries({ queryKey: ["cases"] });
    },
  });

  const bulkDelete = useMutation({
    mutationFn: async (
      ids: string[],
    ): Promise<{
      data: { deleted: string[]; skipped_approved: string[]; missing: string[] };
    }> => {
      const r = await fetch("/api/cases/bulk-delete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ case_ids: ids }),
      });
      if (!r.ok) throw new Error(await r.text());
      return r.json();
    },
    onSuccess: (resp) => {
      const skipped = resp.data.skipped_approved.length;
      // Tell the user when approved cases were spared so they don't think
      // the action lied. The deleted count is already obvious from the
      // shrunken list.
      if (skipped > 0) {
        window.alert(
          `Deleted ${resp.data.deleted.length} cases. ` +
            `${skipped} approved case${skipped > 1 ? "s were" : " was"} skipped — ` +
            `reject ${skipped > 1 ? "them" : "it"} first if you really want ${skipped > 1 ? "them" : "it"} removed.`,
        );
      }
      setSelected(new Set());
      qc.invalidateQueries({ queryKey: ["cases"] });
      qc.invalidateQueries({ queryKey: ["cases-summary"] });
    },
  });

  const editMut = useMutation({
    mutationFn: async ({ id, patch }: { id: string; patch: Partial<CaseRow> }) => {
      const r = await fetch(`/api/cases/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch),
      });
      if (!r.ok) throw new Error(await r.text());
      return r.json();
    },
    onSuccess: () => {
      setEditing(null);
      qc.invalidateQueries({ queryKey: ["cases"] });
    },
  });

  const deleteMut = useMutation({
    mutationFn: async (id: string) => {
      const r = await fetch(`/api/cases/${id}`, { method: "DELETE" });
      if (!r.ok && r.status !== 204) {
        // 409 = approved-protection guard from backend; surface verbatim.
        throw new Error(await r.text());
      }
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["cases"] }),
  });

  const createMut = useMutation({
    mutationFn: async (body: NewCaseDraft): Promise<{ data: CaseRow }> => {
      const r = await fetch("/api/cases/", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...body, project_id: projectId }),
      });
      if (!r.ok) throw new Error(await r.text());
      return r.json();
    },
    onSuccess: () => {
      setCreating(false);
      qc.invalidateQueries({ queryKey: ["cases"] });
      qc.invalidateQueries({ queryKey: ["cases-summary"] });
    },
  });

  const runMut = useMutation({
    mutationFn: async (case_id: string): Promise<{ data: { run_ids: string[] } }> => {
      const r = await fetch("/api/runs/", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ case_ids: [case_id], env: "default" }),
      });
      if (!r.ok) throw new Error(await r.text());
      return r.json();
    },
    onSuccess: (resp) => {
      const id = resp?.data?.run_ids?.[0];
      if (id) navigate({ to: "/runs/$id", params: { id } });
    },
  });

  const bulkRun = useMutation({
    mutationFn: async (
      caseIds: string[],
    ): Promise<{ data: { run_ids: string[] } }> => {
      const r = await fetch("/api/runs/", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ case_ids: caseIds, env: "default" }),
      });
      if (!r.ok) throw new Error(await r.text());
      return r.json();
    },
    onSuccess: () => {
      // Land on the runs list rather than a single run — for a batch the
      // user wants the overview, not one specific timeline.
      setSelected(new Set());
      qc.invalidateQueries({ queryKey: ["runs"] });
      qc.invalidateQueries({ queryKey: ["runs-recent"] });
      navigate({ to: "/runs" });
    },
  });

  const counts = cases.data?.counts_by_status ?? {};
  const allRows = cases.data?.data ?? [];
  // Apply the free-text filter on top of the server-side status filter.
  // Matches against the human-meaningful fields: id, name, intent, module,
  // tags joined, and auth_state. Multi-token query → all tokens must match
  // (AND), so "登录 P0" narrows to login-related P0 cases.
  const visible = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    if (!q) return allRows;
    const tokens = q.split(/\s+/);
    return allRows.filter((c) => {
      const haystack = [
        c.case_id,
        c.name,
        c.intent,
        c.module,
        c.priority,
        c.review_status,
        c.auth_state,
        (c.tags ?? []).join(" "),
      ]
        .join(" ")
        .toLowerCase();
      return tokens.every((t) => haystack.includes(t));
    });
  }, [allRows, searchQuery]);
  const allSelected = visible.length > 0 && visible.every((c) => selected.has(c.case_id));

  const toggleSelect = (id: string) => {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setSelected(next);
  };
  const toggleSelectAll = () => {
    if (allSelected) setSelected(new Set());
    else setSelected(new Set(visible.map((c) => c.case_id)));
  };

  if (!projectId) {
    return (
      <div className="bg-white border border-slate-200 rounded-lg p-8 text-center text-sm text-slate-500">
        Pick a project from the header dropdown to see its cases.
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-semibold">
          Test cases <span className="text-slate-400 text-base font-normal">/ {projectId}</span>
        </h1>
        <div className="mt-1">
          <ProjectTargetBadge projectId={projectId} />
        </div>
        <p className="text-slate-500 text-sm mt-1">
          AI drafts → review → run. Edits to approved cases re-open them as pending.
          {runMut.error && (
            <span className="ml-2 text-red-600">run error: {(runMut.error as Error).message}</span>
          )}
          {review.error && (
            <span className="ml-2 text-red-600">review error: {(review.error as Error).message}</span>
          )}
          {bulk.error && (
            <span className="ml-2 text-red-600">bulk error: {(bulk.error as Error).message}</span>
          )}
          {editMut.error && (
            <span className="ml-2 text-red-600">edit error: {(editMut.error as Error).message}</span>
          )}
          {cases.error && (
            <span className="ml-2 text-red-600">load error: {(cases.error as Error).message}</span>
          )}
        </p>
      </div>

      {/* Filter pills + search + new case */}
      <div className="flex flex-wrap items-center gap-2">
        {STATUS_FILTERS.map((f) => {
          const n = f.key
            ? (counts[f.key] ?? 0)
            : Object.values(counts).reduce((a, b) => a + b, 0);
          const active = filter === f.key;
          return (
            <button
              key={f.key}
              onClick={() => {
                setFilter(f.key);
                // Without this, IDs selected under one filter remain in
                // `selected` after switching tabs; bulk-review would then act
                // on rows the user can't see.
                setSelected(new Set());
              }}
              className={
                "text-sm px-3 py-1 rounded border " +
                (active
                  ? "bg-slate-900 text-white border-slate-900"
                  : "bg-white text-slate-700 border-slate-200 hover:border-slate-400")
              }
            >
              {f.label} <span className="text-xs opacity-60">({n})</span>
            </button>
          );
        })}
        <div className="relative ml-2 flex-1 min-w-[200px] max-w-md">
          <input
            type="search"
            value={searchQuery}
            onChange={(e) => {
              setSearchQuery(e.target.value);
              // Same hidden-row guard as filter pill switches: a query
              // that hides a checked row shouldn't let it stowaway into
              // the next bulk action.
              setSelected(new Set());
            }}
            placeholder="搜索 name / intent / module / tags…  (空格分隔多关键字)"
            className="w-full text-sm px-3 py-1 rounded border border-slate-200 focus:border-slate-400 focus:outline-none"
          />
          {searchQuery && (
            <button
              onClick={() => setSearchQuery("")}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-700 text-sm"
              title="clear search"
            >
              ×
            </button>
          )}
        </div>
        <div className="ml-auto">
          <button
            onClick={() => setCreating((v) => !v)}
            className="text-sm px-3 py-1 rounded bg-blue-700 text-white hover:bg-blue-800"
          >
            {creating ? "× cancel" : "+ new case"}
          </button>
        </div>
      </div>

      {/* Search summary line — only when actually searching */}
      {searchQuery && (
        <div className="text-xs text-slate-500 -mt-2">
          {visible.length} of {allRows.length} cases match{" "}
          <code className="font-mono">{searchQuery}</code>
          {filter && (
            <>
              {" "}
              within <code>{filter}</code>
            </>
          )}
        </div>
      )}

      {creating && (
        <NewCaseFormPanel
          busy={createMut.isPending}
          error={createMut.error as Error | null}
          onSubmit={(draft) => createMut.mutate(draft)}
          onCancel={() => setCreating(false)}
        />
      )}

      {/* Bulk action bar */}
      {selected.size > 0 && (() => {
        // The selection partitions cleanly into review_status buckets
        // (pending+stale | approved | rejected). We show that partition on
        // the top line — those four numbers DO sum to `selected.size`.
        // The action buttons below show their target-set cardinality, which
        // can overlap (Approve and Reject both target pending; Run is a
        // subset of Revert), so they intentionally don't sum to anything.
        const selectedRows = visible.filter((c) => selected.has(c.case_id));
        const pendingCount = selectedRows.filter((c) => c.review_status === "pending").length;
        const staleCount = selectedRows.filter((c) => c.review_status === "stale").length;
        const approvedCount = selectedRows.filter((c) => c.review_status === "approved").length;
        const rejectedCount = selectedRows.filter((c) => c.review_status === "rejected").length;
        const reviewableCount = pendingCount + staleCount;          // approve/reject target
        const reviewedCount = approvedCount + rejectedCount;        // revert target
        const deletableCount = selected.size - approvedCount;       // delete target (approved are protected)
        return (
        <div className="bg-slate-900 text-white rounded px-3 py-2 text-sm space-y-1">
          {/* Top row: explicit partition that sums to `selected.size` */}
          <div className="flex items-center gap-3 text-slate-300">
            <span className="text-white font-medium">{selected.size} selected</span>
            <span className="text-slate-500">=</span>
            {pendingCount > 0 && <span>{pendingCount} pending</span>}
            {staleCount > 0 && <span>+ {staleCount} stale</span>}
            {approvedCount > 0 && (
              <span>{pendingCount + staleCount > 0 ? "+ " : ""}{approvedCount} approved</span>
            )}
            {rejectedCount > 0 && (
              <span>{pendingCount + staleCount + approvedCount > 0 ? "+ " : ""}{rejectedCount} rejected</span>
            )}
            <button
              onClick={() => setSelected(new Set())}
              className="text-slate-400 hover:text-white ml-auto"
            >
              clear
            </button>
          </div>

          {/* Bottom row: actions. Each number is the cardinality of THAT
              button's target set; numbers may overlap across buttons. */}
          <div className="flex items-center gap-3">
          <button
            disabled={
              reviewableCount === 0 ||
              bulk.isPending || bulkDelete.isPending || bulkRun.isPending
            }
            onClick={() => bulk.mutate("approve")}
            className="bg-emerald-600 px-3 py-0.5 rounded hover:bg-emerald-500 disabled:opacity-50"
            title={
              reviewableCount === 0
                ? "no pending/stale cases in selection"
                : `${reviewableCount} pending/stale → approved`
            }
          >
            ✓ Approve {reviewableCount > 0 ? `(${reviewableCount})` : ""}
          </button>
          <button
            disabled={
              reviewableCount === 0 ||
              bulk.isPending || bulkDelete.isPending || bulkRun.isPending
            }
            onClick={() => bulk.mutate("reject")}
            className="bg-red-600 px-3 py-0.5 rounded hover:bg-red-500 disabled:opacity-50"
            title={
              reviewableCount === 0
                ? "no pending/stale cases in selection"
                : `${reviewableCount} pending/stale → rejected`
            }
          >
            ✗ Reject {reviewableCount > 0 ? `(${reviewableCount})` : ""}
          </button>
          <button
            disabled={
              reviewedCount === 0 ||
              bulk.isPending || bulkDelete.isPending || bulkRun.isPending
            }
            onClick={() => {
              if (
                window.confirm(
                  `Revert ${reviewedCount} case${reviewedCount > 1 ? "s" : ""} back to pending?\n\n` +
                    `This undoes the approve/reject verdict so they re-enter the review queue.`,
                )
              ) {
                bulk.mutate("reset");
              }
            }}
            className="bg-amber-600 px-3 py-0.5 rounded hover:bg-amber-500 disabled:opacity-50"
            title={
              reviewedCount === 0
                ? "no approved/rejected cases in selection"
                : `${approvedCount} approved + ${rejectedCount} rejected → pending`
            }
          >
            ↺ Revert {reviewedCount > 0 ? `(${reviewedCount})` : ""}
          </button>
          <button
            disabled={
              approvedCount === 0 ||
              bulkRun.isPending ||
              bulk.isPending ||
              bulkDelete.isPending
            }
            onClick={() => {
              const ids = selectedRows
                .filter((c) => c.review_status === "approved")
                .map((c) => c.case_id);
              const skipped = selected.size - ids.length;
              const note =
                skipped > 0
                  ? `\n\n${skipped} non-approved case${skipped > 1 ? "s" : ""} in selection will be skipped — only approved cases run.`
                  : "";
              if (
                window.confirm(
                  `Run ${ids.length} approved case${ids.length > 1 ? "s" : ""}?` +
                    note +
                    `\n\nEach case spawns its own Chromium + claude session, gated by MAX_CONCURRENT_RUNS (default 2).`,
                )
              ) {
                bulkRun.mutate(ids);
              }
            }}
            className="bg-blue-600 px-3 py-0.5 rounded hover:bg-blue-500 disabled:opacity-50"
            title={
              approvedCount === 0
                ? "no approved cases in selection"
                : `${approvedCount} approved → run`
            }
          >
            ▶ Run {approvedCount > 0 ? `(${approvedCount})` : ""}
          </button>
          <button
            disabled={deletableCount === 0 || bulk.isPending || bulkDelete.isPending}
            onClick={() => {
              const ids = [...selected];
              if (
                window.confirm(
                  `Delete ${deletableCount} case${deletableCount > 1 ? "s" : ""}?\n\n` +
                    (approvedCount > 0
                      ? `${approvedCount} approved case${approvedCount > 1 ? "s" : ""} in selection will be skipped — reject ${approvedCount > 1 ? "them" : "it"} first if you want ${approvedCount > 1 ? "them" : "it"} gone too.\n`
                      : "") +
                    `This cannot be undone.`,
                )
              ) {
                bulkDelete.mutate(ids);
              }
            }}
            className="bg-slate-700 hover:bg-slate-600 px-3 py-0.5 rounded disabled:opacity-50"
            title={
              deletableCount === 0
                ? "selection contains only approved cases — reject first to delete"
                : approvedCount > 0
                  ? `${deletableCount} deletable; ${approvedCount} approved skipped`
                  : `${deletableCount} → deleted`
            }
          >
            🗑 Delete {deletableCount > 0 ? `(${deletableCount})` : ""}
          </button>
          </div>
        </div>
        );
      })()}

      <div className="bg-white border border-slate-200 rounded-lg">
        {cases.isLoading ? (
          <div className="p-6 text-slate-400 text-sm">loading…</div>
        ) : visible.length === 0 ? (
          <div className="p-6 text-slate-400 text-sm">
            no cases yet — head to{" "}
            <a className="text-blue-700 underline" href="/prd">
              PRD
            </a>{" "}
            to upload one
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-left text-slate-400 border-b border-slate-100">
              <tr>
                <th className="p-2 w-8">
                  <input type="checkbox" checked={allSelected} onChange={toggleSelectAll} />
                </th>
                <th className="p-2 w-44">case_id</th>
                <th className="p-2">name</th>
                <th className="p-2 w-20">priority</th>
                <th className="p-2 w-24">module</th>
                <th className="p-2 w-24">status</th>
                <th className="p-2 w-40">last run</th>
                <th className="p-2 w-32">edited</th>
                <th className="p-2 w-48">actions</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((c) => (
                <CaseRowView
                  key={c.case_id}
                  c={c}
                  lastRun={lastRunByCase.get(c.case_id) ?? null}
                  expanded={expanded === c.case_id}
                  editing={editing === c.case_id}
                  selected={selected.has(c.case_id)}
                  onSelect={() => toggleSelect(c.case_id)}
                  onToggle={() =>
                    setExpanded((prev) => (prev === c.case_id ? null : c.case_id))
                  }
                  onApprove={() => review.mutate({ id: c.case_id, action: "approve" })}
                  onReject={() => review.mutate({ id: c.case_id, action: "reject" })}
                  onReset={() => review.mutate({ id: c.case_id, action: "reset" })}
                  onRun={() => runMut.mutate(c.case_id)}
                  onEdit={() => setEditing(c.case_id)}
                  onCancelEdit={() => setEditing(null)}
                  onSubmitEdit={(patch) =>
                    editMut.mutate({ id: c.case_id, patch })
                  }
                  onDelete={() => {
                    if (window.confirm(`Delete ${c.case_id}? This cannot be undone.`)) {
                      deleteMut.mutate(c.case_id);
                    }
                  }}
                  busy={review.isPending && review.variables?.id === c.case_id}
                  runBusy={runMut.isPending && runMut.variables === c.case_id}
                  editBusy={editMut.isPending && editMut.variables?.id === c.case_id}
                  deleteBusy={deleteMut.isPending && deleteMut.variables === c.case_id}
                />
              ))}
            </tbody>
          </table>
        )}
        {deleteMut.error && (
          <div className="text-xs text-red-600 px-3 py-2 border-t border-red-100 bg-red-50">
            delete error: {(deleteMut.error as Error).message}
          </div>
        )}
      </div>
    </div>
  );
}

interface NewCaseDraft {
  name: string;
  intent: string;
  module: string;
  priority: "P0" | "P1" | "P2";
  steps: Array<{ intent: string; expected?: string }>;
  assertions: Array<{ description: string }>;
  preconditions: string[];
  tags: string[];
}

/** Inline panel for hand-authoring a case. Same `intent | expected` line
 * format as the row-level edit form so users only learn one mini-DSL. */
function NewCaseFormPanel({
  busy,
  error,
  onSubmit,
  onCancel,
}: {
  busy: boolean;
  error: Error | null;
  onSubmit: (d: NewCaseDraft) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState("");
  const [intent, setIntent] = useState("");
  const [module, setModule] = useState("");
  const [priority, setPriority] = useState<"P0" | "P1" | "P2">("P1");
  const [stepsRaw, setStepsRaw] = useState("");
  const [assertionsRaw, setAssertionsRaw] = useState("");

  const submit = () => {
    if (!name.trim()) return;
    const steps = stepsRaw
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line) => {
        const idx = line.indexOf("|");
        if (idx === -1) return { intent: line.trim() };
        const i = line.slice(0, idx).trim();
        const e = line.slice(idx + 1).trim();
        return e ? { intent: i, expected: e } : { intent: i };
      });
    const assertions = assertionsRaw
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean)
      .map((d) => ({ description: d }));
    onSubmit({
      name: name.trim(),
      intent: intent.trim(),
      module: module.trim(),
      priority,
      steps,
      assertions,
      preconditions: [],
      tags: [],
    });
  };

  const required = name.trim().length > 0;

  return (
    <div className="bg-amber-50 border border-amber-200 rounded p-4 space-y-3">
      <div className="text-xs uppercase tracking-wide text-amber-700">
        new case · lands in pending so it still goes through review
      </div>
      <div className="grid grid-cols-2 gap-3 text-sm">
        <Field label="name *">
          <input
            autoFocus
            className="border border-slate-200 rounded px-2 py-1 w-full"
            placeholder="user can log in with valid credentials"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </Field>
        <Field label="priority">
          <select
            className="border border-slate-200 rounded px-2 py-1 w-full"
            value={priority}
            onChange={(e) => setPriority(e.target.value as "P0" | "P1" | "P2")}
          >
            <option>P0</option>
            <option>P1</option>
            <option>P2</option>
          </select>
        </Field>
        <Field label="module">
          <input
            className="border border-slate-200 rounded px-2 py-1 w-full"
            placeholder="auth"
            value={module}
            onChange={(e) => setModule(e.target.value)}
          />
        </Field>
        <Field label="intent (one-liner)">
          <input
            className="border border-slate-200 rounded px-2 py-1 w-full"
            placeholder="verify login redirects to dashboard"
            value={intent}
            onChange={(e) => setIntent(e.target.value)}
          />
        </Field>
        <Field label="steps (one per line: `intent | expected`)" full>
          <textarea
            className="border border-slate-200 rounded p-2 w-full font-mono text-xs"
            rows={4}
            placeholder={`open /login | login form visible\ntype "admin" into username\nclick submit | redirected to /home`}
            value={stepsRaw}
            onChange={(e) => setStepsRaw(e.target.value)}
          />
        </Field>
        <Field label="assertions (one per line)" full>
          <textarea
            className="border border-slate-200 rounded p-2 w-full font-mono text-xs"
            rows={2}
            placeholder={`URL contains /home\nuser badge is visible`}
            value={assertionsRaw}
            onChange={(e) => setAssertionsRaw(e.target.value)}
          />
        </Field>
      </div>
      <div className="flex items-center gap-2">
        <button
          disabled={!required || busy}
          onClick={submit}
          className="bg-slate-900 text-white text-sm px-3 py-1 rounded hover:bg-slate-700 disabled:opacity-50"
        >
          {busy ? "creating…" : "Create"}
        </button>
        <button onClick={onCancel} className="text-sm text-slate-600 hover:text-slate-900 px-2">
          Cancel
        </button>
        {error && (
          <span className="text-red-600 text-xs ml-2">{error.message}</span>
        )}
      </div>
    </div>
  );
}

function DeleteBtn({ busy, onDelete }: { busy: boolean; onDelete: () => void }) {
  return (
    <button
      disabled={busy}
      onClick={onDelete}
      className="text-xs px-2 py-0.5 rounded text-slate-400 hover:text-red-600 hover:bg-red-50 disabled:opacity-50"
      title="delete this case"
    >
      🗑
    </button>
  );
}

interface CaseRowLastRun {
  run_id: string;
  status: string;
  duration_ms: number | null;
  started_at: string | null;
  created_at: string;
}

function CaseRowView({
  c,
  lastRun,
  expanded,
  editing,
  selected,
  onSelect,
  onToggle,
  onApprove,
  onReject,
  onReset,
  onRun,
  onEdit,
  onCancelEdit,
  onSubmitEdit,
  onDelete,
  busy,
  runBusy,
  editBusy,
  deleteBusy,
}: {
  c: CaseRow;
  lastRun: CaseRowLastRun | null;
  expanded: boolean;
  editing: boolean;
  selected: boolean;
  onSelect: () => void;
  onToggle: () => void;
  onApprove: () => void;
  onReject: () => void;
  onReset: () => void;
  onRun: () => void;
  onEdit: () => void;
  onCancelEdit: () => void;
  onSubmitEdit: (patch: Partial<CaseRow>) => void;
  onDelete: () => void;
  busy: boolean;
  runBusy: boolean;
  editBusy: boolean;
  deleteBusy: boolean;
}) {
  return (
    <>
      <tr
        className="border-t border-slate-100 hover:bg-slate-50 cursor-pointer"
        onClick={onToggle}
      >
        <td className="p-2" onClick={(e) => e.stopPropagation()}>
          <input type="checkbox" checked={selected} onChange={onSelect} />
        </td>
        <td className="p-2 font-mono text-xs">{c.case_id}</td>
        <td className="p-2">
          <div className="font-medium">{c.name}</div>
          <div className="text-xs text-slate-500 truncate">{c.intent}</div>
        </td>
        <td className="p-2">
          <span
            className={
              "text-xs px-1.5 py-0.5 rounded font-mono " +
              (c.priority === "P0"
                ? "bg-red-50 text-red-700"
                : c.priority === "P1"
                  ? "bg-amber-50 text-amber-700"
                  : "bg-slate-100 text-slate-600")
            }
          >
            {c.priority}
          </span>
        </td>
        <td className="p-2 text-xs text-slate-500">{c.module}</td>
        <td className="p-2">
          <StatusPill status={c.review_status} />
        </td>
        <td className="p-2 text-xs" onClick={(e) => e.stopPropagation()}>
          {lastRun ? (
            <Link
              to="/runs/$id"
              params={{ id: lastRun.run_id }}
              className="block hover:underline"
              title={`open run ${lastRun.run_id.slice(0, 8)}`}
            >
              <RunStatusDot status={lastRun.status} />{" "}
              <span className="font-mono">{fmtMs(lastRun.duration_ms)}</span>
              <div className="text-[10px] text-slate-400">
                {fmtDateTime(lastRun.started_at ?? lastRun.created_at)}
              </div>
            </Link>
          ) : (
            <span className="text-slate-300">never</span>
          )}
        </td>
        <td className="p-2">
          {c.manual_edited_fields.length === 0 ? (
            <span className="text-slate-300 text-xs">—</span>
          ) : (
            <span
              className="text-xs text-amber-700 font-mono"
              title={c.manual_edited_fields.join(", ")}
            >
              ✎ {c.manual_edited_fields.length} field
              {c.manual_edited_fields.length > 1 ? "s" : ""}
            </span>
          )}
        </td>
        <td className="p-2 whitespace-nowrap" onClick={(e) => e.stopPropagation()}>
          {c.review_status === "pending" || c.review_status === "stale" ? (
            <>
              <button
                className="text-xs px-2 py-0.5 rounded bg-emerald-700 text-white hover:bg-emerald-800 disabled:opacity-50 mr-1"
                disabled={busy}
                onClick={onApprove}
              >
                approve
              </button>
              <button
                className="text-xs px-2 py-0.5 rounded bg-red-700 text-white hover:bg-red-800 disabled:opacity-50 mr-1"
                disabled={busy}
                onClick={onReject}
              >
                reject
              </button>
              <button
                className="text-xs px-2 py-0.5 rounded bg-slate-200 text-slate-700 hover:bg-slate-300 mr-1"
                onClick={() => {
                  if (!expanded) onToggle();
                  onEdit();
                }}
              >
                edit
              </button>
              <DeleteBtn busy={deleteBusy} onDelete={onDelete} />
            </>
          ) : c.review_status === "approved" ? (
            <>
              <button
                className="text-xs px-2 py-0.5 rounded bg-blue-700 text-white hover:bg-blue-800 disabled:opacity-50 mr-1"
                disabled={runBusy}
                onClick={onRun}
              >
                {runBusy ? "starting…" : "▶ Run"}
              </button>
              <button
                className="text-xs px-2 py-0.5 rounded bg-slate-200 text-slate-700 hover:bg-slate-300 mr-1"
                onClick={() => {
                  if (!expanded) onToggle();
                  onEdit();
                }}
                title="editing an approved case re-opens it as pending"
              >
                edit
              </button>
              <button
                className="text-xs px-2 py-0.5 rounded bg-amber-100 text-amber-800 hover:bg-amber-200 disabled:opacity-50"
                disabled={busy}
                onClick={onReset}
                title="revert to pending — undo the approve verdict without editing"
              >
                ↺ revert
              </button>
              {/* No delete on approved — user must reject first. The
                  contract is that approved == human-confirmed, and one
                  click shouldn't be able to drop that signal. */}
            </>
          ) : c.review_status === "rejected" ? (
            <>
              <button
                className="text-xs px-2 py-0.5 rounded bg-slate-200 text-slate-700 hover:bg-slate-300 mr-1"
                onClick={() => {
                  if (!expanded) onToggle();
                  onEdit();
                }}
                title="edit re-opens the case as pending"
              >
                edit
              </button>
              <button
                className="text-xs px-2 py-0.5 rounded bg-amber-100 text-amber-800 hover:bg-amber-200 disabled:opacity-50 mr-1"
                disabled={busy}
                onClick={onReset}
                title="revert to pending — undo the reject verdict"
              >
                ↺ revert
              </button>
              <DeleteBtn busy={deleteBusy} onDelete={onDelete} />
            </>
          ) : (
            <DeleteBtn busy={deleteBusy} onDelete={onDelete} />
          )}
        </td>
      </tr>
      {expanded && !editing && (
        <tr>
          <td colSpan={9} className="p-3 bg-slate-50">
            <div className="grid grid-cols-3 gap-4 text-xs">
              <Block title="preconditions">
                {c.preconditions.length ? (
                  <ul className="list-disc pl-4 space-y-1">
                    {c.preconditions.map((p, i) => (
                      <li key={i}>{p}</li>
                    ))}
                  </ul>
                ) : (
                  <span className="text-slate-400">—</span>
                )}
              </Block>
              <Block title="steps">
                <ol className="list-decimal pl-4 space-y-1">
                  {c.steps.map((s, i) => (
                    <li key={i}>
                      {s.intent}
                      {s.expected && (
                        <div className="text-slate-500 italic">→ {s.expected}</div>
                      )}
                    </li>
                  ))}
                </ol>
              </Block>
              <Block title="assertions">
                <ul className="list-disc pl-4 space-y-1">
                  {c.assertions.map((a, i) => (
                    <li key={i}>{a.description}</li>
                  ))}
                </ul>
              </Block>
            </div>
            <div className="mt-3 text-xs text-slate-400 font-mono">
              tags: {c.tags.join(", ") || "—"} · source: {c.source} · prompt:{" "}
              {c.prompt_version} · model: {c.model_version} · from:{" "}
              {c.generated_from || "—"} · v{c.version}
              {c.manual_edited_fields.length > 0 && (
                <span className="ml-2 text-amber-700">
                  · edited: {c.manual_edited_fields.join(", ")}
                </span>
              )}
            </div>
          </td>
        </tr>
      )}
      {expanded && editing && (
        <EditForm
          c={c}
          onCancel={onCancelEdit}
          onSubmit={onSubmitEdit}
          busy={editBusy}
        />
      )}
    </>
  );
}

function EditForm({
  c,
  onCancel,
  onSubmit,
  busy,
}: {
  c: CaseRow;
  onCancel: () => void;
  onSubmit: (patch: Partial<CaseRow>) => void;
  busy: boolean;
}) {
  const [name, setName] = useState(c.name);
  const [intent, setIntent] = useState(c.intent);
  const [module, setModule] = useState(c.module);
  const [priority, setPriority] = useState(c.priority);
  const [stepsRaw, setStepsRaw] = useState(
    c.steps
      .map((s) => `${s.intent}${s.expected ? `  | ${s.expected}` : ""}`)
      .join("\n"),
  );
  const [assertionsRaw, setAssertionsRaw] = useState(
    c.assertions.map((a) => a.description).join("\n"),
  );

  const submit = () => {
    const patch: Partial<CaseRow> = {};
    if (name !== c.name) patch.name = name;
    if (intent !== c.intent) patch.intent = intent;
    if (module !== c.module) patch.module = module;
    if (priority !== c.priority) patch.priority = priority;

    const newSteps = stepsRaw
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line) => {
        // Split on the FIRST `|` only — `expected` may legitimately contain
        // pipes (regex literals, table cells in the description, …).
        const idx = line.indexOf("|");
        if (idx === -1) return { intent: line.trim() };
        const intent = line.slice(0, idx).trim();
        const expected = line.slice(idx + 1).trim();
        return expected ? { intent, expected } : { intent };
      });
    if (JSON.stringify(newSteps) !== JSON.stringify(c.steps)) patch.steps = newSteps;

    const newAssertions = assertionsRaw
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean)
      .map((d) => ({ description: d }));
    if (JSON.stringify(newAssertions) !== JSON.stringify(c.assertions))
      patch.assertions = newAssertions;

    if (Object.keys(patch).length === 0) {
      onCancel();
      return;
    }
    onSubmit(patch);
  };

  return (
    <tr>
      <td colSpan={9} className="p-4 bg-amber-50 border-t border-amber-200">
        <div className="text-xs uppercase tracking-wide text-amber-700 mb-3">
          edit case · {c.case_id}
        </div>
        <div className="grid grid-cols-2 gap-3 text-sm">
          <Field label="name">
            <input
              className="border border-slate-200 rounded px-2 py-1 w-full"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </Field>
          <Field label="priority">
            <select
              className="border border-slate-200 rounded px-2 py-1 w-full"
              value={priority}
              onChange={(e) => setPriority(e.target.value)}
            >
              <option>P0</option>
              <option>P1</option>
              <option>P2</option>
            </select>
          </Field>
          <Field label="intent" full>
            <textarea
              className="border border-slate-200 rounded px-2 py-1 w-full"
              rows={2}
              value={intent}
              onChange={(e) => setIntent(e.target.value)}
            />
          </Field>
          <Field label="module">
            <input
              className="border border-slate-200 rounded px-2 py-1 w-full"
              value={module}
              onChange={(e) => setModule(e.target.value)}
            />
          </Field>
          <Field label="steps (one per line, format: intent | expected)" full>
            <textarea
              className="border border-slate-200 rounded px-2 py-1 w-full font-mono text-xs"
              rows={Math.max(4, c.steps.length + 1)}
              value={stepsRaw}
              onChange={(e) => setStepsRaw(e.target.value)}
            />
          </Field>
          <Field label="assertions (one per line)" full>
            <textarea
              className="border border-slate-200 rounded px-2 py-1 w-full font-mono text-xs"
              rows={Math.max(2, c.assertions.length + 1)}
              value={assertionsRaw}
              onChange={(e) => setAssertionsRaw(e.target.value)}
            />
          </Field>
        </div>
        <div className="mt-3 flex items-center gap-2">
          <button
            className="bg-emerald-700 text-white text-sm px-3 py-1 rounded hover:bg-emerald-800 disabled:opacity-50"
            disabled={busy}
            onClick={submit}
          >
            {busy ? "saving…" : "save (→ pending)"}
          </button>
          <button
            className="bg-white text-slate-700 text-sm px-3 py-1 rounded border border-slate-200 hover:bg-slate-50"
            onClick={onCancel}
          >
            cancel
          </button>
          <span className="text-xs text-amber-700 ml-2">
            edited fields will be tracked and protected from future LLM regenerations.
          </span>
        </div>
      </td>
    </tr>
  );
}

function Field({
  label,
  children,
  full,
}: {
  label: string;
  children: React.ReactNode;
  full?: boolean;
}) {
  return (
    <label className={"block " + (full ? "col-span-2" : "")}>
      <span className="text-xs text-slate-500 mb-1 block uppercase tracking-wide">
        {label}
      </span>
      {children}
    </label>
  );
}

function StatusPill({ status }: { status: string }) {
  const colors: Record<string, string> = {
    pending: "bg-amber-50 text-amber-700",
    approved: "bg-emerald-50 text-emerald-700",
    rejected: "bg-red-50 text-red-700",
    stale: "bg-slate-100 text-slate-500",
  };
  return (
    <span
      className={
        "text-xs px-1.5 py-0.5 rounded font-mono " +
        (colors[status] || "bg-slate-100 text-slate-600")
      }
    >
      {status}
    </span>
  );
}

/** Compact run-status indicator for tight rows: a coloured dot + label.
 * Reused on /cases to render the per-row "last run" summary. */
function RunStatusDot({ status }: { status: string }) {
  const colors: Record<string, string> = {
    pending: "bg-slate-400",
    running: "bg-blue-500 animate-pulse",
    passed: "bg-emerald-500",
    failed: "bg-red-500",
    flaky: "bg-amber-500",
    aborted: "bg-slate-400",
  };
  return (
    <span className="inline-flex items-center gap-1 font-mono">
      <span
        className={"inline-block w-1.5 h-1.5 rounded-full " + (colors[status] ?? "bg-slate-300")}
      />
      <span className="text-slate-700">{status}</span>
    </span>
  );
}

function Block({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-xs uppercase tracking-wide text-slate-400 mb-1">{title}</div>
      {children}
    </div>
  );
}

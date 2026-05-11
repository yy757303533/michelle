import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { useCurrentProject } from "../lib/useCurrentProject";
import { ProjectTargetBadge } from "../components/ProjectTargetBadge";
import {
  isLLMRunnerBlocked,
  LLMRunnerStatusLight,
} from "../components/LLMRunnerStatusLight";
import { fmtDateTime, fmtMs } from "../lib/datetime";
import { useLLMRunnerStatus } from "../lib/useLLMRunnerStatus";
import { apiFetch } from "../lib/adminAuth";

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
  assertions: Array<{
    description: string;
    source?: "prd_explicit" | "domain_inferred" | "exploratory";
    confidence?: number;
    evidence?: string;
    rationale?: string;
  }>;
  quality?: {
    score?: number;
    severity?: "low" | "medium" | "high" | string;
    flags?: string[];
    avg_assertion_confidence?: number;
    reviewer_notes?: string[];
  };
  preconditions: string[];
  version: number;
  created_at: string;
}

interface CasesResponse {
  data: CaseRow[];
  count: number;
  counts_by_status: Record<string, number>;
  total: number;
  truncated: boolean;
}

interface CaseFeedbackRow {
  feedback_id: string;
  case_id: string;
  project_id: string;
  generated_from: string | null;
  generation_job_id: string | null;
  category: FeedbackCategory;
  note: string;
  evidence: string;
  status: "open" | "resolved";
  resolved_by_commit: string | null;
  created_at: string;
}

interface CaseFeedbackResponse {
  data: CaseFeedbackRow[];
  count: number;
  summary: Array<{ category: FeedbackCategory; status: "open" | "resolved"; count: number }>;
}

type FeedbackCategory =
  | "prompt_rule_missing"
  | "prd_context_missing"
  | "hallucinated_requirement"
  | "missed_requirement"
  | "wrong_auth_state"
  | "not_browser_executable"
  | "duplicate_or_low_value"
  | "executor_limitation";

const FEEDBACK_CATEGORIES: FeedbackCategory[] = [
  "prompt_rule_missing",
  "prd_context_missing",
  "hallucinated_requirement",
  "missed_requirement",
  "wrong_auth_state",
  "not_browser_executable",
  "duplicate_or_low_value",
  "executor_limitation",
];

const STATUS_FILTERS: Array<{ key: string; label: string }> = [
  { key: "", label: "all" },
  { key: "pending", label: "pending" },
  { key: "approved", label: "approved" },
  { key: "rejected", label: "rejected" },
  { key: "stale", label: "stale" },
];

const ACTIVE_RUN_STATUSES = new Set(["pending", "running"]);
const CASE_ID_QUERY_KEY = "case_id";

function readCaseIdFromUrl(): string {
  if (typeof window === "undefined") return "";
  return new URLSearchParams(window.location.search).get(CASE_ID_QUERY_KEY) ?? "";
}

function CasesPage() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const { projectId } = useCurrentProject();
  // Reachability of the LLM proxy that the executor talks to. The Run button
  // grays itself out when the proxy is "down" / "starting" so users don't
  // hit the cryptic "API returned empty/malformed response" error during boot.
  const llmRunner = useLLMRunnerStatus();
  const llmStatus = llmRunner.data?.status ?? "unknown";
  const llmDetail = llmRunner.data?.detail ?? "";
  const runnerBlocked = isLLMRunnerBlocked(llmStatus);
  const [filter, setFilter] = useState("");
  const initialCaseId = readCaseIdFromUrl();
  const [expanded, setExpanded] = useState<string | null>(initialCaseId || null);
  const [editing, setEditing] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [creating, setCreating] = useState(false);
  // Free-text fuzzy filter on top of the status pills. The backend applies
  // this before pagination so large projects don't need a 5000-row fetch.
  const [searchQuery, setSearchQuery] = useState(initialCaseId);

  // Server-side pagination. `all` keeps the old operator escape hatch but is
  // still capped by the API's max limit.
  const [pageSize, setPageSize] = useState<number | "all">(100);
  const [page, setPage] = useState(1);
  const effectiveLimit = pageSize === "all" ? 5000 : pageSize;
  const offset = pageSize === "all" ? 0 : (page - 1) * pageSize;

  const cases = useQuery({
    // Re-key on project so swapping the global selector re-fetches.
    queryKey: ["cases", projectId, filter, searchQuery, effectiveLimit, offset],
    enabled: Boolean(projectId),
    queryFn: async (): Promise<CasesResponse> => {
      const params = new URLSearchParams({
        project_id: projectId,
        limit: String(effectiveLimit),
        offset: String(offset),
      });
      if (filter) params.set("status", filter);
      if (searchQuery.trim()) params.set("q", searchQuery.trim());
      const r = await apiFetch(`/api/cases/?${params}`);
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
      const r = await apiFetch(
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
  const activeRunCaseIds = useMemo(() => {
    const out = new Set<string>();
    for (const r of projectRuns.data?.data ?? []) {
      if (ACTIVE_RUN_STATUSES.has(r.status)) out.add(r.case_id);
    }
    return out;
  }, [projectRuns.data]);

  const feedback = useQuery({
    queryKey: ["case-feedback", projectId],
    enabled: Boolean(projectId),
    queryFn: async (): Promise<CaseFeedbackResponse> => {
      const r = await apiFetch(
        `/api/case-feedback/?project_id=${encodeURIComponent(projectId)}&status=open&limit=200`,
      );
      if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`);
      return r.json();
    },
  });

  const createFeedback = useMutation({
    mutationFn: async (body: {
      case_id: string;
      category: FeedbackCategory;
      note: string;
      evidence: string;
    }): Promise<{ data: CaseFeedbackRow }> => {
      const r = await apiFetch("/api/case-feedback/", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error(await r.text());
      return r.json();
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["case-feedback"] });
    },
  });

  const resolveFeedback = useMutation({
    mutationFn: async (feedbackId: string): Promise<{ data: CaseFeedbackRow }> => {
      const r = await apiFetch(`/api/case-feedback/${feedbackId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: "resolved" }),
      });
      if (!r.ok) throw new Error(await r.text());
      return r.json();
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["case-feedback"] }),
  });

  const review = useMutation({
    mutationFn: async ({ id, action }: { id: string; action: "approve" | "reject" | "reset" }) => {
      const r = await apiFetch(`/api/cases/${id}/review`, {
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
      const r = await apiFetch("/api/cases/bulk-review", {
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
      const r = await apiFetch("/api/cases/bulk-delete", {
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
      const r = await apiFetch(`/api/cases/${id}`, {
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
      const r = await apiFetch(`/api/cases/${id}`, { method: "DELETE" });
      if (!r.ok && r.status !== 204) {
        // 409 = approved-protection guard from backend; surface verbatim.
        throw new Error(await r.text());
      }
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["cases"] }),
  });

  const createMut = useMutation({
    mutationFn: async (body: NewCaseDraft): Promise<{ data: CaseRow }> => {
      const r = await apiFetch("/api/cases/", {
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
      const r = await apiFetch("/api/runs/", {
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
      const r = await apiFetch("/api/runs/", {
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
  const visible = cases.data?.data ?? [];
  const total = cases.data?.total ?? 0;
  const allSelected = visible.length > 0 && visible.every((c) => selected.has(c.case_id));

  // Pagination math. Bulk-select acts on the currently loaded page.
  const totalPages = pageSize === "all" ? 1 : Math.max(1, Math.ceil(total / pageSize));
  // Clamp page when filter/search/delete shrinks the dataset under the cursor.
  useEffect(() => {
    if (page > totalPages) setPage(totalPages);
  }, [page, totalPages]);
  // Snap back to page 1 when the user changes filter/search/page-size — the
  // old offset rarely lines up with the new dataset.
  useEffect(() => {
    setPage(1);
  }, [filter, searchQuery, pageSize]);
  useEffect(() => {
    const linkedCaseId = readCaseIdFromUrl();
    if (!linkedCaseId) return;
    setFilter("");
    setSearchQuery(linkedCaseId);
    setExpanded(linkedCaseId);
    setPage(1);
  }, []);
  const pagedVisible = visible;
  const pageStart = total === 0 ? 0 : offset + 1;
  const pageEnd = offset + visible.length;

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
        <div className="mt-2 flex items-center gap-2">
          <LLMRunnerStatusLight data={llmRunner.data} loading={llmRunner.isLoading} />
          <Link
            to="/queue"
            className="text-xs rounded border border-slate-200 bg-white px-2 py-0.5 text-slate-700 hover:border-slate-400"
          >
            run queue →
          </Link>
          {runnerBlocked && (
            <span className="text-xs text-amber-700">
              Run is disabled until the selected execution loop is ready.
            </span>
          )}
        </div>
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

      {cases.data?.truncated && (
        <div className="text-xs px-3 py-2 rounded bg-amber-50 border border-amber-200 text-amber-900">
          ⚠ Loaded {cases.data.count} of {cases.data.total} cases — older
          cases are hidden by the server-side limit. Bulk actions only see
          the loaded subset.
        </div>
      )}

      {/* Search summary line — only when actually searching */}
      {searchQuery && (
        <div className="text-xs text-slate-500 -mt-2">
          {total} cases match{" "}
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

      <GenerationFeedbackPanel
        feedback={feedback.data?.data ?? []}
        summary={feedback.data?.summary ?? []}
        resolving={resolveFeedback.isPending}
        resolvingId={resolveFeedback.variables ?? null}
        onResolve={(id) => resolveFeedback.mutate(id)}
        error={feedback.error as Error | null}
      />

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
        const runnableApprovedCount = selectedRows.filter(
          (c) => c.review_status === "approved" && !activeRunCaseIds.has(c.case_id),
        ).length;
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
              runnableApprovedCount === 0 ||
              bulkRun.isPending ||
              bulk.isPending ||
              bulkDelete.isPending ||
              runnerBlocked
            }
            onClick={() => {
              const ids = selectedRows
                .filter((c) => c.review_status === "approved")
                .filter((c) => !activeRunCaseIds.has(c.case_id))
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
              runnerBlocked
                ? `executor ${llmStatus}: ${llmDetail || "not ready"}`
                : approvedCount === 0
                ? "no approved cases in selection"
                : runnableApprovedCount === 0
                ? "selected approved cases already have pending/running runs"
                : `${runnableApprovedCount} approved → run`
            }
          >
            ▶ Run {runnableApprovedCount > 0 ? `(${runnableApprovedCount})` : ""}
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
              {pagedVisible.map((c) => (
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
                  onCreateFeedback={(payload) =>
                    createFeedback.mutate({ case_id: c.case_id, ...payload })
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
                  feedbackBusy={
                    createFeedback.isPending && createFeedback.variables?.case_id === c.case_id
                  }
                  llmStatus={llmStatus}
                  llmDetail={llmDetail}
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

        {/* Pagination footer — purely a render-layer concern; selection,
            counts and search are applied by the backend before pagination. */}
        {visible.length > 0 && (
          <div className="flex flex-wrap items-center gap-3 px-3 py-2 border-t border-slate-100 text-xs text-slate-600">
            <span>
              {pageStart}–{pageEnd} of {total}
            </span>
            <label className="flex items-center gap-1">
              每页
              <select
                value={pageSize === "all" ? "all" : String(pageSize)}
                onChange={(e) => {
                  const v = e.target.value;
                  setPageSize(v === "all" ? "all" : Number(v));
                }}
                className="border border-slate-200 rounded px-1 py-0.5"
              >
                {[10, 20, 50, 100, 200, 500].map((n) => (
                  <option key={n} value={n}>{n}</option>
                ))}
                <option value="all">全部</option>
              </select>
            </label>
            {pageSize !== "all" && totalPages > 1 && (
              <div className="flex items-center gap-1 ml-auto">
                <button
                  disabled={page <= 1}
                  onClick={() => setPage(1)}
                  className="px-2 py-0.5 border border-slate-200 rounded hover:bg-slate-50 disabled:opacity-40"
                  title="first"
                >
                  «
                </button>
                <button
                  disabled={page <= 1}
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                  className="px-2 py-0.5 border border-slate-200 rounded hover:bg-slate-50 disabled:opacity-40"
                >
                  ‹ prev
                </button>
                <span className="px-2">
                  page {page} / {totalPages}
                </span>
                <button
                  disabled={page >= totalPages}
                  onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                  className="px-2 py-0.5 border border-slate-200 rounded hover:bg-slate-50 disabled:opacity-40"
                >
                  next ›
                </button>
                <button
                  disabled={page >= totalPages}
                  onClick={() => setPage(totalPages)}
                  className="px-2 py-0.5 border border-slate-200 rounded hover:bg-slate-50 disabled:opacity-40"
                  title="last"
                >
                  »
                </button>
              </div>
            )}
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

function GenerationFeedbackPanel({
  feedback,
  summary,
  resolving,
  resolvingId,
  onResolve,
  error,
}: {
  feedback: CaseFeedbackRow[];
  summary: CaseFeedbackResponse["summary"];
  resolving: boolean;
  resolvingId: string | null;
  onResolve: (id: string) => void;
  error: Error | null;
}) {
  const openSummary = summary.filter((s) => s.status === "open" && s.count > 0);
  if (!feedback.length && !error) return null;
  return (
    <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm">
      <div className="flex items-center justify-between gap-3">
        <div>
          <div className="text-xs uppercase tracking-wide text-amber-700">
            Generation feedback
          </div>
          <div className="text-xs text-amber-900">
            bad case → category → prompt/parser/filter fix → resolve
          </div>
        </div>
        <div className="flex flex-wrap gap-1 text-xs">
          {openSummary.map((s) => (
            <span key={s.category} className="rounded bg-white px-1.5 py-0.5 font-mono">
              {s.category}: {s.count}
            </span>
          ))}
        </div>
      </div>
      {error && <div className="mt-2 text-xs text-red-600">{error.message}</div>}
      {feedback.length > 0 && (
        <ul className="mt-2 max-h-40 overflow-auto divide-y divide-amber-100">
          {feedback.map((f) => (
            <li key={f.feedback_id} className="py-1.5 text-xs">
              <div className="flex items-start gap-2">
                <Link
                  to="/cases"
                  search={{ case_id: f.case_id } as never}
                  className="font-mono text-blue-700 hover:underline"
                >
                  {f.case_id}
                </Link>
                <span className="rounded bg-white px-1.5 py-0.5 font-mono text-amber-900">
                  {f.category}
                </span>
                <span className="text-slate-700">{f.note || f.evidence || "no note"}</span>
                <button
                  className="ml-auto rounded border border-amber-200 bg-white px-2 py-0.5 text-amber-800 hover:bg-amber-100 disabled:opacity-50"
                  disabled={resolving && resolvingId === f.feedback_id}
                  onClick={() => onResolve(f.feedback_id)}
                >
                  resolve
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function GenerationFeedbackForm({
  busy,
  onSubmit,
}: {
  busy: boolean;
  onSubmit: (payload: { category: FeedbackCategory; note: string; evidence: string }) => void;
}) {
  const [open, setOpen] = useState(false);
  const [category, setCategory] = useState<FeedbackCategory>("hallucinated_requirement");
  const [note, setNote] = useState("");
  const [evidence, setEvidence] = useState("");

  const submit = () => {
    onSubmit({ category, note, evidence });
    setNote("");
    setEvidence("");
    setOpen(false);
  };

  if (!open) {
    return (
      <button
        className="mt-3 rounded border border-amber-200 bg-white px-2 py-1 text-xs text-amber-800 hover:bg-amber-50"
        onClick={() => setOpen(true)}
      >
        Mark generation issue
      </button>
    );
  }

  return (
    <div className="mt-3 rounded border border-amber-200 bg-white p-3 text-xs">
      <div className="grid grid-cols-3 gap-2">
        <label>
          <span className="block text-slate-500 mb-1">category</span>
          <select
            className="w-full rounded border border-slate-200 px-2 py-1"
            value={category}
            onChange={(e) => setCategory(e.target.value as FeedbackCategory)}
          >
            {FEEDBACK_CATEGORIES.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </label>
        <label className="col-span-2">
          <span className="block text-slate-500 mb-1">PRD evidence / missing basis</span>
          <input
            className="w-full rounded border border-slate-200 px-2 py-1"
            value={evidence}
            onChange={(e) => setEvidence(e.target.value)}
            placeholder="paste PRD quote, or say PRD has no basis"
          />
        </label>
        <label className="col-span-3">
          <span className="block text-slate-500 mb-1">note</span>
          <textarea
            className="w-full rounded border border-slate-200 p-2"
            rows={2}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="why this case is bad, and what rule should change"
          />
        </label>
      </div>
      <div className="mt-2 flex gap-2">
        <button
          disabled={busy}
          onClick={submit}
          className="rounded bg-amber-700 px-2 py-1 text-white hover:bg-amber-800 disabled:opacity-50"
        >
          {busy ? "saving…" : "Save feedback"}
        </button>
        <button onClick={() => setOpen(false)} className="px-2 py-1 text-slate-500">
          cancel
        </button>
      </div>
    </div>
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
  onCreateFeedback,
  onDelete,
  busy,
  runBusy,
  editBusy,
  deleteBusy,
  feedbackBusy,
  llmStatus,
  llmDetail,
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
  onCreateFeedback: (payload: {
    category: FeedbackCategory;
    note: string;
    evidence: string;
  }) => void;
  onDelete: () => void;
  busy: boolean;
  runBusy: boolean;
  editBusy: boolean;
  deleteBusy: boolean;
  feedbackBusy: boolean;
  llmStatus: "ready" | "starting" | "down" | "unknown";
  llmDetail: string;
}) {
  const runnerBlocked = isLLMRunnerBlocked(llmStatus);
  const caseHasActiveRun = Boolean(lastRun && ACTIVE_RUN_STATUSES.has(lastRun.status));
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
          <QualityBadges quality={c.quality} />
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
                disabled={runBusy || runnerBlocked || caseHasActiveRun}
                onClick={onRun}
                title={
                  runnerBlocked
                    ? `executor ${llmStatus}: ${llmDetail || "not ready"}`
                    : caseHasActiveRun
                      ? `case already has an active ${lastRun?.status} run`
                    : "run this approved case"
                }
              >
                {runBusy ? "starting…" : caseHasActiveRun ? "running" : "▶ Run"}
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
                    <li key={i}>
                      {a.description}
                      {(a.source || a.confidence != null) && (
                        <div className="text-[11px] text-slate-500">
                          {a.source || "source?"}
                          {a.confidence != null && (
                            <> · confidence {Math.round(a.confidence * 100)}%</>
                          )}
                          {a.evidence && <> · evidence: “{a.evidence}”</>}
                          {a.rationale && <> · {a.rationale}</>}
                        </div>
                      )}
                    </li>
                  ))}
                </ul>
              </Block>
            </div>
            {c.quality && (
              <div className="mt-3 rounded border border-amber-200 bg-amber-50 p-2 text-xs text-amber-900">
                <div className="font-medium">
                  case quality · score {Math.round((c.quality.score ?? 0) * 100)} ·{" "}
                  {c.quality.severity || "unknown"}
                </div>
                {(c.quality.flags ?? []).length > 0 && (
                  <div className="mt-1 flex flex-wrap gap-1">
                    {(c.quality.flags ?? []).map((f) => (
                      <span key={f} className="rounded bg-white px-1.5 py-0.5 font-mono">
                        {f}
                      </span>
                    ))}
                  </div>
                )}
                {(c.quality.reviewer_notes ?? []).length > 0 && (
                  <ul className="mt-1 list-disc pl-4">
                    {(c.quality.reviewer_notes ?? []).map((n, i) => (
                      <li key={i}>{n}</li>
                    ))}
                  </ul>
                )}
              </div>
            )}
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
            <GenerationFeedbackForm
              busy={feedbackBusy}
              onSubmit={onCreateFeedback}
            />
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

function QualityBadges({ quality }: { quality?: CaseRow["quality"] }) {
  if (!quality) return null;
  const flags = quality.flags ?? [];
  const severity = quality.severity ?? "low";
  const score = quality.score;
  const colors: Record<string, string> = {
    low: "bg-emerald-50 text-emerald-700",
    medium: "bg-amber-50 text-amber-700",
    high: "bg-red-50 text-red-700",
  };
  return (
    <div className="mt-1 flex flex-wrap items-center gap-1">
      <span className={"text-[10px] rounded px-1.5 py-0.5 font-mono " + (colors[severity] || colors.low)}>
        quality {score != null ? Math.round(score * 100) : "?"} · {severity}
      </span>
      {flags.slice(0, 2).map((f) => (
        <span
          key={f}
          className="text-[10px] rounded bg-slate-100 px-1.5 py-0.5 font-mono text-slate-600"
          title={f}
        >
          {f}
        </span>
      ))}
      {flags.length > 2 && (
        <span className="text-[10px] text-slate-400">+{flags.length - 2}</span>
      )}
    </div>
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

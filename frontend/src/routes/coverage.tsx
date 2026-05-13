import { createFileRoute, Link } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ProjectTargetBadge } from "../components/ProjectTargetBadge";
import { apiFetch } from "../lib/adminAuth";
import { useCurrentProject } from "../lib/useCurrentProject";

export const Route = createFileRoute("/coverage")({
  component: CoveragePage,
});

type CoverageStatus = "" | "proposed" | "accepted" | "rejected" | "stale";
type OutputLanguage = "auto" | "zh" | "en";
const PRD_ID_QUERY_KEY = "prd_id";
const STATUS_QUERY_KEY = "status";

interface CoverageRow {
  coverage_id: string;
  project_id: string;
  prd_id: string;
  requirement_id: string;
  chapter_index: number;
  risk_type: string;
  coverage_type: string;
  title: string;
  scenario: string;
  rationale: string;
  priority: string;
  review_status: "proposed" | "accepted" | "rejected" | "stale";
  review_note?: string;
  linked_case_id: string | null;
  deleted_at?: string | null;
  source_prd_deleted_at?: string | null;
}

interface CoverageEditDraft {
  title: string;
  scenario: string;
  rationale: string;
  risk_type: string;
  coverage_type: string;
  priority: string;
}

interface PRDListItem {
  prd_id: string;
  name: string;
  version: number;
}

interface PRDDetail {
  data: {
    prd_id: string;
    name: string;
    chapters: Array<{ title: string; body: string }>;
  };
}

function hasCjk(text: string): boolean {
  return /[\u4e00-\u9fff]/.test(text);
}

function languageSummary(rows: CoverageRow[]): string {
  if (rows.length === 0) return "No coverage";
  const zh = rows.filter((row) => hasCjk(`${row.title}\n${row.scenario}`)).length;
  if (zh === rows.length) return "中文";
  if (zh === 0) return "English";
  return `Mixed (${zh}/${rows.length} 中文)`;
}

function outputLanguageLabel(language: OutputLanguage): string {
  if (language === "zh") return "中文";
  if (language === "en") return "English";
  return "跟随 PRD";
}

function readCoverageFiltersFromUrl(): { prdId: string; status: CoverageStatus } {
  if (typeof window === "undefined") return { prdId: "", status: "" };
  const params = new URLSearchParams(window.location.search);
  const rawStatus = params.get(STATUS_QUERY_KEY);
  const status: CoverageStatus =
    rawStatus === "proposed" ||
    rawStatus === "accepted" ||
    rawStatus === "rejected" ||
    rawStatus === "stale"
      ? rawStatus
      : "";
  return {
    prdId: params.get(PRD_ID_QUERY_KEY) ?? "",
    status,
  };
}

function writeCoverageFiltersToUrl(filters: { prdId: string; status: CoverageStatus }) {
  if (typeof window === "undefined") return;
  const url = new URL(window.location.href);
  if (filters.prdId) url.searchParams.set(PRD_ID_QUERY_KEY, filters.prdId);
  else url.searchParams.delete(PRD_ID_QUERY_KEY);
  if (filters.status) url.searchParams.set(STATUS_QUERY_KEY, filters.status);
  else url.searchParams.delete(STATUS_QUERY_KEY);
  window.history.replaceState({}, "", url.toString());
}

function CoveragePage() {
  const qc = useQueryClient();
  const { projectId } = useCurrentProject();
  const initialFilters = readCoverageFiltersFromUrl();
  const [status, setStatus] = useState<CoverageStatus>(initialFilters.status);
  const [prdId, setPrdId] = useState(initialFilters.prdId);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [reviewNotes, setReviewNotes] = useState<Record<string, string>>({});
  const [editing, setEditing] = useState<Record<string, CoverageEditDraft>>({});

  const setPrdFilter = (nextPrdId: string) => {
    setPrdId(nextPrdId);
    setSelected(new Set());
    writeCoverageFiltersToUrl({ prdId: nextPrdId, status });
  };

  const setStatusFilter = (nextStatus: CoverageStatus) => {
    setStatus(nextStatus);
    setSelected(new Set());
    writeCoverageFiltersToUrl({ prdId, status: nextStatus });
  };

  const prds = useQuery({
    queryKey: ["coverage-prds", projectId],
    enabled: Boolean(projectId),
    queryFn: async (): Promise<{ data: PRDListItem[] }> => {
      const r = await apiFetch(`/api/prd/?project_id=${encodeURIComponent(projectId)}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    },
  });

  const activePrd = useQuery({
    queryKey: ["coverage-prd-detail", prdId],
    enabled: Boolean(prdId),
    queryFn: async (): Promise<PRDDetail> => {
      const r = await apiFetch(`/api/prd/${prdId}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    },
  });

  const coverage = useQuery({
    queryKey: ["coverage-workbench", projectId, prdId, status],
    enabled: Boolean(projectId),
    queryFn: async (): Promise<{ data: CoverageRow[]; count: number }> => {
      const params = new URLSearchParams({ project_id: projectId, limit: "5000" });
      if (prdId) params.set("prd_id", prdId);
      if (status) params.set("status", status);
      const r = await apiFetch(`/api/coverage/?${params.toString()}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    },
  });

  const reviewCoverage = useMutation({
    mutationFn: async ({
      coverageId,
      action,
      note,
    }: {
      coverageId: string;
      action: "accept" | "reject" | "reset";
      note?: string;
    }) => {
      const r = await apiFetch(`/api/coverage/${coverageId}/review`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, note: note ?? "" }),
      });
      if (!r.ok) throw new Error(await r.text());
      return r.json();
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["coverage-workbench"] });
    },
  });

  const updateCoverage = useMutation({
    mutationFn: async ({
      coverageId,
      draft,
    }: {
      coverageId: string;
      draft: CoverageEditDraft;
    }) => {
      const r = await apiFetch(`/api/coverage/${coverageId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(draft),
      });
      if (!r.ok) throw new Error(await r.text());
      return r.json();
    },
    onSuccess: (_data, variables) => {
      setEditing((prev) => {
        const next = { ...prev };
        delete next[variables.coverageId];
        return next;
      });
      qc.invalidateQueries({ queryKey: ["coverage-workbench"] });
    },
  });

  const draftCoverageCase = useMutation({
    mutationFn: async (coverageId: string) => {
      const r = await apiFetch(`/api/coverage/${coverageId}/draft-case`, { method: "POST" });
      if (!r.ok) throw new Error(await r.text());
      return r.json();
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["coverage-workbench"] });
      qc.invalidateQueries({ queryKey: ["cases"] });
      qc.invalidateQueries({ queryKey: ["cases-summary"] });
      qc.invalidateQueries({ queryKey: ["cases-for-prd-overlay"] });
    },
  });

  const deleteCoverage = useMutation({
    mutationFn: async (coverageId: string) => {
      const r = await apiFetch(`/api/coverage/${coverageId}`, { method: "DELETE" });
      if (!r.ok && r.status !== 204) throw new Error(await r.text());
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["coverage-workbench"] });
    },
  });

  const bulkDeleteCoverage = useMutation({
    mutationFn: async (coverageIds: string[]) => {
      const r = await apiFetch("/api/coverage/bulk-delete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ coverage_ids: coverageIds }),
      });
      if (!r.ok) throw new Error(await r.text());
      return r.json() as Promise<{
        data: {
          deleted: string[];
          skipped_linked: string[];
          skipped_already_deleted: string[];
          missing: string[];
        };
      }>;
    },
    onSuccess: () => {
      setSelected(new Set());
      qc.invalidateQueries({ queryKey: ["coverage-workbench"] });
    },
  });

  const regenerateCoverage = useMutation({
    mutationFn: async (outputLanguage: OutputLanguage) => {
      if (!prdId) throw new Error("select one PRD first");
      const chapters = activePrd.data?.data.chapters ?? [];
      if (chapters.length === 0) throw new Error("PRD has no chapters to analyze");
      const r = await apiFetch(`/api/prd/${prdId}/analyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          chapter_indices: chapters.map((_, index) => index),
          output_language: outputLanguage,
          replace_unreviewed: true,
        }),
      });
      if (!r.ok) throw new Error(await r.text());
      return r.json();
    },
    onSuccess: () => {
      setSelected(new Set());
      qc.invalidateQueries({ queryKey: ["coverage-workbench"] });
    },
  });

  const rows = coverage.data?.data ?? [];
  const currentLanguage = languageSummary(rows);
  const deletableRows = rows.filter((row) => !row.linked_case_id);
  const selectedRows = rows.filter((row) => selected.has(row.coverage_id));
  const selectedDeletableRows = selectedRows.filter((row) => !row.linked_case_id);
  const allDeletableSelected =
    deletableRows.length > 0 && deletableRows.every((row) => selected.has(row.coverage_id));
  const toggleCoverageSelection = (coverageId: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(coverageId)) next.delete(coverageId);
      else next.add(coverageId);
      return next;
    });
  };
  const toggleAllDeletable = () => {
    if (allDeletableSelected) setSelected(new Set());
    else setSelected(new Set(deletableRows.map((row) => row.coverage_id)));
  };
  const beginEdit = (row: CoverageRow) => {
    setEditing((prev) => ({
      ...prev,
      [row.coverage_id]: {
        title: row.title,
        scenario: row.scenario,
        rationale: row.rationale,
        risk_type: row.risk_type,
        coverage_type: row.coverage_type,
        priority: row.priority,
      },
    }));
  };
  const updateEditDraft = (
    coverageId: string,
    field: keyof CoverageEditDraft,
    value: string,
  ) => {
    setEditing((prev) => ({
      ...prev,
      [coverageId]: {
        ...prev[coverageId],
        [field]: value,
      },
    }));
  };
  const cancelEdit = (coverageId: string) => {
    setEditing((prev) => {
      const next = { ...prev };
      delete next[coverageId];
      return next;
    });
  };
  const submitReview = (row: CoverageRow, action: "accept" | "reject" | "reset") => {
    reviewCoverage.mutate({
      coverageId: row.coverage_id,
      action,
      note: reviewNotes[row.coverage_id] ?? row.review_note ?? "",
    });
  };

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-semibold">
          Coverage
          {projectId && (
            <span className="text-slate-400 text-base font-normal"> / {projectId}</span>
          )}
        </h1>
        {projectId && (
          <div className="mt-1">
            <ProjectTargetBadge projectId={projectId} />
          </div>
        )}
        <p className="mt-1 text-sm text-slate-500">
          Review coverage items before drafting executable cases.
        </p>
      </div>

      {!projectId ? (
        <div className="rounded-lg border border-slate-200 bg-white p-8 text-center text-sm text-slate-500">
          Pick a project from the header dropdown to review coverage.
        </div>
      ) : (
        <div className="rounded-lg border border-slate-200 bg-white">
          <div className="flex flex-wrap items-center gap-3 border-b border-slate-100 p-3 text-sm">
            <label className="text-xs font-medium text-slate-500">
              PRD
              <select
                value={prdId}
                onChange={(event) => setPrdFilter(event.target.value)}
                className="ml-2 rounded border border-slate-200 bg-white px-2 py-1 text-xs text-slate-700"
              >
                <option value="">All PRDs</option>
                {(prds.data?.data ?? []).map((prd) => (
                  <option key={prd.prd_id} value={prd.prd_id}>
                    {prd.name} v{prd.version}
                  </option>
                ))}
              </select>
            </label>
            <label className="text-xs font-medium text-slate-500">
              Status
              <select
                value={status}
                onChange={(event) => setStatusFilter(event.target.value as CoverageStatus)}
                className="ml-2 rounded border border-slate-200 bg-white px-2 py-1 text-xs text-slate-700"
              >
                <option value="">All</option>
                <option value="proposed">Proposed</option>
                <option value="accepted">Accepted</option>
                <option value="rejected">Rejected</option>
                <option value="stale">Stale</option>
              </select>
            </label>
            <span className="ml-auto text-xs text-slate-500">
              {coverage.data?.count ?? 0} items
            </span>
          </div>
          <div className="flex flex-wrap items-center gap-2 border-b border-slate-100 bg-slate-50 px-3 py-2 text-xs text-slate-600">
            <span>
              Coverage language: <span className="font-medium text-slate-800">{currentLanguage}</span>
            </span>
            <span className="text-slate-300">·</span>
            <span>Regenerate unreviewed coverage as</span>
            {(["zh", "en", "auto"] as OutputLanguage[]).map((language) => (
              <button
                key={language}
                className="rounded border border-slate-200 bg-white px-2 py-1 text-xs text-slate-700 hover:border-slate-300 disabled:cursor-not-allowed disabled:opacity-50"
                disabled={
                  !prdId ||
                  activePrd.isLoading ||
                  coverage.isLoading ||
                  regenerateCoverage.isPending
                }
                onClick={() => {
                  const target = outputLanguageLabel(language);
                  const reviewed = rows.filter(
                    (row) => row.review_status === "accepted" || row.linked_case_id,
                  ).length;
                  if (
                    window.confirm(
                      `Regenerate coverage for this PRD as ${target}?\n\nUnreviewed, unlinked coverage will be replaced. Accepted or drafted coverage (${reviewed}) will be preserved.`,
                    )
                  ) {
                    regenerateCoverage.mutate(language);
                  }
                }}
              >
                {outputLanguageLabel(language)}
              </button>
            ))}
            {!prdId && <span className="text-slate-400">select one PRD to regenerate</span>}
          </div>

          {coverage.isLoading ? (
            <div className="p-4 text-sm text-slate-400">Loading coverage...</div>
          ) : rows.length ? (
            <div className="overflow-x-auto">
              {selected.size > 0 && (
                <div className="flex flex-wrap items-center gap-3 border-b border-slate-100 bg-slate-900 px-3 py-2 text-sm text-white">
                  <span>{selected.size} selected</span>
                  <span className="text-xs text-slate-400">·</span>
                  <span className="text-xs">
                    <span className="text-emerald-300">{selectedDeletableRows.length}</span>{" "}
                    can be deleted
                  </span>
                  {selectedRows.length > selectedDeletableRows.length && (
                    <span className="text-xs text-amber-300">
                      · {selectedRows.length - selectedDeletableRows.length} linked skipped
                    </span>
                  )}
                  <button
                    className="rounded bg-red-600 px-3 py-0.5 text-xs hover:bg-red-500 disabled:opacity-50"
                    disabled={selectedDeletableRows.length === 0 || bulkDeleteCoverage.isPending}
                    onClick={() => {
                      if (
                        window.confirm(
                          `Delete ${selectedDeletableRows.length} coverage item${selectedDeletableRows.length > 1 ? "s" : ""}?\n\nOnly coverage items that have not generated cases will be hidden from active review. Linked coverage items are skipped. Deleted coverage can be restored later.`,
                        )
                      ) {
                        bulkDeleteCoverage.mutate(
                          selectedDeletableRows.map((row) => row.coverage_id),
                        );
                      }
                    }}
                  >
                    delete selected
                  </button>
                  <button
                    className="px-2 py-0.5 text-xs text-slate-300 hover:text-white"
                    onClick={() => setSelected(new Set())}
                  >
                    clear
                  </button>
                </div>
              )}
              <table className="w-full text-sm">
                <thead className="text-left text-xs uppercase tracking-wide text-slate-400">
                  <tr>
                    <th className="w-10 p-3">
                      <input
                        type="checkbox"
                        checked={allDeletableSelected}
                        disabled={deletableRows.length === 0}
                        onChange={toggleAllDeletable}
                        title="select all deletable coverage items"
                      />
                    </th>
                    <th className="p-3">Coverage</th>
                    <th className="w-28 p-3">Risk</th>
                    <th className="w-28 p-3">Status</th>
                    <th className="w-64 p-3">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.coverage_id} className="border-t border-slate-100">
                      <td className="p-3 align-top">
                        <input
                          type="checkbox"
                          checked={selected.has(row.coverage_id)}
                          disabled={Boolean(row.linked_case_id)}
                          onChange={() => toggleCoverageSelection(row.coverage_id)}
                          title={
                            row.linked_case_id
                              ? "linked case exists; handle the case before deleting coverage"
                              : "select coverage item"
                          }
                        />
                      </td>
                      <td className="p-3 align-top">
                        {editing[row.coverage_id] ? (
                          <div className="max-w-4xl space-y-2">
                            <input
                              className="w-full rounded border border-slate-200 px-2 py-1 text-sm font-medium text-slate-800"
                              value={editing[row.coverage_id].title}
                              onChange={(event) =>
                                updateEditDraft(row.coverage_id, "title", event.target.value)
                              }
                              aria-label="coverage title"
                            />
                            <textarea
                              className="min-h-20 w-full rounded border border-slate-200 px-2 py-1 text-xs leading-5 text-slate-700"
                              value={editing[row.coverage_id].scenario}
                              onChange={(event) =>
                                updateEditDraft(row.coverage_id, "scenario", event.target.value)
                              }
                              aria-label="coverage scenario"
                            />
                            <textarea
                              className="min-h-16 w-full rounded border border-slate-200 px-2 py-1 text-xs leading-5 text-slate-700"
                              value={editing[row.coverage_id].rationale}
                              onChange={(event) =>
                                updateEditDraft(row.coverage_id, "rationale", event.target.value)
                              }
                              placeholder="Rationale"
                              aria-label="coverage rationale"
                            />
                            <div className="grid gap-2 text-xs sm:grid-cols-3">
                              <input
                                className="rounded border border-slate-200 px-2 py-1"
                                value={editing[row.coverage_id].risk_type}
                                onChange={(event) =>
                                  updateEditDraft(row.coverage_id, "risk_type", event.target.value)
                                }
                                aria-label="risk type"
                              />
                              <input
                                className="rounded border border-slate-200 px-2 py-1"
                                value={editing[row.coverage_id].coverage_type}
                                onChange={(event) =>
                                  updateEditDraft(
                                    row.coverage_id,
                                    "coverage_type",
                                    event.target.value,
                                  )
                                }
                                aria-label="coverage type"
                              />
                              <input
                                className="rounded border border-slate-200 px-2 py-1"
                                value={editing[row.coverage_id].priority}
                                onChange={(event) =>
                                  updateEditDraft(row.coverage_id, "priority", event.target.value)
                                }
                                aria-label="priority"
                              />
                            </div>
                          </div>
                        ) : (
                          <>
                            <div className="font-medium text-slate-800">{row.title}</div>
                            <div className="mt-1 max-w-4xl text-xs leading-5 text-slate-500">
                              {row.scenario}
                            </div>
                            {row.rationale && (
                              <div className="mt-1 max-w-4xl text-[11px] leading-5 text-slate-400">
                                {row.rationale}
                              </div>
                            )}
                            {row.review_note && (
                              <div className="mt-2 max-w-4xl rounded border border-amber-100 bg-amber-50 px-2 py-1 text-[11px] leading-5 text-amber-800">
                                Review note: {row.review_note}
                              </div>
                            )}
                          </>
                        )}
                        <div className="mt-1 flex flex-wrap gap-2 text-[11px] text-slate-400">
                          <span>chapter {row.chapter_index}</span>
                          <span>{row.priority}</span>
                          {row.source_prd_deleted_at && (
                            <span
                              className="rounded bg-amber-50 px-1.5 py-0.5 text-amber-700"
                              title="source PRD has been deleted"
                            >
                              PRD deleted
                            </span>
                          )}
                          {row.linked_case_id && (
                            <Link
                              to="/cases"
                              search={{ case_id: row.linked_case_id }}
                              className="text-blue-700 hover:underline"
                            >
                              {row.linked_case_id}
                            </Link>
                          )}
                        </div>
                      </td>
                      <td className="p-3 align-top text-xs text-slate-600">
                        {editing[row.coverage_id] ? (
                          <>
                            {editing[row.coverage_id].risk_type}
                            <div className="text-slate-400">
                              {editing[row.coverage_id].coverage_type}
                            </div>
                          </>
                        ) : (
                          <>
                            {row.risk_type}
                            <div className="text-slate-400">{row.coverage_type}</div>
                          </>
                        )}
                      </td>
                      <td className="p-3 align-top">
                        <span className="rounded bg-slate-100 px-2 py-0.5 text-xs text-slate-700">
                          {row.review_status}
                        </span>
                      </td>
                      <td className="p-3 align-top">
                        <div className="mb-2 flex flex-wrap gap-1">
                          {editing[row.coverage_id] ? (
                            <>
                              <button
                                className="rounded bg-slate-900 px-2 py-1 text-xs text-white disabled:opacity-50"
                                disabled={updateCoverage.isPending}
                                onClick={() =>
                                  updateCoverage.mutate({
                                    coverageId: row.coverage_id,
                                    draft: editing[row.coverage_id],
                                  })
                                }
                              >
                                Save
                              </button>
                              <button
                                className="rounded border border-slate-200 px-2 py-1 text-xs text-slate-600 disabled:opacity-50"
                                disabled={updateCoverage.isPending}
                                onClick={() => cancelEdit(row.coverage_id)}
                              >
                                Cancel
                              </button>
                            </>
                          ) : (
                            <button
                              className="rounded border border-slate-200 px-2 py-1 text-xs text-slate-600 disabled:cursor-not-allowed disabled:opacity-50"
                              disabled={Boolean(row.linked_case_id) || updateCoverage.isPending}
                              onClick={() => beginEdit(row)}
                              title={
                                row.linked_case_id
                                  ? "linked case exists; handle the case before editing coverage"
                                  : "edit this coverage item"
                              }
                            >
                              Edit
                            </button>
                          )}
                          <button
                            className="rounded border border-emerald-200 px-2 py-1 text-xs text-emerald-700 disabled:opacity-50"
                            disabled={
                              row.review_status === "accepted" || reviewCoverage.isPending
                            }
                            onClick={() => submitReview(row, "accept")}
                          >
                            Accept
                          </button>
                          <button
                            className="rounded border border-red-200 px-2 py-1 text-xs text-red-700 disabled:opacity-50"
                            disabled={
                              row.review_status === "rejected" || reviewCoverage.isPending
                            }
                            onClick={() => submitReview(row, "reject")}
                          >
                            Reject
                          </button>
                          <button
                            className="rounded border border-slate-200 px-2 py-1 text-xs text-slate-600 disabled:opacity-50"
                            disabled={
                              row.review_status === "proposed" || reviewCoverage.isPending
                            }
                            onClick={() => submitReview(row, "reset")}
                          >
                            Reset
                          </button>
                          <button
                            className="rounded bg-slate-900 px-2 py-1 text-xs text-white disabled:opacity-50"
                            disabled={
                              row.review_status !== "accepted" ||
                              Boolean(row.linked_case_id) ||
                              draftCoverageCase.isPending
                            }
                            onClick={() => draftCoverageCase.mutate(row.coverage_id)}
                          >
                            {row.linked_case_id ? "Drafted" : "Draft case"}
                          </button>
                          <button
                            className="rounded border border-red-200 px-2 py-1 text-xs text-red-700 disabled:cursor-not-allowed disabled:opacity-50"
                            disabled={Boolean(row.linked_case_id) || deleteCoverage.isPending}
                            onClick={() => {
                              if (
                                window.confirm(
                                  `Delete coverage item "${row.title}"?\n\nThis coverage has not generated a case. It will be hidden from active review and can be restored from deleted items.`,
                                )
                              ) {
                                deleteCoverage.mutate(row.coverage_id);
                              }
                            }}
                            title={
                              row.linked_case_id
                                ? "linked case exists; handle the case before deleting coverage"
                                : "delete this coverage item"
                            }
                          >
                            delete
                          </button>
                        </div>
                        <textarea
                          className="min-h-14 w-56 rounded border border-slate-200 px-2 py-1 text-xs text-slate-700"
                          value={reviewNotes[row.coverage_id] ?? row.review_note ?? ""}
                          onChange={(event) =>
                            setReviewNotes((prev) => ({
                              ...prev,
                              [row.coverage_id]: event.target.value,
                            }))
                          }
                          placeholder="Review note"
                          aria-label="review note"
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="p-8 text-center text-sm text-slate-500">
              No coverage items match the current filters.
            </div>
          )}

          {(coverage.error ||
            reviewCoverage.error ||
            draftCoverageCase.error ||
            deleteCoverage.error ||
            bulkDeleteCoverage.error ||
            regenerateCoverage.error) && (
            <pre className="border-t border-red-100 bg-red-50 p-3 text-xs text-red-600 whitespace-pre-wrap">
              {(
                (coverage.error ||
                  reviewCoverage.error ||
                  draftCoverageCase.error ||
                  deleteCoverage.error ||
                  bulkDeleteCoverage.error ||
                  regenerateCoverage.error) as Error
              ).message}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}

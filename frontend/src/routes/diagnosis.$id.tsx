import { createFileRoute, Link } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { apiFetch } from "../lib/adminAuth";

export const Route = createFileRoute("/diagnosis/$id")({
  component: DiagnosisDetailPage,
});

interface DiagnosisRow {
  diag_id: string;
  run_id: string;
  case_id: string;
  diagnoser_prompt_version: string;
  diagnoser_model: string;
  category: string;
  confidence: number;
  reasoning: string;
  fix_suggestion: string;
  evidence_pack?: {
    keywords?: string[];
    code_context?: {
      candidate_files?: Array<{
        repo: string;
        path: string;
        matches?: Array<{ keyword: string; line_number: number; line: string }>;
      }>;
    };
    external_context?: {
      jira?: Array<{ key: string; ok: boolean; text?: string; error?: string }>;
      ci?: Array<{ job_id: number; ok: boolean; text?: string; error?: string }>;
      jenkins?: Array<{ url: string; ok: boolean; text?: string; error?: string }>;
      confluence?: Array<{ page_id: string; ok: boolean; text?: string; error?: string }>;
    };
    server_logs?: {
      snippets?: Array<{ server: string; path: string; ok: boolean; text?: string; error?: string }>;
    };
  };
  candidate_files?: Array<{
    repo: string;
    path: string;
    matches?: Array<{ keyword: string; line_number: number; line: string }>;
  }>;
  human_feedback: string | null;
  feedback_note: string;
  feedback_target: string;
  feedback_at: string | null;
  created_at: string;
  publish_suggestions?: PublishSuggestion[];
}

type PublishSuggestion =
  | { type: "jira"; issue_key: string; label: string }
  | { type: "confluence"; page_id: string; label: string }
  | {
      type: "gitlab_discussion";
      project: string;
      mr_iid: number;
      discussion_id: string;
      label: string;
    };

interface PatternMatch {
  pattern_id: string;
  pattern_type: string;
  title: string;
  description: string;
  suggested_action: string;
  hit_count: number;
}

interface ByRunResponse {
  data: { diagnoses: DiagnosisRow[]; pattern_matches: PatternMatch[] };
}

interface DiagnosisJobResponse {
  data: {
    job_id: string;
    status: "pending" | "running" | "done" | "failed";
    diag_id: string;
    error: string;
    include_dev_context: boolean;
  };
}

const CATEGORY_COLORS: Record<string, string> = {
  real_bug: "bg-red-100 text-red-800",
  flaky: "bg-amber-100 text-amber-800",
  selector_drift: "bg-orange-100 text-orange-800",
  vision_misjudge: "bg-purple-100 text-purple-800",
  env_issue: "bg-blue-100 text-blue-800",
  data_issue: "bg-pink-100 text-pink-800",
  unknown: "bg-slate-200 text-slate-700",
};

function DiagnosisDetailPage() {
  /** id is interpreted as a run_id — easier link from the run page. */
  const { id } = Route.useParams();
  const qc = useQueryClient();
  const [note, setNote] = useState("");
  const [reason, setReason] = useState("");
  const [feedbackTarget, setFeedbackTarget] = useState("pattern");
  const [jobId, setJobId] = useState("");
  const [publishType, setPublishType] = useState("jira");
  const [publishTarget, setPublishTarget] = useState("");
  const [publishProject, setPublishProject] = useState("");
  const [publishMrIid, setPublishMrIid] = useState("");
  const [publishDiscussionId, setPublishDiscussionId] = useState("");

  const byRun = useQuery({
    queryKey: ["diagnosis-by-run", id],
    queryFn: async (): Promise<ByRunResponse> => {
      const r = await apiFetch(`/api/diagnosis/by-run/${id}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    },
    refetchInterval: (q) => {
      const diag = (q.state.data as ByRunResponse | undefined)?.data?.diagnoses?.[0];
      return diag ? false : 3000;
    },
  });

  const job = useQuery({
    queryKey: ["diagnosis-job", jobId],
    enabled: Boolean(jobId),
    queryFn: async (): Promise<DiagnosisJobResponse> => {
      const r = await apiFetch(`/api/diagnosis/jobs/${jobId}`);
      if (!r.ok) throw new Error(await r.text());
      return r.json();
    },
    refetchInterval: (q) => {
      const status = (q.state.data as DiagnosisJobResponse | undefined)?.data?.status;
      return status === "done" || status === "failed" ? false : 2000;
    },
  });

  useEffect(() => {
    if (job.data?.data.status === "done" && jobId) {
      setJobId("");
      qc.invalidateQueries({ queryKey: ["diagnosis-by-run", id] });
    }
  }, [id, job.data?.data.status, jobId, qc]);

  const generate = useMutation({
    mutationFn: async (args: { withDevContext?: boolean; overwrite?: boolean } = {}) => {
      const r = await apiFetch(`/api/diagnosis/by-run/${id}/jobs`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          overwrite_existing: Boolean(args.overwrite),
          include_dev_context: Boolean(args.withDevContext),
        }),
      });
      if (!r.ok) throw new Error(await r.text());
      return (await r.json()) as DiagnosisJobResponse;
    },
    onSuccess: (data) => setJobId(data.data.job_id),
  });

  const feedback = useMutation({
    mutationFn: async (args: { diag_id: string; feedback: string }) => {
      const r = await apiFetch(`/api/diagnosis/${args.diag_id}/feedback`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          feedback: args.feedback,
          feedback_target: args.feedback === "confirmed" ? feedbackTarget : "",
          reason,
          note,
        }),
      });
      if (!r.ok) throw new Error(await r.text());
      return r.json();
    },
    onSuccess: () => {
      setNote("");
      setReason("");
      setFeedbackTarget("pattern");
      qc.invalidateQueries({ queryKey: ["diagnosis-by-run", id] });
    },
  });
  const publish = useMutation({
    mutationFn: async (diagId: string) => {
      const target =
        publishType === "jira"
          ? { type: "jira", issue_key: publishTarget.trim() }
          : publishType === "confluence"
            ? { type: "confluence", page_id: publishTarget.trim() }
            : {
                type: "gitlab_discussion",
                project: publishProject.trim(),
                mr_iid: Number(publishMrIid),
                discussion_id: publishDiscussionId.trim(),
              };
      const r = await apiFetch(`/api/diagnosis/${diagId}/publish`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target }),
      });
      if (!r.ok) throw new Error(await r.text());
      return r.json();
    },
  });

  const diag = byRun.data?.data.diagnoses?.[0];
  const matches = byRun.data?.data.pattern_matches ?? [];

  useEffect(() => {
    const suggestion = diag?.publish_suggestions?.[0];
    if (!suggestion) return;
    if (publishTarget || publishProject || publishMrIid || publishDiscussionId) return;
    applyPublishSuggestion(suggestion);
  }, [diag?.diag_id]);

  function applyPublishSuggestion(suggestion: PublishSuggestion) {
    setPublishType(suggestion.type);
    if (suggestion.type === "jira") {
      setPublishTarget(suggestion.issue_key);
      setPublishProject("");
      setPublishMrIid("");
      setPublishDiscussionId("");
      return;
    }
    if (suggestion.type === "confluence") {
      setPublishTarget(suggestion.page_id);
      setPublishProject("");
      setPublishMrIid("");
      setPublishDiscussionId("");
      return;
    }
    setPublishTarget("");
    setPublishProject(suggestion.project);
    setPublishMrIid(String(suggestion.mr_iid));
    setPublishDiscussionId(suggestion.discussion_id);
  }

  return (
    <div className="space-y-6">
      <Link to="/runs/$id" params={{ id }} className="text-xs text-slate-500 hover:text-slate-900">
        ← run {id.slice(0, 8)}…
      </Link>

      <div>
        <div className="text-xs uppercase tracking-wide text-slate-400">AI diagnosis</div>
        <h1 className="text-2xl font-semibold">
          run <span className="font-mono text-base">{id.slice(0, 12)}…</span>
        </h1>
      </div>

      <div className="rounded border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
        AI diagnosis is advisory. Confirm manually before acting on issues involving data,
        permissions, payments, environment drift, account state, or missing screenshots.
      </div>

      {byRun.isLoading && <div className="text-slate-400 text-sm">loading…</div>}

      {/* No diagnosis yet */}
      {byRun.data && !diag && (
        <div className="bg-amber-50 border border-amber-200 rounded p-4 text-sm space-y-3">
          <div className="text-amber-900">
            No diagnosis yet for this run. Click below to ask the LLM to analyse the trace.
          </div>
          <button
            disabled={generate.isPending}
            onClick={() => generate.mutate({ withDevContext: false })}
            className="bg-slate-900 text-white text-sm px-3 py-1.5 rounded hover:bg-slate-700 disabled:opacity-50"
          >
            {generate.isPending || jobId ? "diagnosing…" : "Run diagnosis"}
          </button>
          <button
            disabled={generate.isPending}
            onClick={() => generate.mutate({ withDevContext: true })}
            className="ml-2 bg-indigo-700 text-white text-sm px-3 py-1.5 rounded hover:bg-indigo-800 disabled:opacity-50"
          >
            Analyze with workspace context
          </button>
          {jobId && (
            <div className="text-xs text-slate-600">
              job {jobId}: {job.data?.data.status ?? "pending"}
            </div>
          )}
          {job.data?.data.status === "failed" && (
            <pre className="text-xs text-red-600 whitespace-pre-wrap">
              {job.data.data.error}
            </pre>
          )}
          {generate.error && (
            <pre className="text-xs text-red-600 whitespace-pre-wrap">
              {(generate.error as Error).message}
            </pre>
          )}
        </div>
      )}

      {/* Diagnosis card */}
      {diag && (
        <div className="bg-white border border-slate-200 rounded-lg p-5 space-y-4">
          <div className="flex items-baseline justify-between">
            <div className="flex items-center gap-3">
              <span
                className={
                  "text-sm font-mono px-3 py-1 rounded " +
                  (CATEGORY_COLORS[diag.category] || CATEGORY_COLORS.unknown)
                }
              >
                {diag.category}
              </span>
              <span className="text-sm text-slate-500">
                confidence{" "}
                <code className="text-slate-900">{diag.confidence.toFixed(2)}</code>
              </span>
              <ConfidenceBar v={diag.confidence} />
            </div>
            <span className="text-xs text-slate-400 font-mono">
              {diag.diagnoser_prompt_version} · {diag.diagnoser_model}
            </span>
          </div>

          <Section title="reasoning">
            <p className="text-sm whitespace-pre-wrap leading-relaxed">{diag.reasoning || "—"}</p>
          </Section>

          <Section title="fix suggestion">
            <p className="text-sm bg-emerald-50 border border-emerald-200 rounded p-3">
              {diag.fix_suggestion || "(none)"}
            </p>
          </Section>

          {((diag.candidate_files?.length ?? 0) > 0 ||
            (diag.evidence_pack?.external_context?.jira?.length ?? 0) > 0 ||
            (diag.evidence_pack?.external_context?.ci?.length ?? 0) > 0 ||
            (diag.evidence_pack?.external_context?.jenkins?.length ?? 0) > 0 ||
            (diag.evidence_pack?.external_context?.confluence?.length ?? 0) > 0 ||
            (diag.evidence_pack?.server_logs?.snippets?.length ?? 0) > 0) && (
            <Section title="dev context evidence">
              {diag.candidate_files && diag.candidate_files.length > 0 && (
                <div className="space-y-2">
                  {diag.candidate_files.slice(0, 6).map((file) => (
                    <div key={`${file.repo}/${file.path}`} className="rounded border border-slate-200 p-2">
                      <div className="font-mono text-xs text-slate-800">
                        {file.repo}/{file.path}
                      </div>
                      {(file.matches ?? []).slice(0, 2).map((match) => (
                        <div key={`${file.path}:${match.line_number}:${match.keyword}`} className="mt-1 text-xs text-slate-500 font-mono">
                          {match.line_number}: {match.line}
                        </div>
                      ))}
                    </div>
                  ))}
                </div>
              )}
              {(diag.evidence_pack?.external_context?.jira ?? []).length > 0 && (
                <div className="mt-3 text-xs text-slate-600">
                  Jira: {(diag.evidence_pack?.external_context?.jira ?? []).map((j) => j.key).join(", ")}
                </div>
              )}
              {(diag.evidence_pack?.external_context?.ci ?? []).length > 0 && (
                <div className="mt-1 text-xs text-slate-600">
                  CI jobs: {(diag.evidence_pack?.external_context?.ci ?? []).map((j) => j.job_id).join(", ")}
                </div>
              )}
              {(diag.evidence_pack?.external_context?.jenkins ?? []).length > 0 && (
                <div className="mt-1 text-xs text-slate-600">
                  Jenkins builds:{" "}
                  {(diag.evidence_pack?.external_context?.jenkins ?? [])
                    .map((j) => j.url)
                    .join(", ")}
                </div>
              )}
              {(diag.evidence_pack?.external_context?.confluence ?? []).length > 0 && (
                <div className="mt-1 text-xs text-slate-600">
                  Confluence pages:{" "}
                  {(diag.evidence_pack?.external_context?.confluence ?? [])
                    .map((p) => p.page_id)
                    .join(", ")}
                </div>
              )}
              {(diag.evidence_pack?.server_logs?.snippets ?? []).length > 0 && (
                <div className="mt-3 space-y-2">
                  {(diag.evidence_pack?.server_logs?.snippets ?? []).slice(0, 3).map((snippet) => (
                    <pre key={`${snippet.server}:${snippet.path}`} className="rounded bg-slate-950 p-2 text-xs text-slate-100 overflow-auto">
                      {snippet.server} {snippet.path}
                      {"\n"}
                      {snippet.text || snippet.error || "(empty)"}
                    </pre>
                  ))}
                </div>
              )}
            </Section>
          )}

          {/* Human feedback */}
          <Section title="human feedback">
            {diag.human_feedback ? (
              <div className="text-sm">
                <span className="font-mono px-2 py-0.5 rounded bg-slate-200 text-slate-800 mr-2">
                  {diag.human_feedback}
                </span>
                <span className="text-slate-500">
                  {diag.feedback_at && new Date(diag.feedback_at).toLocaleString()}
                </span>
                {diag.feedback_target && (
                  <span className="ml-2 text-xs text-slate-500">
                    target: <code>{diag.feedback_target}</code>
                  </span>
                )}
                {diag.feedback_note && (
                  <p className="text-xs text-slate-600 mt-1 italic">{diag.feedback_note}</p>
                )}
              </div>
            ) : (
              <div className="space-y-2">
                <textarea
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  className="border border-slate-200 rounded p-2 w-full text-sm"
                  rows={2}
                  placeholder="optional note: why was the diagnosis right or wrong?"
                />
                <div className="flex flex-wrap items-center gap-2">
                  <select
                    value={reason}
                    onChange={(e) => setReason(e.target.value)}
                    className="border border-slate-200 rounded px-2 py-1 text-sm"
                  >
                    <option value="">reason</option>
                    <option value="category_wrong">category wrong</option>
                    <option value="evidence_insufficient">evidence insufficient</option>
                    <option value="fix_not_actionable">fix not actionable</option>
                    <option value="model_hallucinated">model hallucinated</option>
                    <option value="other">other</option>
                  </select>
                  <select
                    value={feedbackTarget}
                    onChange={(e) => setFeedbackTarget(e.target.value)}
                    className="border border-slate-200 rounded px-2 py-1 text-sm"
                  >
                    <option value="pattern">pattern</option>
                    <option value="asset">asset</option>
                    <option value="case">case</option>
                    <option value="coverage">coverage</option>
                  </select>
                  <div className="basis-full rounded border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-600">
                    Feedback target controls where a confirmed diagnosis is routed: pattern
                    learns a reusable failure signature, asset marks the regression asset for
                    repair, case sends the case back to review, and coverage marks the source
                    coverage stale.
                  </div>
                  <button
                    disabled={feedback.isPending}
                    onClick={() =>
                      feedback.mutate({ diag_id: diag.diag_id, feedback: "confirmed" })
                    }
                    className="bg-emerald-700 text-white text-sm px-3 py-1 rounded hover:bg-emerald-800 disabled:opacity-50"
                  >
                    ✓ confirmed (→ sediment)
                  </button>
                  <button
                    disabled={feedback.isPending}
                    onClick={() =>
                      feedback.mutate({ diag_id: diag.diag_id, feedback: "partially_correct" })
                    }
                    className="bg-amber-600 text-white text-sm px-3 py-1 rounded hover:bg-amber-700 disabled:opacity-50"
                  >
                    ◐ partially correct
                  </button>
                  <button
                    disabled={feedback.isPending}
                    onClick={() =>
                      feedback.mutate({ diag_id: diag.diag_id, feedback: "wrong" })
                    }
                    className="bg-red-700 text-white text-sm px-3 py-1 rounded hover:bg-red-800 disabled:opacity-50"
                  >
                    ✗ wrong
                  </button>
                  <span className="text-xs text-slate-400 ml-2">
                    confirmed feedback feeds the pattern library
                  </span>
                </div>
              </div>
            )}
          </Section>

          <Section title="publish back">
            <div className="flex flex-wrap items-center gap-2 text-sm">
              {(diag.publish_suggestions ?? []).length > 0 && (
                <select
                  value=""
                  onChange={(e) => {
                    const suggestion = (diag.publish_suggestions ?? [])[Number(e.target.value)];
                    if (suggestion) applyPublishSuggestion(suggestion);
                  }}
                  className="border border-slate-200 rounded px-2 py-1 text-sm"
                >
                  <option value="">suggested targets</option>
                  {(diag.publish_suggestions ?? []).map((suggestion, index) => (
                    <option key={`${suggestion.type}:${suggestion.label}`} value={index}>
                      {suggestion.label}
                    </option>
                  ))}
                </select>
              )}
              <select
                value={publishType}
                onChange={(e) => {
                  setPublishType(e.target.value);
                  setPublishTarget("");
                  setPublishProject("");
                  setPublishMrIid("");
                  setPublishDiscussionId("");
                }}
                className="border border-slate-200 rounded px-2 py-1 text-sm"
              >
                <option value="jira">Jira comment</option>
                <option value="confluence">Confluence comment</option>
                <option value="gitlab_discussion">GitLab discussion</option>
              </select>
              {publishType === "gitlab_discussion" ? (
                <>
                  <input
                    value={publishProject}
                    onChange={(e) => setPublishProject(e.target.value)}
                    placeholder="group/project"
                    className="min-w-44 border border-slate-200 rounded px-2 py-1 text-sm"
                  />
                  <input
                    value={publishMrIid}
                    onChange={(e) => setPublishMrIid(e.target.value)}
                    placeholder="MR IID"
                    className="w-24 border border-slate-200 rounded px-2 py-1 text-sm"
                  />
                  <input
                    value={publishDiscussionId}
                    onChange={(e) => setPublishDiscussionId(e.target.value)}
                    placeholder="discussion id"
                    className="min-w-44 border border-slate-200 rounded px-2 py-1 text-sm"
                  />
                </>
              ) : (
                <input
                  value={publishTarget}
                  onChange={(e) => setPublishTarget(e.target.value)}
                  placeholder={publishType === "jira" ? "ZSTAC-12345" : "Confluence pageId"}
                  className="min-w-56 border border-slate-200 rounded px-2 py-1 text-sm"
                />
              )}
              <button
                disabled={
                  publish.isPending ||
                  (publishType === "gitlab_discussion"
                    ? !publishProject.trim() ||
                      !publishMrIid.trim() ||
                      !publishDiscussionId.trim()
                    : !publishTarget.trim())
                }
                onClick={() => publish.mutate(diag.diag_id)}
                className="bg-slate-900 text-white text-sm px-3 py-1 rounded hover:bg-slate-700 disabled:opacity-50"
              >
                {publish.isPending ? "publishing…" : "publish"}
              </button>
              {publish.data && <span className="text-xs text-emerald-700">published</span>}
              {publish.error && (
                <span className="text-xs text-red-600">{(publish.error as Error).message}</span>
              )}
            </div>
          </Section>

          <div className="flex items-center justify-between pt-2 border-t border-slate-100">
            <span className="text-xs text-slate-400">
              created {new Date(diag.created_at).toLocaleString()}
            </span>
            <button
              disabled={generate.isPending || Boolean(jobId)}
              onClick={() => generate.mutate({ withDevContext: false, overwrite: true })}
              className="text-xs px-2 py-0.5 rounded border border-slate-200 hover:border-slate-400"
            >
              {generate.isPending ? "regenerating…" : "↻ regenerate"}
            </button>
            <button
              disabled={generate.isPending || Boolean(jobId)}
              onClick={() => generate.mutate({ withDevContext: true, overwrite: true })}
              className="ml-2 text-xs px-2 py-0.5 rounded border border-indigo-200 text-indigo-700 hover:border-indigo-400"
            >
              workspace context
            </button>
            {jobId && (
              <span className="ml-2 text-xs text-slate-500">
                job {jobId}: {job.data?.data.status ?? "pending"}
              </span>
            )}
          </div>
        </div>
      )}

      {/* Sediment matches */}
      {matches.length > 0 && (
        <div className="bg-white border border-slate-200 rounded-lg p-5">
          <div className="text-xs uppercase tracking-wide text-slate-400 mb-2">
            we've seen this before · {matches.length} pattern match
            {matches.length > 1 ? "es" : ""}
          </div>
          <ul className="space-y-3">
            {matches.map((p) => (
              <li
                key={p.pattern_id}
                className="border-l-2 border-slate-300 pl-3"
              >
                <div className="flex items-center gap-2">
                  <span
                    className={
                      "text-xs font-mono px-2 py-0.5 rounded " +
                      (CATEGORY_COLORS[p.pattern_type] || CATEGORY_COLORS.unknown)
                    }
                  >
                    {p.pattern_type}
                  </span>
                  <span className="text-sm font-medium">{p.title}</span>
                  <span className="text-xs text-slate-400 ml-auto">
                    hits: {p.hit_count}
                  </span>
                </div>
                {p.suggested_action && (
                  <p className="text-xs text-emerald-800 mt-1">
                    last fix: {p.suggested_action}
                  </p>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-xs uppercase tracking-wide text-slate-400 mb-1">{title}</div>
      {children}
    </div>
  );
}

function ConfidenceBar({ v }: { v: number }) {
  const pct = Math.round(Math.max(0, Math.min(1, v)) * 100);
  const color = v >= 0.7 ? "bg-emerald-500" : v >= 0.4 ? "bg-amber-500" : "bg-red-500";
  return (
    <span className="inline-block w-24 h-2 bg-slate-100 rounded overflow-hidden">
      <span className={`block h-full ${color}`} style={{ width: `${pct}%` }} />
    </span>
  );
}

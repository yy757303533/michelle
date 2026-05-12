import { createFileRoute, Link } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
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
  human_feedback: string | null;
  feedback_note: string;
  feedback_target: string;
  feedback_at: string | null;
  created_at: string;
}

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

  const generate = useMutation({
    mutationFn: async () => {
      const r = await apiFetch(`/api/diagnosis/by-run/${id}/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ overwrite_existing: true }),
      });
      if (!r.ok) throw new Error(await r.text());
      return r.json();
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["diagnosis-by-run", id] }),
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

  const diag = byRun.data?.data.diagnoses?.[0];
  const matches = byRun.data?.data.pattern_matches ?? [];

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
            onClick={() => generate.mutate()}
            className="bg-slate-900 text-white text-sm px-3 py-1.5 rounded hover:bg-slate-700 disabled:opacity-50"
          >
            {generate.isPending ? "diagnosing… (may take 30s)" : "Run diagnosis"}
          </button>
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

          <div className="flex items-center justify-between pt-2 border-t border-slate-100">
            <span className="text-xs text-slate-400">
              created {new Date(diag.created_at).toLocaleString()}
            </span>
            <button
              disabled={generate.isPending}
              onClick={() => generate.mutate()}
              className="text-xs px-2 py-0.5 rounded border border-slate-200 hover:border-slate-400"
            >
              {generate.isPending ? "regenerating…" : "↻ regenerate"}
            </button>
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

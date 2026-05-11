import { createFileRoute, Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { fmtDateTime, fmtMs, fmtTime } from "../lib/datetime";
import { apiFetch } from "../lib/adminAuth";

/** Build an artifact URL with each path segment percent-encoded so filenames
 * with spaces / `#` / `?` round-trip correctly to the backend's
 * /artifacts/{filename:path} route. */
function artifactUrl(runId: string, name: string): string {
  const id = encodeURIComponent(runId);
  const path = name.split("/").map(encodeURIComponent).join("/");
  return `/api/runs/${id}/artifacts/${path}`;
}

export const Route = createFileRoute("/runs/$id")({
  component: RunDetailPage,
});

interface StepEvent {
  step_index: number;
  phase: "prepare" | "action" | "assertion" | "cleanup" | string;
  event: string;
  intent: string | null;
  tool_name: string | null;
  tool_args: Record<string, unknown> | null;
  tool_result: {
    page_url?: string | null;
    page_title?: string | null;
    is_error?: boolean | null;
    console_errors?: number | null;
    console_warnings?: number | null;
    result_text?: string | null;
    case_step?: {
      index: number;
      intent: string;
      expected?: string;
    } | null;
  } | null;
  status: string;
  latency_ms: number | null;
  error_message: string | null;
  occurred_at: string;
  screenshot_after?: string | null;
}

interface RunRow {
  run_id: string;
  trace_id: string;
  project_id: string;
  case_id: string;
  case_version: number;
  env: string;
  status: string;
  started_at: string | null;
  ended_at: string | null;
  duration_ms: number | null;
  artifacts_dir: string | null;
  report_html_path: string | null;
  trace_jsonl_path: string | null;
  input_tokens: number;
  output_tokens: number;
  error_message: string | null;
  created_at: string;
}

interface RunDetail {
  data: {
    run: RunRow;
    steps: StepEvent[];
    failure_context: {
      step_index: number;
      phase: string;
      tool_name: string | null;
      intent: string | null;
      error_message: string | null;
      evidence: string;
    } | null;
  };
}

interface CaseDetail {
  data: {
    case_id: string;
    steps: Array<{ intent: string; expected?: string }>;
    assertions: Array<{
      description: string;
      source?: string;
      confidence?: number;
      rationale?: string;
    }>;
  };
}

interface ArtifactsResponse {
  data: Array<{ name: string; size: number; is_image: boolean; kind?: string }>;
}

const TERMINAL = new Set(["passed", "failed", "flaky", "aborted"]);

function RunDetailPage() {
  const { id } = Route.useParams();
  // Lightbox tracks an index into `allImages` rather than a raw URL so we
  // can do arrow-key + button navigation between adjacent screenshots
  // without closing and reopening the modal each time.
  const [lightboxIdx, setLightboxIdx] = useState<number | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["run", id],
    queryFn: async (): Promise<RunDetail> => {
      const r = await apiFetch(`/api/runs/${id}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    },
    refetchInterval: (q) => {
      const status = (q.state.data as RunDetail | undefined)?.data?.run?.status;
      return status && TERMINAL.has(status) ? false : 1500;
    },
  });

  // The artifact-list polling cadence is gated by the OUTER `run` query's
  // status, not its own data — `q.state.data` here is ArtifactsResponse and
  // would never have a `data.run.status` field, so the original code
  // effectively polled forever every 3s.
  const runStatus = data?.data.run.status;
  const isTerminal = Boolean(runStatus && TERMINAL.has(runStatus));
  const artifacts = useQuery({
    queryKey: ["run-artifacts", id],
    queryFn: async (): Promise<ArtifactsResponse> => {
      const r = await apiFetch(`/api/runs/${id}/artifacts`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    },
    refetchInterval: () => (isTerminal ? false : 3000),
    enabled: Boolean(data),
  });

  const caseId = data?.data.run.case_id ?? "";
  const caseSpec = useQuery({
    queryKey: ["run-case-spec", caseId],
    enabled: Boolean(caseId),
    queryFn: async (): Promise<CaseDetail> => {
      const r = await apiFetch(`/api/cases/${encodeURIComponent(caseId)}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    },
  });

  // The run can flip to terminal a fraction of a second before the report
  // writer flushes the final screenshots/report.html. Without a one-shot
  // refetch we can race past the last artifact list and leave the page
  // showing a stale set.
  useEffect(() => {
    if (isTerminal) {
      void artifacts.refetch();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isTerminal]);

  /** Map step_index → image URL by matching filenames like step-N.png. */
  const screenshotByStep = useMemo<Record<number, string>>(() => {
    const m: Record<number, string> = {};
    if (!artifacts.data) return m;
    for (const f of artifacts.data.data) {
      if (!f.is_image) continue;
      const stepMatch = f.name.match(/step-(\d+)\.(png|jpg|jpeg|webp)$/i);
      if (stepMatch) {
        m[parseInt(stepMatch[1], 10)] = artifactUrl(id, f.name);
      }
    }
    return m;
  }, [artifacts.data, id]);

  /** Loose-match images that don't follow step-N: park them on the page so the
   * user can still see them under "other artifacts". */
  const otherImages = useMemo<Array<{ name: string; url: string }>>(() => {
    if (!artifacts.data) return [];
    return artifacts.data.data
      .filter(
        (f) =>
          f.is_image &&
          !/step-\d+\./i.test(f.name) &&
          !f.name.startsWith("screenshots/")  // already accounted for via step_event mapping
      )
      .map((f) => ({ name: f.name, url: artifactUrl(id, f.name) }));
  }, [artifacts.data, id]);

  /** Unified, ordered image list for the lightbox: step screenshots first
   * (in step order), then "other" screenshots. Indexing into this list is
   * what `lightboxIdx` refers to. Order matters because arrow-key
   * navigation should feel like reading a sequence — not jumping around. */
  const allImages = useMemo<Array<{ name: string; url: string }>>(() => {
    if (!data) return [];
    const out: Array<{ name: string; url: string }> = [];
    for (const s of data.data.steps) {
      const url = s.screenshot_after
        ? artifactUrl(id, s.screenshot_after)
        : screenshotByStep[s.step_index];
      if (url) out.push({ name: `step-${s.step_index}`, url });
    }
    for (const o of otherImages) {
      out.push(o);
    }
    return out;
  }, [data, screenshotByStep, otherImages, id]);

  const openImage = (url: string) => {
    const idx = allImages.findIndex((im) => im.url === url);
    setLightboxIdx(idx >= 0 ? idx : null);
  };
  const closeLightbox = () => setLightboxIdx(null);
  const stepLightbox = (delta: number) => {
    setLightboxIdx((prev) => {
      if (prev == null || allImages.length === 0) return prev;
      const next = prev + delta;
      if (next < 0 || next >= allImages.length) return prev;
      return next;
    });
  };

  // Keyboard navigation: ← prev · → next · Esc close. Only attaches the
  // listener while the lightbox is actually open so we don't intercept
  // arrow keys for users scrolling the page.
  useEffect(() => {
    if (lightboxIdx == null) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        closeLightbox();
      } else if (e.key === "ArrowLeft") {
        e.preventDefault();
        stepLightbox(-1);
      } else if (e.key === "ArrowRight") {
        e.preventDefault();
        stepLightbox(1);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lightboxIdx, allImages.length]);

  if (isLoading) return <div className="text-slate-400">loading…</div>;
  if (error)
    return (
      <div className="text-red-600 text-sm">
        error: {(error as Error).message}
      </div>
    );
  const run = data!.data.run;
  const steps = data!.data.steps;
  const failureContext = data!.data.failure_context;
  const live = !TERMINAL.has(run.status);
  const phaseCounts = phaseSummary(steps);
  const forensicArtifacts = (artifacts.data?.data ?? []).filter(
    (f) => !f.is_image && f.name !== "report.html",
  );

  return (
    <div className="space-y-6">
      <Link to="/runs" className="text-xs text-slate-500 hover:text-slate-900">
        ← all runs
      </Link>
      <div className="flex items-baseline justify-between">
        <div>
          <div className="text-xs uppercase tracking-wide text-slate-400">run</div>
          <h1 className="text-2xl font-semibold font-mono">{run.run_id.slice(0, 12)}…</h1>
          <div className="text-sm text-slate-500 mt-1">
            case{" "}
            <a
              href={`/cases?project_id=${encodeURIComponent(run.project_id)}&case_id=${encodeURIComponent(run.case_id)}`}
              className="text-blue-700 underline"
            >
              {run.case_id}
            </a>{" "}
            · env <code>{run.env}</code> · trace <code>{run.trace_id.slice(0, 8)}</code>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <StatusBadge status={run.status} live={live} />
          {TERMINAL.has(run.status) && (
            <>
              {(run.status === "failed" || run.status === "flaky" || run.status === "aborted") && (
                <Link
                  to="/diagnosis/$id"
                  params={{ id: run.run_id }}
                  className="text-xs px-3 py-1 rounded bg-amber-600 text-white hover:bg-amber-700"
                >
                  AI diagnose →
                </Link>
              )}
              <a
                href={`/api/runs/${run.run_id}/report.html`}
                target="_blank"
                rel="noreferrer"
                className="text-xs px-3 py-1 rounded bg-slate-900 text-white hover:bg-slate-700"
              >
                single-run report ↗
              </a>
              <a
                href={`/api/projects/${encodeURIComponent(run.project_id)}/report.html`}
                target="_blank"
                rel="noreferrer"
                className="text-xs px-3 py-1 rounded border border-slate-200 bg-white text-slate-700 hover:border-slate-400"
              >
                project report ↗
              </a>
            </>
          )}
        </div>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-4 gap-3 text-sm">
        <Card label="duration" value={fmtMs(run.duration_ms)} />
        <Card
          label="steps"
          value={String(steps.length)}
          sub={`${steps.filter(s => s.status === "ok").length} ok / ${steps.filter(s => s.status === "failed").length} failed`}
        />
        <Card label="tokens" value={`${run.input_tokens} in / ${run.output_tokens} out`} />
        <Card label="started" value={fmtTime(run.started_at)} />
      </div>

      {run.error_message && (
        <div className="bg-red-50 border border-red-200 rounded p-3 text-sm text-red-800 font-mono whitespace-pre-wrap">
          {run.error_message}
        </div>
      )}

      <div className="grid grid-cols-2 gap-3">
        <div className="bg-white border border-slate-200 rounded-lg p-4">
          <div className="text-xs uppercase tracking-wide text-slate-400 mb-3">
            execution phases
          </div>
          <div className="grid grid-cols-4 gap-2 text-sm">
            {["prepare", "action", "assertion", "cleanup"].map((phase) => (
              <div key={phase} className="rounded border border-slate-100 p-2">
                <div className="text-xs text-slate-400">{phase}</div>
                <div className="text-lg font-semibold">{phaseCounts[phase] ?? 0}</div>
              </div>
            ))}
          </div>
        </div>
        <div className="bg-white border border-slate-200 rounded-lg p-4">
          <div className="text-xs uppercase tracking-wide text-slate-400 mb-3">
            first failure
          </div>
          {failureContext ? (
            <div className="text-sm space-y-1">
              <div>
                <PhaseBadge phase={failureContext.phase} />{" "}
                <span className="font-mono text-xs">step {failureContext.step_index}</span>{" "}
                <span className="text-slate-500">{failureContext.tool_name}</span>
              </div>
              <div className="font-medium">{failureContext.intent || "(no label)"}</div>
              {(failureContext.error_message || failureContext.evidence) && (
                <pre className="text-xs text-red-700 whitespace-pre-wrap max-h-20 overflow-auto">
                  {failureContext.error_message || failureContext.evidence}
                </pre>
              )}
            </div>
          ) : (
            <div className="text-sm text-slate-400">no failed step recorded</div>
          )}
        </div>
      </div>

      <CaseSpecPanel
        caseId={run.case_id}
        loading={caseSpec.isLoading}
        error={caseSpec.error as Error | null}
        steps={caseSpec.data?.data.steps ?? []}
        assertions={caseSpec.data?.data.assertions ?? []}
      />

      {/* Step timeline */}
      <div className="bg-white border border-slate-200 rounded-lg overflow-hidden">
        <div className="px-4 py-2 text-xs uppercase tracking-wide text-slate-400 border-b border-slate-100 flex items-center justify-between">
          <span>step timeline</span>
          {live && (
            <span className="text-xs text-blue-600 font-mono">
              ● polling every 1.5s
            </span>
          )}
        </div>
        {steps.length === 0 ? (
          <div className="p-6 text-slate-400 text-sm">
            {live ? "agent starting…" : "no steps recorded"}
          </div>
        ) : (
          <ol>
            {steps.map((s) => (
              <StepRow
                key={s.step_index}
                s={s}
                screenshotUrl={
                  // Prefer the explicit screenshot path the backend recorded
                  // on the StepEvent; fall back to filename-pattern guess for
                  // older runs that didn't populate it.
                  s.screenshot_after
                    ? artifactUrl(id, s.screenshot_after)
                    : (screenshotByStep[s.step_index] ?? null)
                }
                onShowImage={openImage}
              />
            ))}
          </ol>
        )}
      </div>

      <RunHistorySection
        currentRunId={run.run_id}
        caseId={run.case_id}
        projectId={run.project_id}
      />

      {otherImages.length > 0 && (
        <div className="bg-white border border-slate-200 rounded-lg p-4">
          <div className="text-xs uppercase tracking-wide text-slate-400 mb-3">
            other screenshots
          </div>
          <div className="grid grid-cols-4 gap-3">
            {otherImages.map((img) => (
              <button
                key={img.name}
                onClick={() => openImage(img.url)}
                className="text-left"
              >
                <img
                  src={img.url}
                  alt={img.name}
                  className="w-full rounded border border-slate-200 hover:border-blue-400"
                />
                <div className="text-xs text-slate-500 mt-1 font-mono truncate">
                  {img.name}
                </div>
              </button>
            ))}
          </div>
        </div>
      )}

      {forensicArtifacts.length > 0 && (
        <div className="bg-white border border-slate-200 rounded-lg p-4">
          <div className="text-xs uppercase tracking-wide text-slate-400 mb-3">
            trace / logs / data
          </div>
          <div className="grid grid-cols-2 gap-2 text-sm">
            {forensicArtifacts.map((f) => (
              <a
                key={f.name}
                href={artifactUrl(id, f.name)}
                target="_blank"
                rel="noreferrer"
                className="flex items-center justify-between gap-3 rounded border border-slate-100 px-3 py-2 hover:border-blue-300"
              >
                <span className="font-mono text-xs truncate">{f.name}</span>
                <span className="text-xs text-slate-400 shrink-0">
                  {f.kind || "file"} · {formatBytes(f.size)}
                </span>
              </a>
            ))}
          </div>
        </div>
      )}

      {lightboxIdx != null && allImages[lightboxIdx] && (() => {
        const cur = allImages[lightboxIdx];
        const prevDisabled = lightboxIdx === 0;
        const nextDisabled = lightboxIdx >= allImages.length - 1;
        return (
          <div
            className="fixed inset-0 bg-black/85 flex flex-col items-center justify-center z-50"
            onClick={closeLightbox}
          >
            {/* Stop the click-through-to-close on the image area + chrome
                so users can interact with prev/next without dismissing. */}
            <div
              className="relative max-w-[94vw] max-h-[88vh] flex items-center"
              onClick={(e) => e.stopPropagation()}
            >
              <button
                onClick={() => stepLightbox(-1)}
                disabled={prevDisabled}
                className="absolute left-[-3rem] top-1/2 -translate-y-1/2 text-white/80 hover:text-white text-4xl px-2 disabled:opacity-30 disabled:cursor-not-allowed"
                title="previous (←)"
              >
                ‹
              </button>
              <img
                src={cur.url}
                alt={cur.name}
                className="max-w-[90vw] max-h-[80vh] rounded shadow-2xl object-contain"
              />
              <button
                onClick={() => stepLightbox(1)}
                disabled={nextDisabled}
                className="absolute right-[-3rem] top-1/2 -translate-y-1/2 text-white/80 hover:text-white text-4xl px-2 disabled:opacity-30 disabled:cursor-not-allowed"
                title="next (→)"
              >
                ›
              </button>
            </div>
            <div
              className="mt-3 flex items-center gap-4 text-white/80 text-xs"
              onClick={(e) => e.stopPropagation()}
            >
              <span className="font-mono">
                {lightboxIdx + 1} / {allImages.length}
              </span>
              <span className="font-mono">{cur.name}</span>
              <span className="text-white/50">←/→ navigate · Esc close</span>
              <button
                onClick={closeLightbox}
                className="ml-2 px-2 py-0.5 rounded bg-white/10 hover:bg-white/20"
              >
                close
              </button>
            </div>
          </div>
        );
      })()}
    </div>
  );
}

interface SiblingRun {
  run_id: string;
  status: string;
  duration_ms: number | null;
  started_at: string | null;
  created_at: string;
  error_message: string | null;
}

/** Cross-links between runs of the same case_id. Without this, navigating
 * from "this case failed 5 times — show me the previous attempt" required
 * going back to /runs, finding the case, and there'd be no row for the
 * older run anymore (the list dedupes to latest). The detail page is the
 * right place to surface history because it's already case-scoped. */
function RunHistorySection({
  currentRunId,
  caseId,
  projectId,
}: {
  currentRunId: string;
  caseId: string;
  projectId: string;
}) {
  const siblings = useQuery({
    queryKey: ["case-run-history", projectId, caseId],
    queryFn: async (): Promise<{ data: SiblingRun[] }> => {
      const r = await apiFetch(
        `/api/runs/?project_id=${encodeURIComponent(projectId)}&case_id=${encodeURIComponent(caseId)}&limit=50`,
      );
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    },
  });

  if (siblings.isLoading) return null;
  const rows = siblings.data?.data ?? [];
  if (rows.length <= 1) return null; // only this run; nothing to link

  return (
    <div className="bg-white border border-slate-200 rounded-lg p-4">
      <div className="text-xs uppercase tracking-wide text-slate-400 mb-3">
        Run history for <code className="font-mono">{caseId}</code> ·{" "}
        <span className="text-slate-500 normal-case">{rows.length} total run{rows.length > 1 ? "s" : ""}</span>
      </div>
      <ol className="space-y-1.5 text-sm">
        {rows.map((r, i) => {
          const isCurrent = r.run_id === currentRunId;
          return (
            <li key={r.run_id} className="flex items-center gap-3">
              <span className="text-xs text-slate-400 font-mono w-6 text-right">
                #{rows.length - i}
              </span>
              {isCurrent ? (
                <span className="font-mono text-xs px-2 py-0.5 rounded bg-blue-100 text-blue-700">
                  {r.run_id.slice(0, 8)} (this run)
                </span>
              ) : (
                <Link
                  to="/runs/$id"
                  params={{ id: r.run_id }}
                  className="font-mono text-xs text-blue-700 hover:underline"
                >
                  {r.run_id.slice(0, 8)}
                </Link>
              )}
              <StatusBadge status={r.status} live={false} />
              <span className="text-xs text-slate-500 font-mono">
                {fmtMs(r.duration_ms)}
              </span>
              <span className="text-xs text-slate-400">
                {fmtDateTime(r.started_at ?? r.created_at)}
              </span>
              {r.error_message && (
                <span className="text-xs text-red-600 truncate max-w-md" title={r.error_message}>
                  {r.error_message.split("\n")[0].slice(0, 80)}
                </span>
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
}

function CaseSpecPanel({
  caseId,
  loading,
  error,
  steps,
  assertions,
}: {
  caseId: string;
  loading: boolean;
  error: Error | null;
  steps: Array<{ intent: string; expected?: string }>;
  assertions: Array<{
    description: string;
    source?: string;
    confidence?: number;
    rationale?: string;
  }>;
}) {
  return (
    <div className="bg-white border border-slate-200 rounded-lg p-4">
      <div className="text-xs uppercase tracking-wide text-slate-400 mb-3">
        case spec · <code className="font-mono normal-case">{caseId}</code>
      </div>
      {loading ? (
        <div className="text-sm text-slate-400">loading case steps…</div>
      ) : error ? (
        <div className="text-sm text-amber-700">
          case details unavailable: {error.message}
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-4 text-sm">
          <div>
            <div className="text-xs uppercase tracking-wide text-slate-400 mb-2">
              steps
            </div>
            {steps.length ? (
              <ol className="list-decimal pl-5 space-y-2">
                {steps.map((s, i) => (
                  <li key={i}>
                    <div>{s.intent}</div>
                    {s.expected && (
                      <div className="text-slate-500 italic">→ {s.expected}</div>
                    )}
                  </li>
                ))}
              </ol>
            ) : (
              <div className="text-slate-400">—</div>
            )}
          </div>
          <div>
            <div className="text-xs uppercase tracking-wide text-slate-400 mb-2">
              assertions
            </div>
            {assertions.length ? (
              <ul className="list-disc pl-5 space-y-2">
                {assertions.map((a, i) => (
                  <li key={i}>
                    <div>{a.description}</div>
                    {(a.source || a.confidence != null || a.rationale) && (
                      <div className="text-[11px] text-slate-500">
                        {a.source || "source?"}
                        {a.confidence != null && (
                          <> · confidence {Math.round(a.confidence * 100)}%</>
                        )}
                        {a.rationale && <> · {a.rationale}</>}
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            ) : (
              <div className="text-slate-400">—</div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function StepRow({
  s,
  screenshotUrl,
  onShowImage,
}: {
  s: StepEvent;
  screenshotUrl: string | null;
  onShowImage: (url: string) => void;
}) {
  const ok = s.status !== "failed";
  return (
    <li
      className={
        "border-t border-slate-100 px-4 py-3 text-sm " +
        (ok ? "" : "bg-red-50/30")
      }
    >
      <div className="flex items-start gap-3">
        <span
          className={
            "inline-block w-6 h-6 rounded-full text-center text-xs leading-6 font-mono shrink-0 " +
            (ok ? "bg-emerald-100 text-emerald-700" : "bg-red-100 text-red-700")
          }
        >
          {s.step_index}
        </span>
        <div className="flex-1 min-w-0">
          <div className="font-mono text-xs text-slate-500">{s.tool_name}</div>
          <div className="flex items-center gap-2">
            <PhaseBadge phase={s.phase || "action"} />
            <div className="font-medium">{s.intent || "(no label)"}</div>
          </div>
          {s.tool_result?.case_step?.expected && (
            <div className="mt-1 text-xs text-slate-500 italic">
              → {s.tool_result.case_step.expected}
            </div>
          )}
          {s.tool_args && Object.keys(s.tool_args).length > 0 && (
            <pre className="mt-1 text-xs text-slate-400 font-mono whitespace-pre-wrap break-all">
              {JSON.stringify(s.tool_args, null, 0)}
            </pre>
          )}
          {s.error_message && (
            <pre className="mt-2 text-xs text-red-700 whitespace-pre-wrap">
              {s.error_message}
            </pre>
          )}
        </div>
        <div className="text-xs text-slate-400 font-mono text-right shrink-0">
          {s.tool_result?.page_url && (
            <span className="block max-w-[260px] truncate">{s.tool_result.page_url}</span>
          )}
          {s.tool_result?.page_title && (
            <span className="block text-slate-300">{s.tool_result.page_title}</span>
          )}
        </div>
        {screenshotUrl && (
          <button
            onClick={() => onShowImage(screenshotUrl)}
            className="shrink-0"
            title="click to enlarge"
          >
            <img
              src={screenshotUrl}
              alt={`step ${s.step_index}`}
              className="w-32 max-h-20 rounded border border-slate-200 hover:border-blue-400 cursor-zoom-in object-cover object-top"
            />
          </button>
        )}
      </div>
    </li>
  );
}

function PhaseBadge({ phase }: { phase: string }) {
  const m: Record<string, string> = {
    prepare: "bg-slate-100 text-slate-600",
    action: "bg-blue-100 text-blue-700",
    assertion: "bg-amber-100 text-amber-700",
    cleanup: "bg-zinc-100 text-zinc-600",
  };
  return (
    <span className={"text-[11px] px-1.5 py-0.5 rounded font-mono " + (m[phase] || m.action)}>
      {phase}
    </span>
  );
}

function phaseSummary(steps: StepEvent[]): Record<string, number> {
  const out: Record<string, number> = {};
  for (const s of steps) {
    const phase = s.phase || "action";
    out[phase] = (out[phase] ?? 0) + 1;
  }
  return out;
}

function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function Card({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="bg-white border border-slate-200 rounded p-3">
      <div className="text-xs uppercase tracking-wide text-slate-400">{label}</div>
      <div className="text-lg font-semibold mt-1">{value}</div>
      {sub && <div className="text-xs text-slate-500 mt-0.5">{sub}</div>}
    </div>
  );
}

function StatusBadge({ status, live }: { status: string; live: boolean }) {
  const m: Record<string, string> = {
    pending: "bg-slate-100 text-slate-600",
    running: "bg-blue-100 text-blue-700",
    passed: "bg-emerald-100 text-emerald-700",
    failed: "bg-red-100 text-red-700",
    flaky: "bg-amber-100 text-amber-700",
    aborted: "bg-slate-300 text-slate-700",
  };
  return (
    <span
      className={
        "text-sm px-3 py-1 rounded-full font-mono " + (m[status] || m["pending"])
      }
    >
      {live && <span className="inline-block animate-pulse mr-1">●</span>}
      {status}
    </span>
  );
}

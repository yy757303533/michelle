import { createFileRoute, Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

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
  data: { run: RunRow; steps: StepEvent[] };
}

interface ArtifactsResponse {
  data: Array<{ name: string; size: number; is_image: boolean }>;
}

const TERMINAL = new Set(["passed", "failed", "flaky", "aborted"]);

function RunDetailPage() {
  const { id } = Route.useParams();
  const [lightbox, setLightbox] = useState<string | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["run", id],
    queryFn: async (): Promise<RunDetail> => {
      const r = await fetch(`/api/runs/${id}`);
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
      const r = await fetch(`/api/runs/${id}/artifacts`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    },
    refetchInterval: () => (isTerminal ? false : 3000),
    enabled: Boolean(data),
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

  if (isLoading) return <div className="text-slate-400">loading…</div>;
  if (error)
    return (
      <div className="text-red-600 text-sm">
        error: {(error as Error).message}
      </div>
    );
  const run = data!.data.run;
  const steps = data!.data.steps;
  const live = !TERMINAL.has(run.status);

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
            case <Link to="/cases" className="text-blue-700 underline">{run.case_id}</Link>{" "}
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
                open HTML report ↗
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
        <Card label="started" value={run.started_at ? new Date(run.started_at).toLocaleTimeString() : "—"} />
      </div>

      {run.error_message && (
        <div className="bg-red-50 border border-red-200 rounded p-3 text-sm text-red-800 font-mono whitespace-pre-wrap">
          {run.error_message}
        </div>
      )}

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
                onShowImage={(url) => setLightbox(url)}
              />
            ))}
          </ol>
        )}
      </div>

      {otherImages.length > 0 && (
        <div className="bg-white border border-slate-200 rounded-lg p-4">
          <div className="text-xs uppercase tracking-wide text-slate-400 mb-3">
            other screenshots
          </div>
          <div className="grid grid-cols-4 gap-3">
            {otherImages.map((img) => (
              <button
                key={img.name}
                onClick={() => setLightbox(img.url)}
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

      {lightbox && (
        <div
          className="fixed inset-0 bg-black/75 flex items-center justify-center z-50 cursor-zoom-out"
          onClick={() => setLightbox(null)}
        >
          <img
            src={lightbox}
            alt="screenshot"
            className="max-w-[92vw] max-h-[92vh] rounded shadow-2xl"
          />
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
          <div className="font-medium">{s.intent || "(no label)"}</div>
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

function fmtMs(ms: number | null): string {
  if (ms == null) return "—";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

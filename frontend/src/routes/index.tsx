import { createFileRoute, Link } from "@tanstack/react-router";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";

export const Route = createFileRoute("/")({
  component: Dashboard,
});

interface HealthResponse {
  status: string;
  version: string;
  env: string;
  providers: Record<string, boolean>;
}

interface LLMHealth {
  data: Record<string, { available: boolean; priority: number }>;
  available_providers: string[];
}

interface CasesResponse {
  data: Array<{ case_id: string; name: string; review_status: string }>;
  count: number;
  counts_by_status: Record<string, number>;
}

interface RunsResponse {
  data: Array<{
    run_id: string;
    case_id: string;
    status: string;
    duration_ms: number | null;
    started_at: string | null;
  }>;
  count: number;
}

interface PRDsResponse {
  data: Array<{
    prd_id: string;
    name: string;
    version: number;
    chapter_count: number;
    uploaded_at: string;
  }>;
}

interface ProbeResult {
  ok: boolean;
  provider?: string;
  model?: string;
  text?: string;
  latency_ms?: number;
  input_tokens?: number;
  output_tokens?: number;
  cost_usd?: number | null;
  error?: string;
  error_type?: string;
}

function Dashboard() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Dashboard</h1>
        <p className="text-slate-500 text-sm mt-1">
          PRD → AI cases → review → autonomous run → AI diagnosis (Day 11) → sediment.
        </p>
      </div>

      <BackendHealth />

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <CasesWidget />
        <RecentRunsWidget />
        <PRDsWidget />
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <LLMProvidersWidget />
        <ProbePanel />
      </div>
    </div>
  );
}

function BackendHealth() {
  const health = useQuery({
    queryKey: ["healthz"],
    queryFn: async (): Promise<HealthResponse> => {
      const r = await fetch("/healthz");
      if (!r.ok) throw new Error("backend down");
      return r.json();
    },
    refetchInterval: 5000,
  });

  return (
    <Panel title="Backend">
      {health.isLoading && <span className="text-slate-400 text-sm">checking…</span>}
      {health.error && <span className="text-red-600 text-sm">offline</span>}
      {health.data && (
        <div className="flex items-center gap-4 text-sm">
          <Pill ok>{health.data.status}</Pill>
          <span className="text-slate-500">
            v<code>{health.data.version}</code>
          </span>
          <span className="text-slate-500">
            env <code>{health.data.env}</code>
          </span>
        </div>
      )}
    </Panel>
  );
}

function CasesWidget() {
  const cases = useQuery({
    queryKey: ["cases-summary"],
    queryFn: async (): Promise<CasesResponse> => {
      const r = await fetch("/api/cases/?limit=200");
      return r.json();
    },
    refetchInterval: 10000,
  });

  const counts = cases.data?.counts_by_status ?? {};
  const total = Object.values(counts).reduce((a, b) => a + b, 0);

  return (
    <Panel title="Cases" linkTo="/cases" linkLabel="manage →">
      {cases.isLoading ? (
        <span className="text-slate-400 text-sm">…</span>
      ) : total === 0 ? (
        <Empty cta="upload a PRD" to="/prd" />
      ) : (
        <div className="space-y-1 text-sm">
          <Row label="total" value={String(total)} mono />
          {Object.entries(counts).map(([k, v]) => (
            <Row
              key={k}
              label={k}
              value={String(v)}
              mono
              valueClass={
                k === "approved"
                  ? "text-emerald-700"
                  : k === "rejected"
                    ? "text-red-700"
                    : "text-slate-700"
              }
            />
          ))}
        </div>
      )}
    </Panel>
  );
}

function RecentRunsWidget() {
  const runs = useQuery({
    queryKey: ["runs-recent"],
    queryFn: async (): Promise<RunsResponse> => {
      const r = await fetch("/api/runs/?limit=5");
      return r.json();
    },
    refetchInterval: 3000,
  });

  return (
    <Panel title="Recent runs" linkTo="/runs" linkLabel="all →">
      {runs.isLoading ? (
        <span className="text-slate-400 text-sm">…</span>
      ) : (runs.data?.count ?? 0) === 0 ? (
        <Empty cta="run an approved case" to="/cases" />
      ) : (
        <ul className="space-y-1.5 text-sm">
          {runs.data?.data.slice(0, 5).map((r) => (
            <li key={r.run_id} className="flex items-center gap-2">
              <Link
                to="/runs/$id"
                params={{ id: r.run_id }}
                className="font-mono text-xs text-blue-700 hover:underline"
              >
                {r.run_id.slice(0, 8)}
              </Link>
              <span className="text-xs text-slate-400 truncate flex-1">{r.case_id}</span>
              <StatusPill status={r.status} small />
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

function PRDsWidget() {
  const prds = useQuery({
    queryKey: ["prds-recent"],
    queryFn: async (): Promise<PRDsResponse> => {
      const r = await fetch("/api/prd/?project_id=michelle");
      return r.json();
    },
    refetchInterval: 30000,
  });

  return (
    <Panel title="Recent PRDs" linkTo="/prd" linkLabel="upload →">
      {prds.isLoading ? (
        <span className="text-slate-400 text-sm">…</span>
      ) : (prds.data?.data.length ?? 0) === 0 ? (
        <Empty cta="upload first PRD" to="/prd" />
      ) : (
        <ul className="space-y-1.5 text-sm">
          {prds.data?.data.slice(0, 4).map((p) => (
            <li key={p.prd_id} className="flex items-center gap-2">
              <span className="text-xs text-slate-500 truncate flex-1">{p.name}</span>
              <span className="text-xs text-slate-400 font-mono">v{p.version}</span>
              <span className="text-xs text-slate-400 font-mono">{p.chapter_count}ch</span>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

function LLMProvidersWidget() {
  const llm = useQuery({
    queryKey: ["llm-health"],
    queryFn: async (): Promise<LLMHealth> => {
      const r = await fetch("/api/llm/health");
      return r.json();
    },
    refetchInterval: 30000,
  });

  return (
    <Panel title="LLM providers (priority order)">
      {llm.isLoading ? (
        <span className="text-slate-400 text-sm">…</span>
      ) : (
        <div className="space-y-1 text-xs">
          {Object.entries(llm.data?.data ?? {})
            .sort(([, a], [, b]) => a.priority - b.priority)
            .map(([name, p]) => (
              <div key={name} className="flex justify-between">
                <span>
                  <code>{name}</code>{" "}
                  <span className="text-slate-400">prio {p.priority}</span>
                </span>
                <span className={p.available ? "text-emerald-600" : "text-slate-300"}>
                  {p.available ? "configured" : "off"}
                </span>
              </div>
            ))}
        </div>
      )}
    </Panel>
  );
}

function ProbePanel() {
  const [prefer, setPrefer] = useState<string>("");
  const [last, setLast] = useState<ProbeResult | null>(null);

  const probe = useMutation({
    mutationFn: async (): Promise<ProbeResult> => {
      const r = await fetch("/api/llm/probe", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prefer: prefer || null }),
      });
      const body = await r.json();
      return body.data as ProbeResult;
    },
    onSuccess: setLast,
  });

  return (
    <Panel title="Probe">
      <div className="flex items-center gap-2 text-xs mb-2">
        <select
          className="border border-slate-200 rounded px-2 py-0.5 text-xs"
          value={prefer}
          onChange={(e) => setPrefer(e.target.value)}
        >
          <option value="">auto</option>
          <option value="claude-cli">claude-cli</option>
          <option value="codex-cli">codex-cli</option>
          <option value="flywheel">flywheel</option>
          <option value="deepseek">deepseek</option>
          <option value="qwen">qwen</option>
          <option value="glm">glm</option>
          <option value="kimi">kimi</option>
          <option value="gemini">gemini</option>
          <option value="minimax">minimax</option>
        </select>
        <button
          className="bg-slate-900 text-white px-2 py-0.5 rounded hover:bg-slate-700 disabled:opacity-50"
          disabled={probe.isPending}
          onClick={() => probe.mutate()}
        >
          {probe.isPending ? "…" : "probe"}
        </button>
        <span className="text-slate-400">10-token "ok" round-trip</span>
      </div>
      {last && (
        <div className="text-xs space-y-0.5 bg-slate-50 rounded p-2">
          {last.ok ? (
            <>
              <div className="flex justify-between">
                <Pill ok small>
                  ok
                </Pill>
                <code className="text-slate-500">
                  {last.provider} · {last.model}
                </code>
              </div>
              <Row
                label="text"
                value={<code className="text-emerald-700">{last.text}</code>}
              />
              <Row label="latency" value={<code>{last.latency_ms}ms</code>} />
              <Row
                label="tokens"
                value={
                  <code>
                    {last.input_tokens}/{last.output_tokens}
                    {last.cost_usd != null ? ` · $${last.cost_usd.toFixed(3)}` : ""}
                  </code>
                }
              />
            </>
          ) : (
            <>
              <Pill small>err</Pill>
              <Row label="type" value={<code className="text-red-700">{last.error_type}</code>} />
              <Row label="msg" value={<code className="break-all">{last.error}</code>} />
            </>
          )}
        </div>
      )}
    </Panel>
  );
}

// ── primitives ──

function Panel({
  title,
  children,
  linkTo,
  linkLabel,
}: {
  title: string;
  children: React.ReactNode;
  linkTo?: string;
  linkLabel?: string;
}) {
  return (
    <div className="bg-white border border-slate-200 rounded-lg p-4">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs uppercase tracking-wide text-slate-400">{title}</span>
        {linkTo && (
          <Link to={linkTo} className="text-xs text-blue-700 hover:underline">
            {linkLabel || "open"}
          </Link>
        )}
      </div>
      {children}
    </div>
  );
}

function Row({
  label,
  value,
  mono,
  valueClass,
}: {
  label: string;
  value: React.ReactNode;
  mono?: boolean;
  valueClass?: string;
}) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-slate-500">{label}</span>
      <span className={(mono ? "font-mono " : "") + (valueClass || "")}>{value}</span>
    </div>
  );
}

function Pill({
  children,
  ok,
  small,
}: {
  children: React.ReactNode;
  ok?: boolean;
  small?: boolean;
}) {
  return (
    <span
      className={
        "inline-block rounded font-mono " +
        (small ? "text-xs px-1.5 py-0.5 " : "text-xs px-2 py-0.5 ") +
        (ok ? "bg-emerald-50 text-emerald-700" : "bg-red-50 text-red-700")
      }
    >
      {children}
    </span>
  );
}

function StatusPill({ status, small }: { status: string; small?: boolean }) {
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
        "rounded-full font-mono " +
        (small ? "text-[10px] px-2 py-0.5 " : "text-xs px-2 py-0.5 ") +
        (m[status] || m.pending)
      }
    >
      {status === "running" && <span className="inline-block animate-pulse mr-1">●</span>}
      {status}
    </span>
  );
}

function Empty({ cta, to }: { cta: string; to: string }) {
  return (
    <Link to={to} className="block text-sm text-slate-400 hover:text-slate-700">
      → {cta}
    </Link>
  );
}

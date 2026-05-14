import { createFileRoute, Link } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { LLMRunnerStatusLight } from "../components/LLMRunnerStatusLight";
import { useCurrentProject } from "../lib/useCurrentProject";
import { useLLMRunnerStatus } from "../lib/useLLMRunnerStatus";
import { fmtMs } from "../lib/datetime";
import { apiFetch, getCurrentUser } from "../lib/adminAuth";

export const Route = createFileRoute("/")({
  component: Dashboard,
});

function asArray<T>(value: unknown): T[] {
  return Array.isArray(value) ? value : [];
}

interface HealthResponse {
  status: string;
  version: string;
  env: string;
  providers: Record<string, boolean>;
}

interface LLMHealth {
  data: Record<string, { available: boolean; priority: number; detail?: string }>;
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
    project_id: string;
    duration_ms: number | null;
    started_at: string | null;
  }>;
  count: number;
}

interface RegressionAssetsResponse {
  data: Array<{
    asset_id: string;
    case_id: string;
    source_run_id: string;
    status: string;
    action_plan: Array<Record<string, unknown>>;
    locator_candidates: Array<Record<string, unknown>>;
    assertions: Array<Record<string, unknown>>;
    last_replay_run_id: string | null;
    last_status: string;
    updated_at: string;
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

interface TrendsResponse {
  data: {
    total: number;
    terminal: number;
    pass_rate: number | null;
    flaky_rate: number | null;
    avg_duration_ms: number | null;
    by_status: Record<string, number>;
    by_day: Array<Record<string, number | string>>;
  };
}

interface SelfCheckResponse {
  data: {
    checks: Array<{ name: string; ok: boolean; detail: string; elapsed_ms?: number | null }>;
  };
}

interface DevContextStatusResponse {
  data: {
    workspace: {
      enabled: boolean;
      ok: boolean;
      root: string;
      detail: string;
      repos: Array<{ name: string; path: string; exists: boolean; detail: string }>;
    };
    zdev_mcp: {
      configured: boolean;
      command: string;
      command_available: boolean;
      cwd: string;
      cwd_exists: boolean;
      entrypoint: string;
      entrypoint_exists: boolean;
    };
    code_search: {
      repos: string[];
      max_files: number;
      max_matches_per_file: number;
    };
    server_logs: {
      configured: boolean;
      servers: Array<{ name: string; env: string; roles: string[]; log_paths: string[] }>;
    };
    security: {
      ok: boolean;
      findings: string[];
      boundary: string[];
    };
  };
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

interface ProviderProbeResult extends ProbeResult {
  provider: string;
}

interface LLMMetricsResponse {
  data: {
    totals: {
      calls: number;
      failed: number;
      failure_rate: number;
      input_tokens: number;
      output_tokens: number;
      cost_usd: number;
    };
    providers: Array<{
      provider: string;
      calls: number;
      success: number;
      failed: number;
      failure_rate: number;
      input_tokens: number;
      output_tokens: number;
      cost_usd: number;
      avg_latency_ms: number;
      last_error: string;
    }>;
  };
}

function Dashboard() {
  const { projectId } = useCurrentProject();
  const user = getCurrentUser<{ username: string; role: string }>();
  const isAdmin = user?.role === "admin";
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">
          Dashboard
          {projectId && (
            <span className="text-slate-400 text-base font-normal"> / {projectId}</span>
          )}
        </h1>
        <p className="text-slate-500 text-sm mt-1">
          PRD → coverage review → case drafts → run → regression asset → replay → diagnosis.
        </p>
      </div>

      <BackendHealth />
      <SelfCheckPanel />
      {isAdmin && <DevContextPanel />}
      <TrendsPanel projectId={projectId} />

      {projectId && <CurrentProjectPanel projectId={projectId} />}

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <CasesWidget projectId={projectId} />
        <RecentRunsWidget projectId={projectId} />
        <PRDsWidget projectId={projectId} />
      </div>
      {projectId && <RegressionAssetsWidget projectId={projectId} />}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <LLMProvidersWidget />
        <ProbePanel />
      </div>
      <LLMMetricsPanel />

      <RuntimeSettingsPanel />
      <AdminOpsPanel projectId={projectId} />
    </div>
  );
}

function LLMMetricsPanel() {
  const qc = useQueryClient();
  const [expanded, setExpanded] = useState(false);
  const metrics = useQuery({
    queryKey: ["llm-metrics"],
    queryFn: async (): Promise<LLMMetricsResponse> => {
      const r = await apiFetch("/api/llm/metrics?limit=500");
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    },
    refetchInterval: 30000,
  });
  const clear = useMutation({
    mutationFn: async () => {
      const r = await apiFetch("/api/llm/metrics", { method: "DELETE" });
      if (!r.ok) throw new Error(await r.text());
      return r.json();
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["llm-metrics"] }),
  });
  const data = metrics.data?.data;
  const pct = (v: number) => `${Math.round(v * 100)}%`;
  const hasFailures = Boolean(data?.totals.failed);
  return (
    <Panel title="LLM health history">
      {!data ? (
        <span className="text-slate-400 text-sm">…</span>
      ) : (
        <div className="space-y-3 text-sm">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="text-xs text-slate-500">
              Historical telemetry for provider debugging. Model selection lives in{" "}
              <code>Platform settings / model_routing</code>.
            </div>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => setExpanded((v) => !v)}
                className="text-xs rounded border border-slate-200 px-2 py-1 hover:bg-slate-50"
              >
                {expanded ? "hide details" : hasFailures ? "show errors" : "show details"}
              </button>
              <button
                type="button"
                onClick={() => clear.mutate()}
                disabled={clear.isPending || data.totals.calls === 0}
                className="text-xs rounded border border-slate-200 px-2 py-1 hover:bg-slate-50 disabled:opacity-50"
              >
                {clear.isPending ? "clearing…" : "clear history"}
              </button>
            </div>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
            <Row label="calls" value={data.totals.calls} mono />
            <Row
              label="failures"
              value={`${data.totals.failed} (${pct(data.totals.failure_rate)})`}
              mono
              valueClass={data.totals.failed ? "text-red-700" : "text-emerald-700"}
            />
            <Row label="input tokens" value={data.totals.input_tokens} mono />
            <Row label="output tokens" value={data.totals.output_tokens} mono />
            <Row label="cost" value={`$${data.totals.cost_usd.toFixed(3)}`} mono />
          </div>
          {hasFailures && !expanded && (
            <div className="rounded border border-amber-200 bg-amber-50 px-2 py-1 text-xs text-amber-800">
              Historical provider failures exist. Use details only when generation,
              execution, or diagnosis fails.
            </div>
          )}
          {expanded && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-2 text-xs">
              {data.providers.map((p) => (
                <div key={p.provider} className="rounded border border-slate-200 px-2 py-1">
                  <div className="flex items-center justify-between">
                    <code>{p.provider}</code>
                    <span className={p.failed ? "text-red-700" : "text-emerald-700"}>
                      {p.calls} calls · {pct(p.failure_rate)} failed
                    </span>
                  </div>
                  <div className="text-slate-500">
                    {p.input_tokens}/{p.output_tokens} tokens · {p.avg_latency_ms}ms avg
                    {p.cost_usd ? ` · $${p.cost_usd.toFixed(3)}` : ""}
                  </div>
                  {p.last_error && <div className="truncate text-red-700">{p.last_error}</div>}
                </div>
              ))}
            </div>
          )}
          {clear.error && (
            <div className="text-xs text-red-600">{(clear.error as Error).message}</div>
          )}
        </div>
      )}
    </Panel>
  );
}

function SelfCheckPanel() {
  const [includeMcpProbe, setIncludeMcpProbe] = useState(false);
  const [includeLlmProbe, setIncludeLlmProbe] = useState(false);
  const check = useQuery({
    queryKey: ["selfcheck", includeMcpProbe, includeLlmProbe],
    queryFn: async (): Promise<SelfCheckResponse> => {
      const params = new URLSearchParams();
      if (includeMcpProbe) params.set("include_mcp_probe", "true");
      if (includeLlmProbe) params.set("include_llm_probe", "true");
      const qs = params.toString() ? `?${params.toString()}` : "";
      const r = await apiFetch(`/api/settings/selfcheck${qs}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    },
    refetchInterval: includeMcpProbe || includeLlmProbe ? false : 15000,
  });
  const rows = asArray<{ name: string; ok: boolean; detail: string; elapsed_ms?: number | null }>(
    check.data?.data?.checks,
  );
  return (
    <Panel title="Environment self-check">
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className="text-xs text-slate-500">
          Probes spend real startup/model time; use before sharing a pilot environment.
        </span>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setIncludeMcpProbe(true)}
            disabled={check.isFetching && includeMcpProbe}
            className="text-xs rounded border border-slate-200 px-2 py-1 hover:bg-slate-50 disabled:opacity-50"
          >
            {check.isFetching && includeMcpProbe ? "probing…" : "probe MCP"}
          </button>
          <button
            type="button"
            onClick={() => setIncludeLlmProbe(true)}
            disabled={check.isFetching && includeLlmProbe}
            className="text-xs rounded border border-slate-200 px-2 py-1 hover:bg-slate-50 disabled:opacity-50"
          >
            {check.isFetching && includeLlmProbe ? "probing…" : "probe LLM"}
          </button>
        </div>
      </div>
      {check.isLoading ? (
        <span className="text-slate-400 text-sm">checking…</span>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-2 text-xs">
          {rows.map((r) => (
            <div key={r.name} className="rounded border border-slate-200 px-2 py-1">
              <span className={r.ok ? "text-emerald-700" : "text-amber-700"}>
                {r.ok ? "ok" : "check"}
              </span>{" "}
              <code>{r.name}</code>
              <div className="text-slate-500 truncate">
                {r.detail}
                {typeof r.elapsed_ms === "number" ? ` (${fmtMs(r.elapsed_ms)})` : ""}
              </div>
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}

function DevContextPanel() {
  const status = useQuery({
    queryKey: ["dev-context-status"],
    queryFn: async (): Promise<DevContextStatusResponse> => {
      const r = await apiFetch("/api/dev-context/status");
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    },
    refetchInterval: 30000,
  });
  const d = status.data?.data;
  const repoCount = d?.workspace.repos.filter((repo) => repo.exists).length ?? 0;
  const missingRepos = d?.workspace.repos.filter((repo) => !repo.exists) ?? [];
  return (
    <Panel title="DevContext status">
      {status.isLoading ? (
        <span className="text-slate-400 text-sm">checking…</span>
      ) : status.error ? (
        <span className="text-red-600 text-sm">{(status.error as Error).message}</span>
      ) : !d ? (
        <span className="text-slate-400 text-sm">not configured</span>
      ) : (
        <div className="space-y-3 text-sm">
          <div className="grid grid-cols-1 md:grid-cols-4 gap-2 text-xs">
            <div className="rounded border border-slate-200 px-2 py-1">
              <Pill ok={d.workspace.ok} small>
                {d.workspace.ok ? "ok" : "check"}
              </Pill>{" "}
              <code>workspace</code>
              <div className="mt-1 truncate text-slate-500">{d.workspace.root || d.workspace.detail}</div>
              <div className="text-slate-400">
                repos: {repoCount}/{d.workspace.repos.length}
              </div>
            </div>
            <div className="rounded border border-slate-200 px-2 py-1">
              <Pill ok={d.zdev_mcp.configured && d.zdev_mcp.cwd_exists} small>
                {d.zdev_mcp.configured ? "set" : "off"}
              </Pill>{" "}
              <code>zstack-dev-mcp</code>
              <div className="mt-1 truncate text-slate-500">
                {d.zdev_mcp.command} {d.zdev_mcp.entrypoint || ""}
              </div>
              <div className="text-slate-400">
                command {d.zdev_mcp.command_available ? "available" : "missing"} · cwd{" "}
                {d.zdev_mcp.cwd_exists ? "ok" : "missing"}
              </div>
            </div>
            <div className="rounded border border-slate-200 px-2 py-1">
              <Pill ok={d.code_search.repos.length > 0} small>
                {d.code_search.repos.length > 0 ? "on" : "off"}
              </Pill>{" "}
              <code>code_search</code>
              <div className="mt-1 truncate text-slate-500">
                {d.code_search.repos.join(", ") || "no repos"}
              </div>
              <div className="text-slate-400">
                max {d.code_search.max_files} files · {d.code_search.max_matches_per_file} matches
              </div>
            </div>
            <div className="rounded border border-slate-200 px-2 py-1">
              <Pill ok={d.server_logs.configured} small>
                {d.server_logs.configured ? "on" : "off"}
              </Pill>{" "}
              <code>server_logs</code>
              <div className="mt-1 truncate text-slate-500">
                {d.server_logs.servers.map((s) => s.name).join(", ") || "not configured"}
              </div>
              <div className="text-slate-400">read-only SSH log paths</div>
            </div>
          </div>
          {missingRepos.length > 0 && (
            <div className="rounded border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
              Missing workspace repos: {missingRepos.map((repo) => repo.name || repo.path).join(", ")}
            </div>
          )}
          <div
            className={
              "rounded border px-3 py-2 text-xs " +
              (d.security.ok
                ? "border-emerald-200 bg-emerald-50 text-emerald-800"
                : "border-amber-200 bg-amber-50 text-amber-800")
            }
          >
            <div className="mb-1 font-medium">
              Security boundary: {d.security.ok ? "healthy" : "needs attention"}
            </div>
            {(d.security.ok ? d.security.boundary : d.security.findings).slice(0, 5).map((item) => (
              <div key={item}>- {item}</div>
            ))}
          </div>
        </div>
      )}
    </Panel>
  );
}

function TrendsPanel({ projectId }: { projectId: string }) {
  const trends = useQuery({
    queryKey: ["run-trends", projectId],
    queryFn: async (): Promise<TrendsResponse> => {
      const qs = projectId ? `?project_id=${encodeURIComponent(projectId)}` : "";
      const r = await apiFetch(`/api/runs/trends${qs}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    },
    refetchInterval: 15000,
  });
  const d = trends.data?.data;
  const pct = (v: number | null) => (v == null ? "—" : `${Math.round(v * 100)}%`);
  return (
    <Panel title="Run trends">
      {!d ? (
        <span className="text-slate-400 text-sm">…</span>
      ) : (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3 text-sm">
          <Row label="runs" value={String(d.total)} mono />
          <Row label="pass rate" value={pct(d.pass_rate)} mono valueClass="text-emerald-700" />
          <Row label="flaky rate" value={pct(d.flaky_rate)} mono valueClass="text-amber-700" />
          <Row label="avg duration" value={fmtMs(d.avg_duration_ms)} mono />
          <Row
            label="failed"
            value={String((d.by_status.failed ?? 0) + (d.by_status.aborted ?? 0))}
            mono
            valueClass="text-red-700"
          />
        </div>
      )}
    </Panel>
  );
}

function BackendHealth() {
  const health = useQuery({
    queryKey: ["healthz"],
    queryFn: async (): Promise<HealthResponse> => {
      const r = await apiFetch("/healthz");
      if (!r.ok) throw new Error("backend down");
      return r.json();
    },
    refetchInterval: 5000,
  });
  const runner = useLLMRunnerStatus();

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
          <LLMRunnerStatusLight data={runner.data} loading={runner.isLoading} />
        </div>
      )}
    </Panel>
  );
}

function CasesWidget({ projectId }: { projectId: string }) {
  const cases = useQuery({
    queryKey: ["cases-summary", projectId],
    enabled: Boolean(projectId),
    queryFn: async (): Promise<CasesResponse> => {
      const r = await apiFetch(`/api/cases/?limit=200&project_id=${encodeURIComponent(projectId)}`);
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

function RecentRunsWidget({ projectId }: { projectId: string }) {
  const runs = useQuery({
    queryKey: ["runs-recent", projectId],
    enabled: Boolean(projectId),
    queryFn: async (): Promise<RunsResponse> => {
      const r = await apiFetch(`/api/runs/?limit=50&project_id=${encodeURIComponent(projectId)}`);
      return r.json();
    },
    refetchInterval: 3000,
  });
  const runRows = asArray<RunsResponse["data"][number]>(runs.data?.data);
  const latestByCase = runRows.filter(
    (r, index, arr) => arr.findIndex((candidate) => candidate.case_id === r.case_id) === index,
  );

  return (
    <Panel title="Recent runs" linkTo="/runs" linkLabel="all →">
      {runs.isLoading ? (
        <span className="text-slate-400 text-sm">…</span>
      ) : (runs.data?.count ?? 0) === 0 ? (
        <Empty cta="run an approved case" to="/cases" />
      ) : (
        <ul className="space-y-1.5 text-sm">
          {latestByCase?.slice(0, 5).map((r) => (
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

function RegressionAssetsWidget({ projectId }: { projectId: string }) {
  const qc = useQueryClient();
  const [editingAssetId, setEditingAssetId] = useState<string | null>(null);
  const assets = useQuery({
    queryKey: ["regression-assets", projectId],
    enabled: Boolean(projectId),
    queryFn: async (): Promise<RegressionAssetsResponse> => {
      const r = await apiFetch(
        `/api/regression-assets/?project_id=${encodeURIComponent(projectId)}`,
      );
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    },
    refetchInterval: 10000,
  });
  const runs = useQuery({
    queryKey: ["asset-source-runs", projectId],
    enabled: Boolean(projectId),
    queryFn: async (): Promise<RunsResponse> => {
      const r = await apiFetch(`/api/runs/?limit=100&project_id=${encodeURIComponent(projectId)}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    },
    refetchInterval: 10000,
  });
  const assetSourceRunIds = new Set((assets.data?.data ?? []).map((asset) => asset.source_run_id));
  const eligibleRuns = (runs.data?.data ?? [])
    .filter((run) => run.status === "passed" && !assetSourceRunIds.has(run.run_id))
    .slice(0, 5);
  const approve = useMutation({
    mutationFn: async (assetId: string) => {
      const r = await apiFetch(`/api/regression-assets/${assetId}/approve`, { method: "POST" });
      if (!r.ok) throw new Error(await r.text());
      return r.json();
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["regression-assets", projectId] }),
  });
  const replay = useMutation({
    mutationFn: async (assetId: string) => {
      const r = await apiFetch(`/api/regression-assets/${assetId}/replay`, { method: "POST" });
      if (!r.ok) throw new Error(await r.text());
      return r.json();
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["regression-assets", projectId] });
      qc.invalidateQueries({ queryKey: ["runs-recent", projectId] });
    },
  });
  const extract = useMutation({
    mutationFn: async (runId: string) => {
      const r = await apiFetch(`/api/regression-assets/from-run/${runId}`, { method: "POST" });
      if (!r.ok) throw new Error(await r.text());
      return r.json();
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["regression-assets", projectId] }),
  });
  const repair = useMutation({
    mutationFn: async ({
      assetId,
      body,
    }: {
      assetId: string;
      body: {
        status: string;
        action_plan: Array<Record<string, unknown>>;
        locator_candidates: Array<Record<string, unknown>>;
        assertions: Array<Record<string, unknown>>;
      };
    }) => {
      const r = await apiFetch(`/api/regression-assets/${assetId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error(await r.text());
      return r.json();
    },
    onSuccess: () => {
      setEditingAssetId(null);
      qc.invalidateQueries({ queryKey: ["regression-assets", projectId] });
    },
  });
  const assetRows = assets.data?.data ?? [];

  return (
    <Panel title="Regression assets">
      {assets.isLoading ? (
        <span className="text-slate-400 text-sm">…</span>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          <div className="space-y-2">
            {assetRows.slice(0, 8).map((asset) => (
              <div key={asset.asset_id} className="border-b border-slate-100 pb-2 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <code className="text-xs text-blue-700">{asset.asset_id.slice(0, 12)}</code>
                  <span className="text-xs text-slate-500">{asset.case_id}</span>
                  <StatusPill status={asset.status} small />
                  {asset.last_status && <StatusPill status={asset.last_status} small />}
                  {asset.last_replay_run_id && (
                    <Link
                      to="/runs/$id"
                      params={{ id: asset.last_replay_run_id }}
                      className="text-xs text-blue-700 hover:underline"
                    >
                      replay run
                    </Link>
                  )}
                  <button
                    disabled={asset.status !== "draft" || approve.isPending}
                    onClick={() => approve.mutate(asset.asset_id)}
                    className="ml-auto rounded border border-emerald-200 px-2 py-1 text-xs text-emerald-700 disabled:opacity-50"
                  >
                    approve
                  </button>
                  <button
                    disabled={asset.status !== "approved" || replay.isPending}
                    onClick={() => replay.mutate(asset.asset_id)}
                    className="rounded bg-slate-900 px-2 py-1 text-xs text-white disabled:opacity-50"
                  >
                    replay
                  </button>
                  <button
                    onClick={() =>
                      setEditingAssetId((current) =>
                        current === asset.asset_id ? null : asset.asset_id,
                      )
                    }
                    className="rounded border border-slate-200 px-2 py-1 text-xs text-slate-700"
                  >
                    repair
                  </button>
                </div>
                {editingAssetId === asset.asset_id && (
                  <AssetRepairEditor
                    asset={asset}
                    isSaving={repair.isPending}
                    onCancel={() => setEditingAssetId(null)}
                    onSave={(body) => repair.mutate({ assetId: asset.asset_id, body })}
                  />
                )}
              </div>
            ))}
            {!assetRows.length && (
              <div className="text-sm text-slate-500">No regression assets yet.</div>
            )}
          </div>
          <div>
            <div className="mb-2 text-xs uppercase tracking-wide text-slate-400">
              Passed runs ready to extract
            </div>
            <div className="space-y-2">
              {eligibleRuns.map((run) => (
                <div key={run.run_id} className="flex items-center gap-2 text-sm">
                  <Link
                    to="/runs/$id"
                    params={{ id: run.run_id }}
                    className="font-mono text-xs text-blue-700 hover:underline"
                  >
                    {run.run_id.slice(0, 8)}
                  </Link>
                  <span className="text-xs text-slate-500">{run.case_id}</span>
                  <button
                    disabled={extract.isPending}
                    onClick={() => extract.mutate(run.run_id)}
                    className="ml-auto rounded border border-slate-200 px-2 py-1 text-xs text-slate-700 disabled:opacity-50"
                  >
                    extract
                  </button>
                </div>
              ))}
              {!eligibleRuns.length && (
                <div className="text-sm text-slate-500">No new passed runs.</div>
              )}
            </div>
          </div>
        </div>
      )}
      {(approve.error || replay.error || extract.error) && (
        <pre className="mt-2 whitespace-pre-wrap text-xs text-red-600">
          {((approve.error || replay.error || extract.error) as Error).message}
        </pre>
      )}
      {repair.error && (
        <pre className="mt-2 whitespace-pre-wrap text-xs text-red-600">
          {(repair.error as Error).message}
        </pre>
      )}
    </Panel>
  );
}

type RegressionAssetRow = RegressionAssetsResponse["data"][number];

function AssetRepairEditor({
  asset,
  isSaving,
  onCancel,
  onSave,
}: {
  asset: RegressionAssetRow;
  isSaving: boolean;
  onCancel: () => void;
  onSave: (body: {
    status: string;
    action_plan: Array<Record<string, unknown>>;
    locator_candidates: Array<Record<string, unknown>>;
    assertions: Array<Record<string, unknown>>;
  }) => void;
}) {
  const [status, setStatus] = useState(asset.status === "needs_repair" ? "draft" : asset.status);
  const [actionPlan, setActionPlan] = useState(formatJson(asset.action_plan));
  const [locators, setLocators] = useState(formatJson(asset.locator_candidates));
  const [assertions, setAssertions] = useState(formatJson(asset.assertions));
  const [error, setError] = useState("");

  function parseJsonArray(label: string, raw: string): Array<Record<string, unknown>> {
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) throw new Error(`${label} must be a JSON array`);
    return parsed.map((item, index) => {
      if (!item || typeof item !== "object" || Array.isArray(item)) {
        throw new Error(`${label}[${index}] must be an object`);
      }
      return item as Record<string, unknown>;
    });
  }

  return (
    <div className="mt-3 rounded border border-slate-200 bg-slate-50 p-3">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <label className="text-xs font-medium text-slate-600">
          Status
          <select
            value={status}
            onChange={(event) => setStatus(event.target.value)}
            className="ml-2 rounded border border-slate-200 bg-white px-2 py-1 text-xs"
          >
            <option value="draft">draft</option>
            <option value="approved">approved</option>
            <option value="needs_repair">needs_repair</option>
            <option value="retired">retired</option>
          </select>
        </label>
        <button
          disabled={isSaving}
          onClick={() => {
            try {
              setError("");
              onSave({
                status,
                action_plan: parseJsonArray("action_plan", actionPlan),
                locator_candidates: parseJsonArray("locator_candidates", locators),
                assertions: parseJsonArray("assertions", assertions),
              });
            } catch (err) {
              setError((err as Error).message);
            }
          }}
          className="ml-auto rounded bg-slate-900 px-2 py-1 text-xs text-white disabled:opacity-50"
        >
          save repair
        </button>
        <button
          disabled={isSaving}
          onClick={onCancel}
          className="rounded border border-slate-200 bg-white px-2 py-1 text-xs text-slate-700 disabled:opacity-50"
        >
          cancel
        </button>
      </div>
      <div className="grid gap-2">
        <JsonField label="action_plan" value={actionPlan} onChange={setActionPlan} rows={8} />
        <JsonField label="locator_candidates" value={locators} onChange={setLocators} rows={4} />
        <JsonField label="assertions" value={assertions} onChange={setAssertions} rows={4} />
      </div>
      {error && <div className="mt-2 text-xs text-red-600">{error}</div>}
    </div>
  );
}

function JsonField({
  label,
  value,
  onChange,
  rows,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  rows: number;
}) {
  return (
    <label className="block text-xs font-medium text-slate-600">
      {label}
      <textarea
        value={value}
        onChange={(event) => onChange(event.target.value)}
        rows={rows}
        spellCheck={false}
        className="mt-1 w-full rounded border border-slate-200 bg-white p-2 font-mono text-xs text-slate-800"
      />
    </label>
  );
}

function formatJson(value: unknown): string {
  return JSON.stringify(value ?? [], null, 2);
}

function PRDsWidget({ projectId }: { projectId: string }) {
  const prds = useQuery({
    queryKey: ["prds-recent", projectId],
    enabled: Boolean(projectId),
    queryFn: async (): Promise<PRDsResponse> => {
      const r = await apiFetch(`/api/prd/?project_id=${encodeURIComponent(projectId)}`);
      return r.json();
    },
    refetchInterval: 30000,
  });
  const prdRows = asArray<PRDsResponse["data"][number]>(prds.data?.data);

  return (
    <Panel title="Recent PRDs" linkTo="/prd" linkLabel="upload →">
      {prds.isLoading ? (
        <span className="text-slate-400 text-sm">…</span>
      ) : prdRows.length === 0 ? (
        <Empty cta="upload first PRD" to="/prd" />
      ) : (
        <ul className="space-y-1.5 text-sm">
          {prdRows.slice(0, 4).map((p) => (
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
      const r = await apiFetch("/api/llm/health");
      return r.json();
    },
    refetchInterval: 30000,
  });

  return (
    <Panel title="LLM provider status">
      {llm.isLoading ? (
        <span className="text-slate-400 text-sm">…</span>
      ) : (
        <div className="space-y-1 text-xs">
          {Object.entries(llm.data?.data ?? {})
            .sort(([, a], [, b]) => a.priority - b.priority)
            .map(([name, p]) => (
              <div key={name} className="flex justify-between gap-3">
                <span>
                  <code>{name}</code>{" "}
                  <span className="text-slate-400">prio {p.priority}</span>
                </span>
                <span
                  className={p.available ? "text-emerald-600" : "text-slate-400"}
                  title={p.detail}
                >
                  {p.available ? "available" : p.detail || "off"}
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
  const [all, setAll] = useState<ProviderProbeResult[]>([]);

  const probe = useMutation({
    mutationFn: async (): Promise<ProbeResult> => {
      const r = await apiFetch("/api/llm/probe", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prefer: prefer || null }),
      });
      const body = await r.json();
      return body.data as ProbeResult;
    },
    onSuccess: setLast,
  });
  const probeAll = useMutation({
    mutationFn: async (): Promise<ProviderProbeResult[]> => {
      const r = await apiFetch("/api/llm/probe_all", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      const body = await r.json();
      return body.data as ProviderProbeResult[];
    },
    onSuccess: setAll,
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
        </select>
        <button
          className="bg-slate-900 text-white px-2 py-0.5 rounded hover:bg-slate-700 disabled:opacity-50"
          disabled={probe.isPending}
          onClick={() => probe.mutate()}
        >
          {probe.isPending ? "…" : "probe"}
        </button>
        <button
          className="border border-slate-200 px-2 py-0.5 rounded hover:bg-slate-50 disabled:opacity-50"
          disabled={probeAll.isPending}
          onClick={() => probeAll.mutate()}
        >
          {probeAll.isPending ? "…" : "probe all"}
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
      {all.length > 0 && (
        <div className="mt-2 text-xs space-y-1">
          {all.map((row) => (
            <div key={row.provider} className="flex items-center justify-between rounded bg-slate-50 px-2 py-1">
              <span>
                <Pill ok={row.ok} small>
                  {row.ok ? "ok" : "err"}
                </Pill>{" "}
                <code>{row.provider}</code>
              </span>
              <code className={row.ok ? "text-slate-500" : "text-red-700"}>
                {row.ok ? `${row.model} · ${row.latency_ms}ms` : `${row.error_type}: ${row.error}`}
              </code>
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}

interface ProjectConfig {
  project_id: string;
  name: string;
  base_url: string;
  login_url: string;
  default_username: string;
  default_password: string;
  default_password_is_set?: boolean;
  description?: string;
}

/** Show what test runs will actually target / authenticate as for the
 * currently-selected project. Without this, the user fills in
 * base_url/user/password in the create-project form and then has no way
 * to see them again — the dropdown only shows the name, and the values
 * sit invisible inside `runs` and `cases` requests. */
function CurrentProjectPanel({ projectId }: { projectId: string }) {
  const [showPwd, setShowPwd] = useState(false);
  const [editing, setEditing] = useState(false);
  const qc = useQueryClient();
  const projects = useQuery({
    queryKey: ["projects"],
    queryFn: async (): Promise<{ data: ProjectConfig[] }> => {
      const r = await apiFetch("/api/projects/");
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    },
  });
  const projectRows = asArray<ProjectConfig>(projects.data?.data);
  const proj = projectRows.find((p) => p.project_id === projectId);
  if (!proj) return null;

  const hasCreds = Boolean(proj.default_username && proj.default_password_is_set);
  const hasLoginUrl = Boolean(proj.login_url);

  if (editing) {
    return (
      <div className="bg-white border border-slate-200 rounded-lg p-4">
        <div className="flex items-center justify-between mb-2">
          <span className="text-xs uppercase tracking-wide text-slate-400">
            Edit project / {proj.project_id}
          </span>
          <button
            onClick={() => setEditing(false)}
            className="text-xs text-slate-500 hover:text-slate-900"
          >
            cancel
          </button>
        </div>
        <ProjectInlineEditForm
          initial={proj}
          onSaved={() => {
            setEditing(false);
            qc.invalidateQueries({ queryKey: ["projects"] });
          }}
        />
      </div>
    );
  }

  return (
    <Panel
      title={`Project config / ${proj.project_id}`}
      onEdit={() => setEditing(true)}
    >
      <div className="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-1 text-sm">
        <Row label="name" value={<code>{proj.name}</code>} />
        <Row
          label="base_url"
          value={
            proj.base_url ? (
              <a
                href={proj.base_url}
                target="_blank"
                rel="noreferrer"
                className="text-blue-700 hover:underline font-mono text-xs break-all"
              >
                {proj.base_url}
              </a>
            ) : (
              <span className="text-amber-700 text-xs">
                (not set — runs will fail without a target)
              </span>
            )
          }
        />
        <Row
          label="login_url"
          value={
            proj.login_url ? (
              <a
                href={proj.login_url}
                target="_blank"
                rel="noreferrer"
                className="text-blue-700 hover:underline font-mono text-xs break-all"
              >
                {proj.login_url}
              </a>
            ) : (
              <span className="text-amber-700 text-xs">
                (not set — protected cases may need to discover login)
              </span>
            )
          }
        />
        <Row
          label="default_username"
          value={
            proj.default_username ? (
              <code className="text-xs">{proj.default_username}</code>
            ) : (
              <span className="text-slate-400 text-xs">(none)</span>
            )
          }
        />
        <Row
          label="default_password"
          value={
            proj.default_password_is_set ? (
              <span className="flex items-center gap-2">
                <code className="text-xs">
                  {showPwd ? "(stored, not shown)" : "••••••••••••"}
                </code>
                <button
                  onClick={() => setShowPwd((v) => !v)}
                  className="text-xs text-slate-500 hover:text-slate-900"
                >
                  {showPwd ? "hide" : "reveal status"}
                </button>
              </span>
            ) : (
              <span className="text-slate-400 text-xs">(none)</span>
            )
          }
        />
      </div>
      <div className="mt-2 text-xs">
        {hasCreds && hasLoginUrl ? (
          <span className="text-emerald-700">
            ✓ Protected cases use this login URL and credentials at runtime.
          </span>
        ) : hasCreds ? (
          <span className="text-amber-700">
            ⚠ Credentials are configured, but login_url is missing — cases can use
            credentials only after a login form is reachable.
          </span>
        ) : (
          <span className="text-amber-700">
            ⚠ No credentials configured — cases targeting protected pages will need
            explicit login steps in the case body.
          </span>
        )}
      </div>
    </Panel>
  );
}

/** Tiny edit form for the dashboard panel. Mirrors the header's
 * ProjectSwitcher edit form but lives in the page so the user doesn't
 * have to chase the ✎ icon. */
function ProjectInlineEditForm({
  initial,
  onSaved,
}: {
  initial: ProjectConfig;
  onSaved: () => void;
}) {
  const [name, setName] = useState(initial.name);
  const [baseUrl, setBaseUrl] = useState(initial.base_url);
  const [loginUrl, setLoginUrl] = useState(initial.login_url);
  const [username, setUsername] = useState(initial.default_username);
  const [password, setPassword] = useState("");
  const [showPwd, setShowPwd] = useState(false);

  const save = useMutation({
    mutationFn: async () => {
      const r = await apiFetch("/api/projects/", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          project_id: initial.project_id,
          name: name.trim(),
          base_url: baseUrl.trim(),
          login_url: loginUrl.trim(),
          default_username: username.trim(),
          ...(password ? { default_password: password } : {}),
        }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`);
      return r.json();
    },
    onSuccess: onSaved,
  });

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-sm">
      <label className="block">
        <span className="text-xs text-slate-500">name *</span>
        <input
          className="border border-slate-200 rounded px-2 py-1 w-full"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
      </label>
      <label className="block">
        <span className="text-xs text-slate-500">base_url *</span>
        <input
          className="border border-slate-200 rounded px-2 py-1 w-full font-mono"
          value={baseUrl}
          onChange={(e) => setBaseUrl(e.target.value)}
          placeholder="http://localhost:5000/"
        />
      </label>
      <label className="block">
        <span className="text-xs text-slate-500">login_url</span>
        <input
          className="border border-slate-200 rounded px-2 py-1 w-full font-mono"
          value={loginUrl}
          onChange={(e) => setLoginUrl(e.target.value)}
          placeholder="http://localhost:5000/login"
        />
      </label>
      <label className="block">
        <span className="text-xs text-slate-500">default_username</span>
        <input
          className="border border-slate-200 rounded px-2 py-1 w-full font-mono"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          placeholder="admin@example.com"
        />
      </label>
      <label className="block">
        <span className="text-xs text-slate-500">default_password</span>
        <div className="flex items-center gap-2">
          <input
            type={showPwd ? "text" : "password"}
            className="border border-slate-200 rounded px-2 py-1 flex-1 font-mono"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder={initial.default_password_is_set ? "stored; leave blank to keep" : ""}
        />
          <button
            type="button"
            onClick={() => setShowPwd((v) => !v)}
            className="text-xs text-slate-500 hover:text-slate-900"
          >
            {showPwd ? "hide" : "show"}
          </button>
        </div>
      </label>
      <div className="md:col-span-2 flex items-center gap-2">
        <button
          disabled={!name.trim() || !baseUrl.trim() || save.isPending}
          onClick={() => save.mutate()}
          className="bg-slate-900 text-white text-sm px-3 py-1 rounded hover:bg-slate-700 disabled:opacity-50"
        >
          {save.isPending ? "saving…" : "save"}
        </button>
        {save.error && (
          <span className="text-red-600 text-xs">{(save.error as Error).message}</span>
        )}
      </div>
    </div>
  );
}

interface RuntimeKnob<T = number | boolean | string> {
  value: T;
  default: T;
  min?: number;
  max?: number;
  choices?: string[];
  describe: string;
  is_set?: boolean;
}
interface RuntimeSettingsResponse {
  data: {
    max_concurrent_runs: RuntimeKnob<number>;
    headless: RuntimeKnob<boolean>;
    executor_loop: RuntimeKnob<"auto" | "generic_openai" | "claude_cli">;
    test_design_provider: RuntimeKnob<string>;
    case_drafting_provider: RuntimeKnob<string>;
    case_execution_provider: RuntimeKnob<string>;
    diagnosis_provider: RuntimeKnob<string>;
    email_enabled: RuntimeKnob<boolean>;
    email_on_run_completed: RuntimeKnob<boolean>;
    email_on_diagnosis_generated: RuntimeKnob<boolean>;
    smtp_host: RuntimeKnob<string>;
    smtp_port: RuntimeKnob<number>;
    smtp_username: RuntimeKnob<string>;
    smtp_password: RuntimeKnob<string>;
    smtp_from: RuntimeKnob<string>;
    smtp_to: RuntimeKnob<string>;
    smtp_use_tls: RuntimeKnob<boolean>;
    smtp_use_ssl: RuntimeKnob<boolean>;
    email_subject_prefix: RuntimeKnob<string>;
    webhook_enabled: RuntimeKnob<boolean>;
    webhook_url: RuntimeKnob<string>;
    webhook_kind: RuntimeKnob<"generic" | "feishu" | "wecom">;
    artifact_retention_days: RuntimeKnob<number>;
    michelle_workspace_root: RuntimeKnob<string>;
    michelle_zdev_mcp_command: RuntimeKnob<string>;
    michelle_zdev_mcp_args: RuntimeKnob<string>;
    michelle_zdev_mcp_cwd: RuntimeKnob<string>;
    michelle_zdev_mcp_timeout_seconds: RuntimeKnob<number>;
    michelle_dev_context_repos: RuntimeKnob<string>;
    michelle_dev_context_max_files: RuntimeKnob<number>;
    michelle_dev_context_max_matches_per_file: RuntimeKnob<number>;
    michelle_server_logs_json: RuntimeKnob<string>;
  };
}

interface ArtifactCleanupResponse {
  data: {
    retention_days: number;
    dry_run: boolean;
    cutoff: string;
    candidate_runs: number;
    candidate_bytes: number;
    deleted_runs: number;
    deleted_bytes: number;
    errors: string[];
    candidates: Array<{
      run_id: string;
      project_id: string;
      status: string;
      path: string;
      bytes: number;
      files: number;
    }>;
  };
}

interface DevContextProbeResponse {
  data: {
    ok: boolean;
    detail: string;
    configured?: boolean;
    tools?: string[];
    snippets?: number;
    elapsed_ms?: number;
  };
}

/** Live-tunable platform knobs. Currently just `max_concurrent_runs`,
 * exposed as a number input + Save button. The new value affects runs
 * launched after save; in-flight runs keep their original semaphore slot. */
function RuntimeSettingsPanel() {
  const qc = useQueryClient();
  const settings = useQuery({
    queryKey: ["runtime-settings"],
    queryFn: async (): Promise<RuntimeSettingsResponse> => {
      const r = await apiFetch("/api/settings/runtime");
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    },
  });

  const concurrencyKnob = settings.data?.data.max_concurrent_runs;
  const headlessKnob = settings.data?.data.headless;
  const testDesignProviderKnob = settings.data?.data.test_design_provider;
  const caseDraftingProviderKnob = settings.data?.data.case_drafting_provider;
  const caseExecutionProviderKnob = settings.data?.data.case_execution_provider;
  const diagnosisProviderKnob = settings.data?.data.diagnosis_provider;
  const emailKnobs = settings.data?.data;
  const artifactKnob = settings.data?.data.artifact_retention_days;
  const devContextKnobs = settings.data?.data;
  const [draft, setDraft] = useState<number | null>(null);
  const [artifactDraft, setArtifactDraft] = useState<number | null>(null);
  const [emailDraft, setEmailDraft] = useState<Record<string, string | number | boolean>>({});
  const [devContextDraft, setDevContextDraft] = useState<Record<string, string | number>>({});
  const value = draft ?? concurrencyKnob?.value ?? 2;
  const artifactRetention = artifactDraft ?? artifactKnob?.value ?? 30;

  const save = useMutation({
    mutationFn: async (body: Record<string, number | boolean | string>) => {
      const r = await apiFetch("/api/settings/runtime", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error(await r.text());
      return r.json();
    },
    onSuccess: () => {
      setDraft(null);
      setArtifactDraft(null);
      qc.invalidateQueries({ queryKey: ["runtime-settings"] });
      qc.invalidateQueries({ queryKey: ["llm-runner-status"] });
    },
  });
  const testEmail = useMutation({
    mutationFn: async () => {
      const r = await apiFetch("/api/settings/email/test", { method: "POST" });
      if (!r.ok) throw new Error(await r.text());
      return r.json();
    },
  });

  const emailValue = <T extends string | number | boolean>(key: string, fallback: T): T =>
    (emailDraft[key] as T | undefined) ?? fallback;

  const setEmailValue = (key: string, value: string | number | boolean) =>
    setEmailDraft((prev) => ({ ...prev, [key]: value }));
  const devContextValue = <T extends string | number>(key: string, fallback: T): T =>
    (devContextDraft[key] as T | undefined) ?? fallback;
  const setDevContextValue = (key: string, value: string | number) =>
    setDevContextDraft((prev) => ({ ...prev, [key]: value }));

  const saveEmail = () => {
    if (!emailKnobs) return;
    const body: Record<string, string | number | boolean> = {
      email_enabled: emailValue("email_enabled", emailKnobs.email_enabled.value),
      email_on_run_completed: emailValue(
        "email_on_run_completed",
        emailKnobs.email_on_run_completed.value,
      ),
      email_on_diagnosis_generated: emailValue(
        "email_on_diagnosis_generated",
        emailKnobs.email_on_diagnosis_generated.value,
      ),
      smtp_host: emailValue("smtp_host", emailKnobs.smtp_host.value),
      smtp_port: emailValue("smtp_port", emailKnobs.smtp_port.value),
      smtp_username: emailValue("smtp_username", emailKnobs.smtp_username.value),
      smtp_from: emailValue("smtp_from", emailKnobs.smtp_from.value),
      smtp_to: emailValue("smtp_to", emailKnobs.smtp_to.value),
      smtp_use_tls: emailValue("smtp_use_tls", emailKnobs.smtp_use_tls.value),
      smtp_use_ssl: emailValue("smtp_use_ssl", emailKnobs.smtp_use_ssl.value),
      email_subject_prefix: emailValue(
        "email_subject_prefix",
        emailKnobs.email_subject_prefix.value,
      ),
      webhook_enabled: emailValue("webhook_enabled", emailKnobs.webhook_enabled.value),
      webhook_kind: emailValue("webhook_kind", emailKnobs.webhook_kind.value),
    };
    const password = String(emailDraft.smtp_password ?? "");
    if (password) body.smtp_password = password;
    const webhookUrl = String(emailDraft.webhook_url ?? "");
    if (webhookUrl) body.webhook_url = webhookUrl;
    save.mutate(body, { onSuccess: () => setEmailDraft({}) });
  };
  const testWebhook = useMutation({
    mutationFn: async () => {
      const r = await apiFetch("/api/settings/webhook/test", { method: "POST" });
      if (!r.ok) throw new Error(await r.text());
      return r.json();
    },
  });
  const cleanupArtifacts = useMutation({
    mutationFn: async (dryRun: boolean): Promise<ArtifactCleanupResponse> => {
      const r = await apiFetch("/api/settings/artifacts/cleanup", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ retention_days: artifactRetention, dry_run: dryRun }),
      });
      if (!r.ok) throw new Error(await r.text());
      return r.json();
    },
  });
  const probeDevContextMcp = useMutation({
    mutationFn: async (): Promise<DevContextProbeResponse> => {
      const r = await apiFetch("/api/dev-context/probe/mcp", { method: "POST" });
      if (!r.ok) throw new Error(await r.text());
      return r.json();
    },
  });
  const probeServerLogs = useMutation({
    mutationFn: async (): Promise<DevContextProbeResponse> => {
      const r = await apiFetch("/api/dev-context/probe/server-logs", { method: "POST" });
      if (!r.ok) throw new Error(await r.text());
      return r.json();
    },
  });
  const saveDevContext = () => {
    if (!devContextKnobs) return;
    save.mutate(
      {
        michelle_workspace_root: devContextValue(
          "michelle_workspace_root",
          devContextKnobs.michelle_workspace_root.value,
        ),
        michelle_zdev_mcp_command: devContextValue(
          "michelle_zdev_mcp_command",
          devContextKnobs.michelle_zdev_mcp_command.value,
        ),
        michelle_zdev_mcp_args: devContextValue(
          "michelle_zdev_mcp_args",
          devContextKnobs.michelle_zdev_mcp_args.value,
        ),
        michelle_zdev_mcp_cwd: devContextValue(
          "michelle_zdev_mcp_cwd",
          devContextKnobs.michelle_zdev_mcp_cwd.value,
        ),
        michelle_zdev_mcp_timeout_seconds: devContextValue(
          "michelle_zdev_mcp_timeout_seconds",
          devContextKnobs.michelle_zdev_mcp_timeout_seconds.value,
        ),
        michelle_dev_context_repos: devContextValue(
          "michelle_dev_context_repos",
          devContextKnobs.michelle_dev_context_repos.value,
        ),
        michelle_dev_context_max_files: devContextValue(
          "michelle_dev_context_max_files",
          devContextKnobs.michelle_dev_context_max_files.value,
        ),
        michelle_dev_context_max_matches_per_file: devContextValue(
          "michelle_dev_context_max_matches_per_file",
          devContextKnobs.michelle_dev_context_max_matches_per_file.value,
        ),
        michelle_server_logs_json: devContextValue(
          "michelle_server_logs_json",
          devContextKnobs.michelle_server_logs_json.value,
        ),
      },
      {
        onSuccess: () => {
          setDevContextDraft({});
          qc.invalidateQueries({ queryKey: ["dev-context-status"] });
        },
      },
    );
  };

  return (
    <Panel title="Platform settings">
      {settings.isLoading ||
      !concurrencyKnob ||
      !headlessKnob ||
      !testDesignProviderKnob ||
      !caseDraftingProviderKnob ||
      !caseExecutionProviderKnob ||
      !diagnosisProviderKnob ||
      !artifactKnob ? (
        <span className="text-slate-400 text-sm">…</span>
      ) : (
        <div className="space-y-3 text-sm">
          {/* model routing */}
          <div className="rounded border border-slate-200 bg-slate-50 px-3 py-2">
            <div className="mb-2 flex flex-wrap items-center gap-2">
              <code className="text-xs text-slate-500">model_routing</code>
              <span className="text-xs text-slate-400">
                design / drafting / execution / diagnosis
              </span>
            </div>
            <div className="grid gap-2 md:grid-cols-4">
              <ProviderSelect
                label="1. Analyze coverage"
                knob={testDesignProviderKnob}
                disabled={save.isPending}
                onChange={(value) => save.mutate({ test_design_provider: value })}
              />
              <ProviderSelect
                label="2. Draft cases"
                knob={caseDraftingProviderKnob}
                disabled={save.isPending}
                onChange={(value) => save.mutate({ case_drafting_provider: value })}
              />
              <ProviderSelect
                label="3. Execute cases"
                knob={caseExecutionProviderKnob}
                disabled={save.isPending}
                onChange={(value) => save.mutate({ case_execution_provider: value })}
              />
              <ProviderSelect
                label="4. Diagnose failures"
                knob={diagnosisProviderKnob}
                disabled={save.isPending}
                onChange={(value) => save.mutate({ diagnosis_provider: value })}
              />
            </div>
            <p className="mt-2 text-xs text-slate-600">
              Cost/reliability below is only telemetry. These three fields are the
              actual model routing controls. Michelle owns the Playwright action loop
              directly unless Execute cases is <code>claude-cli</code>, which switches
              to the legacy Claude CLI browser loop.
            </p>
          </div>
          {/* concurrency */}
          <div>
            <div className="flex items-center gap-2 mb-1">
              <code className="text-xs text-slate-500">max_concurrent_runs</code>
              <input
                type="number"
                min={concurrencyKnob.min}
                max={concurrencyKnob.max}
                value={value}
                onChange={(e) =>
                  setDraft(parseInt(e.target.value, 10) || concurrencyKnob.value)
                }
                className="border border-slate-200 rounded px-2 py-0.5 w-20 text-sm font-mono"
              />
              <button
                disabled={save.isPending || value === concurrencyKnob.value}
                onClick={() => save.mutate({ max_concurrent_runs: value })}
                className="text-xs bg-slate-900 text-white px-2 py-0.5 rounded hover:bg-slate-700 disabled:opacity-50"
              >
                {save.isPending ? "saving…" : "save"}
              </button>
              {draft != null && draft !== concurrencyKnob.value && (
                <button
                  onClick={() => setDraft(null)}
                  className="text-xs text-slate-500 hover:text-slate-900"
                >
                  reset
                </button>
              )}
              <span className="text-xs text-slate-400">
                default: <code>{String(concurrencyKnob.default)}</code>
              </span>
            </div>
            <p className="text-xs text-slate-500">{concurrencyKnob.describe}</p>
          </div>
          {/* headless */}
          <div>
            <div className="flex items-center gap-2 mb-1">
              <code className="text-xs text-slate-500">headless</code>
              <label className="inline-flex items-center gap-1 cursor-pointer">
                <input
                  type="checkbox"
                  checked={headlessKnob.value}
                  onChange={(e) => save.mutate({ headless: e.target.checked })}
                  disabled={save.isPending}
                />
                <span className="text-xs">
                  {headlessKnob.value ? "headless (no window)" : "show browser"}
                </span>
              </label>
              <span className="text-xs text-slate-400">
                default: <code>{String(headlessKnob.default)}</code>
              </span>
            </div>
            <p className="text-xs text-slate-500">{headlessKnob.describe}</p>
          </div>
          {save.error && (
            <span className="text-red-600 text-xs">{(save.error as Error).message}</span>
          )}
          {emailKnobs && (
            <div className="rounded border border-slate-200 bg-slate-50 px-3 py-2">
              <div className="flex flex-wrap items-center gap-3 mb-2">
                <code className="text-xs text-slate-500">email_notifications</code>
                <label className="inline-flex items-center gap-1">
                  <input
                    type="checkbox"
                    checked={emailValue("email_enabled", emailKnobs.email_enabled.value)}
                    onChange={(e) => setEmailValue("email_enabled", e.target.checked)}
                  />
                  <span className="text-xs">enabled</span>
                </label>
                <label className="inline-flex items-center gap-1">
                  <input
                    type="checkbox"
                    checked={emailValue(
                      "email_on_run_completed",
                      emailKnobs.email_on_run_completed.value,
                    )}
                    onChange={(e) =>
                      setEmailValue("email_on_run_completed", e.target.checked)
                    }
                  />
                  <span className="text-xs">run completed</span>
                </label>
                <label className="inline-flex items-center gap-1">
                  <input
                    type="checkbox"
                    checked={emailValue(
                      "email_on_diagnosis_generated",
                      emailKnobs.email_on_diagnosis_generated.value,
                    )}
                    onChange={(e) =>
                      setEmailValue("email_on_diagnosis_generated", e.target.checked)
                    }
                  />
                  <span className="text-xs">diagnosis done</span>
                </label>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                <EmailInput
                  label="SMTP host"
                  value={emailValue("smtp_host", emailKnobs.smtp_host.value)}
                  onChange={(v) => setEmailValue("smtp_host", v)}
                />
                <EmailInput
                  label="SMTP port"
                  type="number"
                  value={String(emailValue("smtp_port", emailKnobs.smtp_port.value))}
                  onChange={(v) => setEmailValue("smtp_port", parseInt(v, 10) || 587)}
                />
                <EmailInput
                  label="Username"
                  value={emailValue("smtp_username", emailKnobs.smtp_username.value)}
                  onChange={(v) => setEmailValue("smtp_username", v)}
                />
                <EmailInput
                  label="Password"
                  type="password"
                  value={String(emailDraft.smtp_password ?? "")}
                  placeholder={emailKnobs.smtp_password.is_set ? "stored; leave blank to keep" : ""}
                  onChange={(v) => setEmailValue("smtp_password", v)}
                />
                <EmailInput
                  label="From"
                  value={emailValue("smtp_from", emailKnobs.smtp_from.value)}
                  onChange={(v) => setEmailValue("smtp_from", v)}
                />
                <EmailInput
                  label="To"
                  value={emailValue("smtp_to", emailKnobs.smtp_to.value)}
                  onChange={(v) => setEmailValue("smtp_to", v)}
                />
                <EmailInput
                  label="Subject prefix"
                  value={emailValue(
                    "email_subject_prefix",
                    emailKnobs.email_subject_prefix.value,
                  )}
                  onChange={(v) => setEmailValue("email_subject_prefix", v)}
                />
                <EmailInput
                  label="Webhook URL"
                  type="password"
                  value={String(emailDraft.webhook_url ?? "")}
                  placeholder={emailKnobs.webhook_url.is_set ? "stored; leave blank to keep" : ""}
                  onChange={(v) => setEmailValue("webhook_url", v)}
                />
                <label className="text-xs text-slate-600">
                  Webhook kind
                  <select
                    className="mt-1 w-full border border-slate-200 rounded px-2 py-1 text-sm bg-white"
                    value={emailValue("webhook_kind", emailKnobs.webhook_kind.value)}
                    onChange={(e) => setEmailValue("webhook_kind", e.target.value)}
                  >
                    <option value="generic">generic</option>
                    <option value="feishu">Feishu</option>
                    <option value="wecom">WeCom</option>
                  </select>
                </label>
                <div className="flex items-end gap-4 text-xs">
                  <label className="inline-flex items-center gap-1 pb-1">
                    <input
                      type="checkbox"
                      checked={emailValue("smtp_use_tls", emailKnobs.smtp_use_tls.value)}
                      onChange={(e) => setEmailValue("smtp_use_tls", e.target.checked)}
                    />
                    STARTTLS
                  </label>
                  <label className="inline-flex items-center gap-1 pb-1">
                    <input
                      type="checkbox"
                      checked={emailValue("smtp_use_ssl", emailKnobs.smtp_use_ssl.value)}
                      onChange={(e) => setEmailValue("smtp_use_ssl", e.target.checked)}
                    />
                    SSL
                  </label>
                  <label className="inline-flex items-center gap-1 pb-1">
                    <input
                      type="checkbox"
                      checked={emailValue("webhook_enabled", emailKnobs.webhook_enabled.value)}
                      onChange={(e) => setEmailValue("webhook_enabled", e.target.checked)}
                    />
                    webhook
                  </label>
                </div>
              </div>
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <button
                  onClick={saveEmail}
                  disabled={save.isPending}
                  className="text-xs bg-slate-900 text-white px-2 py-0.5 rounded hover:bg-slate-700 disabled:opacity-50"
                >
                  {save.isPending ? "saving…" : "save email"}
                </button>
                <button
                  onClick={() => testEmail.mutate()}
                  disabled={testEmail.isPending}
                  className="text-xs border border-slate-200 bg-white px-2 py-0.5 rounded hover:bg-slate-100 disabled:opacity-50"
                >
                  {testEmail.isPending ? "sending…" : "send test"}
                </button>
                <button
                  onClick={() => testWebhook.mutate()}
                  disabled={testWebhook.isPending}
                  className="text-xs border border-slate-200 bg-white px-2 py-0.5 rounded hover:bg-slate-100 disabled:opacity-50"
                >
                  {testWebhook.isPending ? "sending…" : "test webhook"}
                </button>
                {testEmail.data && (
                  <span className="text-xs text-emerald-700">
                    {testEmail.data.data.detail}
                  </span>
                )}
                {testEmail.error && (
                  <span className="text-xs text-red-600">
                    {(testEmail.error as Error).message}
                  </span>
                )}
                {testWebhook.data && (
                  <span className="text-xs text-emerald-700">
                    {testWebhook.data.data.detail}
                  </span>
                )}
                {testWebhook.error && (
                  <span className="text-xs text-red-600">
                    {(testWebhook.error as Error).message}
                  </span>
                )}
              </div>
            </div>
          )}
          <div className="rounded border border-slate-200 bg-slate-50 px-3 py-2">
            <div className="flex flex-wrap items-center gap-2 mb-1">
              <code className="text-xs text-slate-500">artifacts_cleanup</code>
              <input
                type="number"
                min={artifactKnob.min}
                max={artifactKnob.max}
                value={artifactRetention}
                onChange={(e) =>
                  setArtifactDraft(parseInt(e.target.value, 10) || artifactKnob.value)
                }
                className="border border-slate-200 rounded px-2 py-0.5 w-20 text-sm font-mono"
              />
              <span className="text-xs text-slate-500">days</span>
              <button
                disabled={save.isPending || artifactRetention === artifactKnob.value}
                onClick={() => save.mutate({ artifact_retention_days: artifactRetention })}
                className="text-xs bg-slate-900 text-white px-2 py-0.5 rounded hover:bg-slate-700 disabled:opacity-50"
              >
                {save.isPending ? "saving…" : "save"}
              </button>
              <button
                disabled={cleanupArtifacts.isPending}
                onClick={() => cleanupArtifacts.mutate(true)}
                className="text-xs border border-slate-200 bg-white px-2 py-0.5 rounded hover:bg-slate-100 disabled:opacity-50"
              >
                {cleanupArtifacts.isPending ? "checking…" : "dry run"}
              </button>
              <button
                disabled={cleanupArtifacts.isPending}
                onClick={() => cleanupArtifacts.mutate(false)}
                className="text-xs bg-red-600 text-white px-2 py-0.5 rounded hover:bg-red-700 disabled:opacity-50"
              >
                clean now
              </button>
              <span className="text-xs text-slate-400">
                default: <code>{String(artifactKnob.default)}</code>
              </span>
            </div>
            <p className="text-xs text-slate-500">{artifactKnob.describe}</p>
            {cleanupArtifacts.data && (
              <div className="mt-2 rounded bg-white border border-slate-100 p-2 text-xs text-slate-600">
                <div className="flex flex-wrap gap-3">
                  <span>
                    candidates:{" "}
                    <b className="text-slate-900">
                      {cleanupArtifacts.data.data.candidate_runs}
                    </b>
                  </span>
                  <span>
                    size:{" "}
                    <b className="text-slate-900">
                      {formatBytes(cleanupArtifacts.data.data.candidate_bytes)}
                    </b>
                  </span>
                  {!cleanupArtifacts.data.data.dry_run && (
                    <span>
                      deleted:{" "}
                      <b className="text-slate-900">
                        {cleanupArtifacts.data.data.deleted_runs} /{" "}
                        {formatBytes(cleanupArtifacts.data.data.deleted_bytes)}
                      </b>
                    </span>
                  )}
                </div>
                {cleanupArtifacts.data.data.errors.length > 0 && (
                  <pre className="mt-1 text-red-600 whitespace-pre-wrap">
                    {cleanupArtifacts.data.data.errors.slice(0, 3).join("\n")}
                  </pre>
                )}
              </div>
            )}
            {cleanupArtifacts.error && (
              <span className="text-red-600 text-xs">
                {(cleanupArtifacts.error as Error).message}
              </span>
            )}
          </div>
          {devContextKnobs && (
            <div className="rounded border border-slate-200 bg-slate-50 px-3 py-2">
              <div className="mb-2 flex flex-wrap items-center gap-2">
                <code className="text-xs text-slate-500">dev_context</code>
                <span className="text-xs text-slate-400">
                  workspace / zstack-dev-mcp / code search / server logs
                </span>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                <EmailInput
                  label="Workspace root"
                  value={devContextValue(
                    "michelle_workspace_root",
                    devContextKnobs.michelle_workspace_root.value,
                  )}
                  onChange={(v) => setDevContextValue("michelle_workspace_root", v)}
                />
                <EmailInput
                  label="MCP command"
                  value={devContextValue(
                    "michelle_zdev_mcp_command",
                    devContextKnobs.michelle_zdev_mcp_command.value,
                  )}
                  onChange={(v) => setDevContextValue("michelle_zdev_mcp_command", v)}
                />
                <EmailInput
                  label="MCP args"
                  value={devContextValue(
                    "michelle_zdev_mcp_args",
                    devContextKnobs.michelle_zdev_mcp_args.value,
                  )}
                  onChange={(v) => setDevContextValue("michelle_zdev_mcp_args", v)}
                />
                <EmailInput
                  label="MCP cwd"
                  value={devContextValue(
                    "michelle_zdev_mcp_cwd",
                    devContextKnobs.michelle_zdev_mcp_cwd.value,
                  )}
                  onChange={(v) => setDevContextValue("michelle_zdev_mcp_cwd", v)}
                />
                <EmailInput
                  label="Code repos"
                  value={devContextValue(
                    "michelle_dev_context_repos",
                    devContextKnobs.michelle_dev_context_repos.value,
                  )}
                  onChange={(v) => setDevContextValue("michelle_dev_context_repos", v)}
                />
                <div className="grid grid-cols-3 gap-2">
                  <EmailInput
                    label="MCP timeout"
                    type="number"
                    value={String(
                      devContextValue(
                        "michelle_zdev_mcp_timeout_seconds",
                        devContextKnobs.michelle_zdev_mcp_timeout_seconds.value,
                      ),
                    )}
                    onChange={(v) =>
                      setDevContextValue(
                        "michelle_zdev_mcp_timeout_seconds",
                        parseInt(v, 10) || 60,
                      )
                    }
                  />
                  <EmailInput
                    label="Max files"
                    type="number"
                    value={String(
                      devContextValue(
                        "michelle_dev_context_max_files",
                        devContextKnobs.michelle_dev_context_max_files.value,
                      ),
                    )}
                    onChange={(v) =>
                      setDevContextValue("michelle_dev_context_max_files", parseInt(v, 10) || 8)
                    }
                  />
                  <EmailInput
                    label="Matches/file"
                    type="number"
                    value={String(
                      devContextValue(
                        "michelle_dev_context_max_matches_per_file",
                        devContextKnobs.michelle_dev_context_max_matches_per_file.value,
                      ),
                    )}
                    onChange={(v) =>
                      setDevContextValue(
                        "michelle_dev_context_max_matches_per_file",
                        parseInt(v, 10) || 3,
                      )
                    }
                  />
                </div>
              </div>
              <label className="mt-2 block text-xs text-slate-600">
                Server logs JSON
                <textarea
                  rows={4}
                  value={devContextValue(
                    "michelle_server_logs_json",
                    devContextKnobs.michelle_server_logs_json.value,
                  )}
                  onChange={(e) =>
                    setDevContextValue("michelle_server_logs_json", e.target.value)
                  }
                  className="mt-1 w-full border border-slate-200 rounded px-2 py-1 text-xs font-mono bg-white"
                />
              </label>
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <button
                  onClick={saveDevContext}
                  disabled={save.isPending}
                  className="text-xs bg-slate-900 text-white px-2 py-0.5 rounded hover:bg-slate-700 disabled:opacity-50"
                >
                  {save.isPending ? "saving…" : "save DevContext"}
                </button>
                <button
                  onClick={() => setDevContextDraft({})}
                  disabled={Object.keys(devContextDraft).length === 0}
                  className="text-xs border border-slate-200 bg-white px-2 py-0.5 rounded hover:bg-slate-100 disabled:opacity-50"
                >
                  reset
                </button>
                <button
                  onClick={() => probeDevContextMcp.mutate()}
                  disabled={probeDevContextMcp.isPending}
                  className="text-xs border border-slate-200 bg-white px-2 py-0.5 rounded hover:bg-slate-100 disabled:opacity-50"
                >
                  {probeDevContextMcp.isPending ? "probing…" : "probe MCP"}
                </button>
                <button
                  onClick={() => probeServerLogs.mutate()}
                  disabled={probeServerLogs.isPending}
                  className="text-xs border border-slate-200 bg-white px-2 py-0.5 rounded hover:bg-slate-100 disabled:opacity-50"
                >
                  {probeServerLogs.isPending ? "probing…" : "probe logs"}
                </button>
              </div>
              {(probeDevContextMcp.data || probeServerLogs.data) && (
                <div className="mt-2 grid grid-cols-1 md:grid-cols-2 gap-2 text-xs">
                  {probeDevContextMcp.data && (
                    <ProbeResultCard
                      title="zstack-dev-mcp"
                      result={probeDevContextMcp.data.data}
                      extra={
                        probeDevContextMcp.data.data.tools?.length
                          ? `${probeDevContextMcp.data.data.tools.slice(0, 5).join(", ")}`
                          : ""
                      }
                    />
                  )}
                  {probeServerLogs.data && (
                    <ProbeResultCard
                      title="server logs"
                      result={probeServerLogs.data.data}
                      extra={
                        probeServerLogs.data.data.configured === false
                          ? "not configured"
                          : `${probeServerLogs.data.data.snippets ?? 0} snippets`
                      }
                    />
                  )}
                </div>
              )}
              {(probeDevContextMcp.error || probeServerLogs.error) && (
                <div className="mt-2 text-xs text-red-600">
                  {((probeDevContextMcp.error || probeServerLogs.error) as Error).message}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </Panel>
  );
}

function EmailInput({
  label,
  value,
  onChange,
  type = "text",
  placeholder = "",
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  type?: string;
  placeholder?: string;
}) {
  return (
    <label className="text-xs text-slate-600">
      {label}
      <input
        type={type}
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        className="mt-1 w-full border border-slate-200 rounded px-2 py-1 text-sm bg-white"
      />
    </label>
  );
}

function ProbeResultCard({
  title,
  result,
  extra,
}: {
  title: string;
  result: DevContextProbeResponse["data"];
  extra?: string;
}) {
  return (
    <div className="rounded border border-slate-200 bg-white px-2 py-1">
      <div className="flex items-center justify-between gap-2">
        <span className="font-medium text-slate-700">{title}</span>
        <span className={result.ok ? "text-emerald-700" : "text-amber-700"}>
          {result.ok ? "ok" : "check"}
        </span>
      </div>
      <div className="text-slate-500">
        {result.detail}
        {typeof result.elapsed_ms === "number" ? ` · ${result.elapsed_ms}ms` : ""}
      </div>
      {extra && <div className="truncate font-mono text-slate-500">{extra}</div>}
    </div>
  );
}

function ProviderSelect({
  label,
  knob,
  disabled,
  onChange,
}: {
  label: string;
  knob: RuntimeKnob<string>;
  disabled: boolean;
  onChange: (value: string) => void;
}) {
  return (
    <label className="text-xs text-slate-600">
      {label}
      <select
        className="mt-1 w-full border border-slate-200 rounded px-2 py-1 text-sm bg-white"
        value={knob.value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
      >
        {(knob.choices ?? ["auto"]).map((choice) => (
          <option key={choice} value={choice}>
            {choice}
          </option>
        ))}
      </select>
    </label>
  );
}

function AdminOpsPanel({ projectId }: { projectId: string }) {
  const qc = useQueryClient();
  const [importText, setImportText] = useState("");
  const [memberUserId, setMemberUserId] = useState("");
  const [memberRole, setMemberRole] = useState("viewer");
  const users = useQuery({
    queryKey: ["users"],
    queryFn: async () => {
      const r = await apiFetch("/api/auth/users");
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    },
  });
  const audit = useQuery({
    queryKey: ["audit"],
    queryFn: async () => {
      const r = await apiFetch("/api/auth/audit?limit=20");
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    },
  });
  const projectMembers = useQuery({
    queryKey: ["project-members", projectId],
    enabled: Boolean(projectId),
    queryFn: async () => {
      const r = await apiFetch(`/api/projects/${encodeURIComponent(projectId)}/members`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    },
  });
  const createUser = useMutation({
    mutationFn: async () => {
      const username = prompt("username");
      const password = prompt("password");
      const role = prompt("role: admin/reviewer/viewer", "viewer") || "viewer";
      if (!username || !password) return null;
      const r = await apiFetch("/api/auth/users", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password, role }),
      });
      if (!r.ok) throw new Error(await r.text());
      return r.json();
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["users"] }),
  });
  const importCases = useMutation({
    mutationFn: async () => {
      const parsed = JSON.parse(importText);
      const cases = Array.isArray(parsed) ? parsed : parsed.data || parsed.cases;
      const r = await apiFetch("/api/cases/import", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project_id: projectId, cases }),
      });
      if (!r.ok) throw new Error(await r.text());
      return r.json();
    },
    onSuccess: () => {
      setImportText("");
      qc.invalidateQueries({ queryKey: ["cases-summary"] });
    },
  });
  const upsertMember = useMutation({
    mutationFn: async () => {
      if (!memberUserId) return null;
      const r = await apiFetch(`/api/projects/${encodeURIComponent(projectId)}/members`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_id: memberUserId, role: memberRole }),
      });
      if (!r.ok) throw new Error(await r.text());
      return r.json();
    },
    onSuccess: () => {
      setMemberUserId("");
      qc.invalidateQueries({ queryKey: ["project-members", projectId] });
    },
  });
  const removeMember = useMutation({
    mutationFn: async (userId: string) => {
      const r = await apiFetch(
        `/api/projects/${encodeURIComponent(projectId)}/members/${encodeURIComponent(userId)}`,
        { method: "DELETE" },
      );
      if (!r.ok) throw new Error(await r.text());
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["project-members", projectId] }),
  });
  const download = async (url: string, filename: string) => {
    const r = await apiFetch(url);
    if (!r.ok) throw new Error(await r.text());
    const blob = await r.blob();
    const objectUrl = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = objectUrl;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(objectUrl);
  };

  return (
    <Panel title="Admin ops">
      <div className="grid grid-cols-1 lg:grid-cols-4 gap-4 text-sm">
        <div>
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs uppercase tracking-wide text-slate-400">Users</span>
            <button
              onClick={() => createUser.mutate()}
              className="text-xs border border-slate-200 rounded px-2 py-0.5"
            >
              + user
            </button>
          </div>
          <div className="space-y-1">
            {(users.data?.data ?? []).map((u: any) => (
              <div key={u.user_id} className="flex justify-between border-b border-slate-100 py-1">
                <span>{u.username}</span>
                <code className="text-xs text-slate-500">{u.role}</code>
              </div>
            ))}
          </div>
        </div>
        <div>
          <div className="text-xs uppercase tracking-wide text-slate-400 mb-2">Project access</div>
          <div className="space-y-1 mb-2 max-h-32 overflow-auto">
            {(projectMembers.data?.data ?? []).map((m: any) => (
              <div key={m.id} className="flex items-center justify-between border-b border-slate-100 py-1">
                <span>{m.username}</span>
                <span className="flex items-center gap-2">
                  <code className="text-xs text-slate-500">{m.role}</code>
                  <button
                    onClick={() => removeMember.mutate(m.user_id)}
                    className="text-xs text-red-700"
                  >
                    remove
                  </button>
                </span>
              </div>
            ))}
          </div>
          <select
            value={memberUserId}
            onChange={(e) => setMemberUserId(e.target.value)}
            className="w-full border border-slate-200 rounded px-2 py-1 text-xs"
          >
            <option value="">select user</option>
            {(users.data?.data ?? []).map((u: any) => (
              <option key={u.user_id} value={u.user_id}>
                {u.username}
              </option>
            ))}
          </select>
          <div className="mt-2 flex gap-2">
            <select
              value={memberRole}
              onChange={(e) => setMemberRole(e.target.value)}
              className="flex-1 border border-slate-200 rounded px-2 py-1 text-xs"
            >
              <option value="viewer">viewer</option>
              <option value="reviewer">reviewer</option>
              <option value="admin">admin</option>
            </select>
            <button
              disabled={!projectId || !memberUserId || upsertMember.isPending}
              onClick={() => upsertMember.mutate()}
              className="text-xs bg-slate-900 text-white rounded px-2 py-0.5 disabled:opacity-50"
            >
              grant
            </button>
          </div>
        </div>
        <div>
          <div className="text-xs uppercase tracking-wide text-slate-400 mb-2">Import / Export</div>
          <div className="flex flex-wrap gap-2 mb-2">
            <button
              onClick={() =>
                void download(
                  `/api/cases/export?project_id=${encodeURIComponent(projectId)}&format=json`,
                  "michelle-cases.json",
                )
              }
              className="text-xs border border-slate-200 rounded px-2 py-0.5"
            >
              export cases JSON
            </button>
            <button
              onClick={() =>
                void download(
                  `/api/cases/export?project_id=${encodeURIComponent(projectId)}&format=csv`,
                  "michelle-cases.csv",
                )
              }
              className="text-xs border border-slate-200 rounded px-2 py-0.5"
            >
              export cases CSV
            </button>
            <button
              onClick={() => void download("/api/diagnosis/export", "michelle-diagnosis.json")}
              className="text-xs border border-slate-200 rounded px-2 py-0.5"
            >
              export diagnosis
            </button>
          </div>
          <textarea
            value={importText}
            onChange={(e) => setImportText(e.target.value)}
            rows={4}
            className="w-full border border-slate-200 rounded p-2 font-mono text-xs"
            placeholder='Paste exported cases JSON: {"data":[...]}'
          />
          <button
            disabled={!projectId || !importText.trim() || importCases.isPending}
            onClick={() => importCases.mutate()}
            className="mt-2 text-xs bg-slate-900 text-white rounded px-2 py-0.5 disabled:opacity-50"
          >
            import cases
          </button>
        </div>
        <div>
          <div className="text-xs uppercase tracking-wide text-slate-400 mb-2">Audit log</div>
          <div className="space-y-1 max-h-48 overflow-auto">
            {(audit.data?.data ?? []).map((a: any) => (
              <div key={a.audit_id} className="border-b border-slate-100 py-1 text-xs">
                <div>
                  <code>{a.actor_username || "system"}</code> {a.action}
                </div>
                <div className="text-slate-400">{a.path}</div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </Panel>
  );
}

// ── primitives ──

function Panel({
  title,
  children,
  linkTo,
  linkLabel,
  onEdit,
}: {
  title: string;
  children: React.ReactNode;
  linkTo?: string;
  linkLabel?: string;
  onEdit?: () => void;
}) {
  return (
    <div className="bg-white border border-slate-200 rounded-lg p-4">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs uppercase tracking-wide text-slate-400">{title}</span>
        <div className="flex items-center gap-3">
          {onEdit && (
            <button
              onClick={onEdit}
              className="text-xs text-blue-700 hover:underline"
            >
              ✎ edit
            </button>
          )}
          {linkTo && (
            <Link to={linkTo} className="text-xs text-blue-700 hover:underline">
              {linkLabel || "open"}
            </Link>
          )}
        </div>
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

function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

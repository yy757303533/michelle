import { createFileRoute } from "@tanstack/react-router";
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
  const health = useQuery({
    queryKey: ["healthz"],
    queryFn: async (): Promise<HealthResponse> => {
      const r = await fetch("/healthz");
      if (!r.ok) throw new Error("backend down");
      return r.json();
    },
    refetchInterval: 5000,
  });

  const llm = useQuery({
    queryKey: ["llm-health"],
    queryFn: async (): Promise<LLMHealth> => {
      const r = await fetch("/api/llm/health");
      if (!r.ok) throw new Error("llm health endpoint failed");
      return r.json();
    },
    refetchInterval: 10000,
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Dashboard</h1>
        <p className="text-slate-500 text-sm mt-1">
          Day 3 — LLM Gateway live · Day 4 onwards adds PRD ingest, runs, diagnosis.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-4">
        <Panel title="Backend health">
          {health.isLoading && <span className="text-slate-400">checking…</span>}
          {health.error && <span className="text-red-600">offline</span>}
          {health.data && (
            <div className="space-y-1 text-sm">
              <Row label="status" value={<Pill ok>{health.data.status}</Pill>} />
              <Row label="version" value={<code>{health.data.version}</code>} />
              <Row label="env" value={<code>{health.data.env}</code>} />
            </div>
          )}
        </Panel>
        <Panel title="LLM providers (priority order)">
          {llm.isLoading && <span className="text-slate-400">checking…</span>}
          {llm.data && (
            <div className="space-y-1 text-sm">
              {Object.entries(llm.data.data)
                .sort(([, a], [, b]) => a.priority - b.priority)
                .map(([name, p]) => (
                  <div key={name} className="flex justify-between">
                    <span className="text-slate-700">
                      <code>{name}</code>{" "}
                      <span className="text-slate-400 text-xs">prio {p.priority}</span>
                    </span>
                    <span className={p.available ? "text-emerald-600" : "text-slate-300"}>
                      {p.available ? "configured" : "off"}
                    </span>
                  </div>
                ))}
            </div>
          )}
        </Panel>
      </div>

      <ProbePanel />
    </div>
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
    <Panel title="LLM smoke probe">
      <div className="flex items-center gap-3 text-sm mb-3">
        <label className="text-slate-500">prefer</label>
        <select
          className="border border-slate-200 rounded px-2 py-1 text-sm"
          value={prefer}
          onChange={(e) => setPrefer(e.target.value)}
        >
          <option value="">auto (priority order)</option>
          <option value="claude-cli">claude-cli</option>
          <option value="minimax">minimax</option>
          <option value="flywheel">flywheel</option>
        </select>
        <button
          className="bg-slate-900 text-white text-sm px-3 py-1 rounded hover:bg-slate-700 disabled:opacity-50"
          disabled={probe.isPending}
          onClick={() => probe.mutate()}
        >
          {probe.isPending ? "calling…" : "probe"}
        </button>
        <span className="text-xs text-slate-400">
          fires a 10-token "reply: ok" through the gateway (with auto-fallback)
        </span>
      </div>

      {last && (
        <div className="bg-slate-50 rounded p-3 text-sm space-y-1">
          {last.ok ? (
            <>
              <div className="flex justify-between">
                <Pill ok>ok</Pill>
                <span className="text-xs text-slate-500">
                  via <code>{last.provider}</code>{" "}
                  <span className="text-slate-300">·</span> <code>{last.model}</code>
                </span>
              </div>
              <Row label="text" value={<code className="text-emerald-700">{last.text}</code>} />
              <Row
                label="latency"
                value={<code>{last.latency_ms} ms</code>}
              />
              <Row
                label="tokens"
                value={
                  <code>
                    in={last.input_tokens} out={last.output_tokens}
                    {last.cost_usd ? ` · $${last.cost_usd.toFixed(4)}` : ""}
                  </code>
                }
              />
            </>
          ) : (
            <>
              <Pill>err</Pill>
              <Row
                label="error_type"
                value={<code className="text-red-700">{last.error_type}</code>}
              />
              <Row label="msg" value={<code>{last.error}</code>} />
            </>
          )}
        </div>
      )}
    </Panel>
  );
}

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="bg-white border border-slate-200 rounded-lg p-4">
      <div className="text-xs uppercase tracking-wide text-slate-400 mb-2">{title}</div>
      {children}
    </div>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex justify-between items-center">
      <span className="text-slate-500">{label}</span>
      {value}
    </div>
  );
}

function Pill({ children, ok }: { children: React.ReactNode; ok?: boolean }) {
  return (
    <span
      className={
        "inline-block text-xs px-2 py-0.5 rounded font-mono " +
        (ok ? "bg-emerald-50 text-emerald-700" : "bg-red-50 text-red-700")
      }
    >
      {children}
    </span>
  );
}

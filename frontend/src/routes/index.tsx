import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";

export const Route = createFileRoute("/")({
  component: Dashboard,
});

interface HealthResponse {
  status: string;
  version: string;
  env: string;
  providers: Record<string, boolean>;
}

function Dashboard() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["healthz"],
    queryFn: async (): Promise<HealthResponse> => {
      const r = await fetch("/healthz");
      if (!r.ok) throw new Error("backend down");
      return r.json();
    },
    refetchInterval: 5000,
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Dashboard</h1>
        <p className="text-slate-500 text-sm mt-1">Day 1 skeleton — full features land Days 4-11.</p>
      </div>

      <div className="grid grid-cols-2 gap-4">
        <Panel title="Backend health">
          {isLoading && <span className="text-slate-400">checking…</span>}
          {error && <span className="text-red-600">offline</span>}
          {data && (
            <div className="space-y-1 text-sm">
              <div>
                <span className="text-slate-500">status</span>{" "}
                <span className="font-mono text-emerald-600">{data.status}</span>
              </div>
              <div>
                <span className="text-slate-500">version</span>{" "}
                <span className="font-mono">{data.version}</span>
              </div>
              <div>
                <span className="text-slate-500">env</span>{" "}
                <span className="font-mono">{data.env}</span>
              </div>
            </div>
          )}
        </Panel>
        <Panel title="LLM providers">
          {data ? (
            <div className="space-y-1 text-sm">
              {Object.entries(data.providers).map(([k, v]) => (
                <div key={k} className="flex justify-between">
                  <span className="text-slate-500">{k}</span>
                  <span className={v ? "text-emerald-600" : "text-slate-300"}>
                    {v ? "configured" : "off"}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <span className="text-slate-400">—</span>
          )}
        </Panel>
      </div>

      <Panel title="Status">
        <p className="text-sm text-slate-600">
          Skeleton up. <span className="font-mono text-xs">/healthz</span> ↔ backend ↔ frontend wired.
        </p>
      </Panel>
    </div>
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

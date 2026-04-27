import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

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
  source: string;
  prompt_version: string;
  model_version: string;
  generated_from: string | null;
  review_status: string;
  steps: Array<{ intent: string; expected?: string }>;
  assertions: Array<{ description: string }>;
  preconditions: string[];
  version: number;
  created_at: string;
}

interface CasesResponse {
  data: CaseRow[];
  count: number;
  counts_by_status: Record<string, number>;
}

const STATUS_FILTERS: Array<{ key: string; label: string }> = [
  { key: "", label: "all" },
  { key: "pending", label: "pending" },
  { key: "approved", label: "approved" },
  { key: "rejected", label: "rejected" },
];

function CasesPage() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [filter, setFilter] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);

  const cases = useQuery({
    queryKey: ["cases", filter],
    queryFn: async (): Promise<CasesResponse> => {
      const u = filter
        ? `/api/cases/?status=${encodeURIComponent(filter)}`
        : "/api/cases/";
      const r = await fetch(u);
      return r.json();
    },
  });

  const review = useMutation({
    mutationFn: async ({ id, action }: { id: string; action: "approve" | "reject" }) => {
      const r = await fetch(`/api/cases/${id}/review`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action }),
      });
      if (!r.ok) throw new Error(await r.text());
      return r.json();
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["cases"] }),
  });

  const runMut = useMutation({
    mutationFn: async (case_id: string): Promise<{ data: { run_ids: string[] } }> => {
      const r = await fetch("/api/runs/", {
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

  const counts = cases.data?.counts_by_status ?? {};

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Test cases</h1>
        <p className="text-slate-500 text-sm mt-1">
          AI-generated drafts enter as <code>pending</code>. Review → approve → click <strong>Run</strong>.
          {runMut.error && <span className="ml-2 text-red-600">run error: {(runMut.error as Error).message}</span>}
        </p>
      </div>

      <div className="flex items-center gap-2">
        {STATUS_FILTERS.map((f) => {
          const n = f.key ? counts[f.key] ?? 0 : Object.values(counts).reduce((a, b) => a + b, 0);
          const active = filter === f.key;
          return (
            <button
              key={f.key}
              onClick={() => setFilter(f.key)}
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
      </div>

      <div className="bg-white border border-slate-200 rounded-lg">
        {cases.isLoading ? (
          <div className="p-6 text-slate-400 text-sm">loading…</div>
        ) : (cases.data?.count ?? 0) === 0 ? (
          <div className="p-6 text-slate-400 text-sm">
            no cases yet — head to <a className="text-blue-700 underline" href="/prd">PRD</a> to upload one
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-left text-slate-400 border-b border-slate-100">
              <tr>
                <th className="p-2 w-44">case_id</th>
                <th className="p-2">name</th>
                <th className="p-2 w-20">priority</th>
                <th className="p-2 w-20">module</th>
                <th className="p-2 w-24">status</th>
                <th className="p-2 w-44">prompt / model</th>
                <th className="p-2 w-44">actions</th>
              </tr>
            </thead>
            <tbody>
              {cases.data?.data.map((c) => (
                <CaseRowView
                  key={c.case_id}
                  c={c}
                  expanded={expanded === c.case_id}
                  onToggle={() =>
                    setExpanded((prev) => (prev === c.case_id ? null : c.case_id))
                  }
                  onApprove={() =>
                    review.mutate({ id: c.case_id, action: "approve" })
                  }
                  onReject={() =>
                    review.mutate({ id: c.case_id, action: "reject" })
                  }
                  onRun={() => runMut.mutate(c.case_id)}
                  busy={review.isPending}
                  runBusy={runMut.isPending && runMut.variables === c.case_id}
                />
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

function CaseRowView({
  c,
  expanded,
  onToggle,
  onApprove,
  onReject,
  onRun,
  busy,
  runBusy,
}: {
  c: CaseRow;
  expanded: boolean;
  onToggle: () => void;
  onApprove: () => void;
  onReject: () => void;
  onRun: () => void;
  busy: boolean;
  runBusy: boolean;
}) {
  return (
    <>
      <tr className="border-t border-slate-100 hover:bg-slate-50 cursor-pointer" onClick={onToggle}>
        <td className="p-2 font-mono text-xs">{c.case_id}</td>
        <td className="p-2">
          <div className="font-medium">{c.name}</div>
          <div className="text-xs text-slate-500 truncate">{c.intent}</div>
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
        <td className="p-2 text-xs text-slate-500 font-mono">
          {c.prompt_version} · {c.model_version}
        </td>
        <td className="p-2" onClick={(e) => e.stopPropagation()}>
          {c.review_status === "pending" ? (
            <>
              <button
                className="text-xs px-2 py-0.5 rounded bg-emerald-700 text-white hover:bg-emerald-800 disabled:opacity-50 mr-1"
                disabled={busy}
                onClick={onApprove}
              >
                approve
              </button>
              <button
                className="text-xs px-2 py-0.5 rounded bg-red-700 text-white hover:bg-red-800 disabled:opacity-50"
                disabled={busy}
                onClick={onReject}
              >
                reject
              </button>
            </>
          ) : c.review_status === "approved" ? (
            <button
              className="text-xs px-2 py-0.5 rounded bg-blue-700 text-white hover:bg-blue-800 disabled:opacity-50"
              disabled={runBusy}
              onClick={onRun}
            >
              {runBusy ? "starting…" : "▶ Run"}
            </button>
          ) : (
            <span className="text-xs text-slate-400">—</span>
          )}
        </td>
      </tr>
      {expanded && (
        <tr>
          <td colSpan={7} className="p-3 bg-slate-50">
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
                    <li key={i}>{a.description}</li>
                  ))}
                </ul>
              </Block>
            </div>
            <div className="mt-3 text-xs text-slate-400 font-mono">
              tags: {c.tags.join(", ") || "—"} · source: {c.source} · from:{" "}
              {c.generated_from || "—"}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

function StatusPill({ status }: { status: string }) {
  const colors: Record<string, string> = {
    pending: "bg-amber-50 text-amber-700",
    approved: "bg-emerald-50 text-emerald-700",
    rejected: "bg-red-50 text-red-700",
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

function Block({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-xs uppercase tracking-wide text-slate-400 mb-1">
        {title}
      </div>
      {children}
    </div>
  );
}

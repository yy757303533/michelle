import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useCurrentProject } from "../lib/useCurrentProject";

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
  manual_edited_fields: string[];
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
  { key: "stale", label: "stale" },
];

function CasesPage() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const { projectId } = useCurrentProject();
  const [filter, setFilter] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const cases = useQuery({
    // Re-key on project so swapping the global selector re-fetches.
    queryKey: ["cases", projectId, filter],
    enabled: Boolean(projectId),
    queryFn: async (): Promise<CasesResponse> => {
      const params = new URLSearchParams({ project_id: projectId, limit: "200" });
      if (filter) params.set("status", filter);
      const r = await fetch(`/api/cases/?${params}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`);
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

  const bulk = useMutation({
    mutationFn: async (action: "approve" | "reject") => {
      const r = await fetch("/api/cases/bulk-review", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ case_ids: [...selected], action }),
      });
      if (!r.ok) throw new Error(await r.text());
      return r.json();
    },
    onSuccess: () => {
      setSelected(new Set());
      qc.invalidateQueries({ queryKey: ["cases"] });
    },
  });

  const editMut = useMutation({
    mutationFn: async ({ id, patch }: { id: string; patch: Partial<CaseRow> }) => {
      const r = await fetch(`/api/cases/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch),
      });
      if (!r.ok) throw new Error(await r.text());
      return r.json();
    },
    onSuccess: () => {
      setEditing(null);
      qc.invalidateQueries({ queryKey: ["cases"] });
    },
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
  const visible = cases.data?.data ?? [];
  const allSelected = visible.length > 0 && visible.every((c) => selected.has(c.case_id));

  const toggleSelect = (id: string) => {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setSelected(next);
  };
  const toggleSelectAll = () => {
    if (allSelected) setSelected(new Set());
    else setSelected(new Set(visible.map((c) => c.case_id)));
  };

  if (!projectId) {
    return (
      <div className="bg-white border border-slate-200 rounded-lg p-8 text-center text-sm text-slate-500">
        Pick a project from the header dropdown to see its cases.
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-semibold">
          Test cases <span className="text-slate-400 text-base font-normal">/ {projectId}</span>
        </h1>
        <p className="text-slate-500 text-sm mt-1">
          AI drafts → review → run. Edits to approved cases re-open them as pending.
          {runMut.error && (
            <span className="ml-2 text-red-600">run error: {(runMut.error as Error).message}</span>
          )}
          {review.error && (
            <span className="ml-2 text-red-600">review error: {(review.error as Error).message}</span>
          )}
          {bulk.error && (
            <span className="ml-2 text-red-600">bulk error: {(bulk.error as Error).message}</span>
          )}
          {editMut.error && (
            <span className="ml-2 text-red-600">edit error: {(editMut.error as Error).message}</span>
          )}
          {cases.error && (
            <span className="ml-2 text-red-600">load error: {(cases.error as Error).message}</span>
          )}
        </p>
      </div>

      {/* Filter pills */}
      <div className="flex items-center gap-2">
        {STATUS_FILTERS.map((f) => {
          const n = f.key
            ? counts[f.key] ?? 0
            : Object.values(counts).reduce((a, b) => a + b, 0);
          const active = filter === f.key;
          return (
            <button
              key={f.key}
              onClick={() => {
                setFilter(f.key);
                // Without this, IDs selected under one filter remain in
                // `selected` after switching tabs; bulk-review would then act
                // on rows the user can't see.
                setSelected(new Set());
              }}
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

      {/* Bulk action bar */}
      {selected.size > 0 && (
        <div className="flex items-center gap-3 bg-slate-900 text-white rounded px-3 py-2 text-sm">
          <span>{selected.size} selected</span>
          <button
            disabled={bulk.isPending}
            onClick={() => bulk.mutate("approve")}
            className="bg-emerald-600 px-3 py-0.5 rounded hover:bg-emerald-500 disabled:opacity-50"
          >
            ✓ Approve
          </button>
          <button
            disabled={bulk.isPending}
            onClick={() => bulk.mutate("reject")}
            className="bg-red-600 px-3 py-0.5 rounded hover:bg-red-500 disabled:opacity-50"
          >
            ✗ Reject
          </button>
          <button
            onClick={() => setSelected(new Set())}
            className="text-slate-300 px-3 py-0.5 rounded hover:text-white ml-auto"
          >
            clear
          </button>
        </div>
      )}

      <div className="bg-white border border-slate-200 rounded-lg">
        {cases.isLoading ? (
          <div className="p-6 text-slate-400 text-sm">loading…</div>
        ) : visible.length === 0 ? (
          <div className="p-6 text-slate-400 text-sm">
            no cases yet — head to{" "}
            <a className="text-blue-700 underline" href="/prd">
              PRD
            </a>{" "}
            to upload one
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-left text-slate-400 border-b border-slate-100">
              <tr>
                <th className="p-2 w-8">
                  <input type="checkbox" checked={allSelected} onChange={toggleSelectAll} />
                </th>
                <th className="p-2 w-44">case_id</th>
                <th className="p-2">name</th>
                <th className="p-2 w-20">priority</th>
                <th className="p-2 w-24">module</th>
                <th className="p-2 w-24">status</th>
                <th className="p-2 w-32">edited</th>
                <th className="p-2 w-48">actions</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((c) => (
                <CaseRowView
                  key={c.case_id}
                  c={c}
                  expanded={expanded === c.case_id}
                  editing={editing === c.case_id}
                  selected={selected.has(c.case_id)}
                  onSelect={() => toggleSelect(c.case_id)}
                  onToggle={() =>
                    setExpanded((prev) => (prev === c.case_id ? null : c.case_id))
                  }
                  onApprove={() => review.mutate({ id: c.case_id, action: "approve" })}
                  onReject={() => review.mutate({ id: c.case_id, action: "reject" })}
                  onRun={() => runMut.mutate(c.case_id)}
                  onEdit={() => setEditing(c.case_id)}
                  onCancelEdit={() => setEditing(null)}
                  onSubmitEdit={(patch) =>
                    editMut.mutate({ id: c.case_id, patch })
                  }
                  busy={review.isPending && review.variables?.id === c.case_id}
                  runBusy={runMut.isPending && runMut.variables === c.case_id}
                  editBusy={editMut.isPending && editMut.variables?.id === c.case_id}
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
  editing,
  selected,
  onSelect,
  onToggle,
  onApprove,
  onReject,
  onRun,
  onEdit,
  onCancelEdit,
  onSubmitEdit,
  busy,
  runBusy,
  editBusy,
}: {
  c: CaseRow;
  expanded: boolean;
  editing: boolean;
  selected: boolean;
  onSelect: () => void;
  onToggle: () => void;
  onApprove: () => void;
  onReject: () => void;
  onRun: () => void;
  onEdit: () => void;
  onCancelEdit: () => void;
  onSubmitEdit: (patch: Partial<CaseRow>) => void;
  busy: boolean;
  runBusy: boolean;
  editBusy: boolean;
}) {
  return (
    <>
      <tr
        className="border-t border-slate-100 hover:bg-slate-50 cursor-pointer"
        onClick={onToggle}
      >
        <td className="p-2" onClick={(e) => e.stopPropagation()}>
          <input type="checkbox" checked={selected} onChange={onSelect} />
        </td>
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
        <td className="p-2">
          {c.manual_edited_fields.length === 0 ? (
            <span className="text-slate-300 text-xs">—</span>
          ) : (
            <span
              className="text-xs text-amber-700 font-mono"
              title={c.manual_edited_fields.join(", ")}
            >
              ✎ {c.manual_edited_fields.length} field
              {c.manual_edited_fields.length > 1 ? "s" : ""}
            </span>
          )}
        </td>
        <td className="p-2" onClick={(e) => e.stopPropagation()}>
          {c.review_status === "pending" || c.review_status === "stale" ? (
            <>
              <button
                className="text-xs px-2 py-0.5 rounded bg-emerald-700 text-white hover:bg-emerald-800 disabled:opacity-50 mr-1"
                disabled={busy}
                onClick={onApprove}
              >
                approve
              </button>
              <button
                className="text-xs px-2 py-0.5 rounded bg-red-700 text-white hover:bg-red-800 disabled:opacity-50 mr-1"
                disabled={busy}
                onClick={onReject}
              >
                reject
              </button>
              <button
                className="text-xs px-2 py-0.5 rounded bg-slate-200 text-slate-700 hover:bg-slate-300"
                onClick={() => {
                  if (!expanded) onToggle();
                  onEdit();
                }}
              >
                edit
              </button>
            </>
          ) : c.review_status === "approved" ? (
            <>
              <button
                className="text-xs px-2 py-0.5 rounded bg-blue-700 text-white hover:bg-blue-800 disabled:opacity-50 mr-1"
                disabled={runBusy}
                onClick={onRun}
              >
                {runBusy ? "starting…" : "▶ Run"}
              </button>
              <button
                className="text-xs px-2 py-0.5 rounded bg-slate-200 text-slate-700 hover:bg-slate-300"
                onClick={() => {
                  if (!expanded) onToggle();
                  onEdit();
                }}
                title="editing an approved case re-opens it as pending"
              >
                edit
              </button>
            </>
          ) : (
            <span className="text-xs text-slate-400">—</span>
          )}
        </td>
      </tr>
      {expanded && !editing && (
        <tr>
          <td colSpan={8} className="p-3 bg-slate-50">
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
              tags: {c.tags.join(", ") || "—"} · source: {c.source} · prompt:{" "}
              {c.prompt_version} · model: {c.model_version} · from:{" "}
              {c.generated_from || "—"} · v{c.version}
              {c.manual_edited_fields.length > 0 && (
                <span className="ml-2 text-amber-700">
                  · edited: {c.manual_edited_fields.join(", ")}
                </span>
              )}
            </div>
          </td>
        </tr>
      )}
      {expanded && editing && (
        <EditForm
          c={c}
          onCancel={onCancelEdit}
          onSubmit={onSubmitEdit}
          busy={editBusy}
        />
      )}
    </>
  );
}

function EditForm({
  c,
  onCancel,
  onSubmit,
  busy,
}: {
  c: CaseRow;
  onCancel: () => void;
  onSubmit: (patch: Partial<CaseRow>) => void;
  busy: boolean;
}) {
  const [name, setName] = useState(c.name);
  const [intent, setIntent] = useState(c.intent);
  const [module, setModule] = useState(c.module);
  const [priority, setPriority] = useState(c.priority);
  const [stepsRaw, setStepsRaw] = useState(
    c.steps
      .map((s) => `${s.intent}${s.expected ? `  | ${s.expected}` : ""}`)
      .join("\n"),
  );
  const [assertionsRaw, setAssertionsRaw] = useState(
    c.assertions.map((a) => a.description).join("\n"),
  );

  const submit = () => {
    const patch: Partial<CaseRow> = {};
    if (name !== c.name) patch.name = name;
    if (intent !== c.intent) patch.intent = intent;
    if (module !== c.module) patch.module = module;
    if (priority !== c.priority) patch.priority = priority;

    const newSteps = stepsRaw
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line) => {
        // Split on the FIRST `|` only — `expected` may legitimately contain
        // pipes (regex literals, table cells in the description, …).
        const idx = line.indexOf("|");
        if (idx === -1) return { intent: line.trim() };
        const intent = line.slice(0, idx).trim();
        const expected = line.slice(idx + 1).trim();
        return expected ? { intent, expected } : { intent };
      });
    if (JSON.stringify(newSteps) !== JSON.stringify(c.steps)) patch.steps = newSteps;

    const newAssertions = assertionsRaw
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean)
      .map((d) => ({ description: d }));
    if (JSON.stringify(newAssertions) !== JSON.stringify(c.assertions))
      patch.assertions = newAssertions;

    if (Object.keys(patch).length === 0) {
      onCancel();
      return;
    }
    onSubmit(patch);
  };

  return (
    <tr>
      <td colSpan={8} className="p-4 bg-amber-50 border-t border-amber-200">
        <div className="text-xs uppercase tracking-wide text-amber-700 mb-3">
          edit case · {c.case_id}
        </div>
        <div className="grid grid-cols-2 gap-3 text-sm">
          <Field label="name">
            <input
              className="border border-slate-200 rounded px-2 py-1 w-full"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </Field>
          <Field label="priority">
            <select
              className="border border-slate-200 rounded px-2 py-1 w-full"
              value={priority}
              onChange={(e) => setPriority(e.target.value)}
            >
              <option>P0</option>
              <option>P1</option>
              <option>P2</option>
            </select>
          </Field>
          <Field label="intent" full>
            <textarea
              className="border border-slate-200 rounded px-2 py-1 w-full"
              rows={2}
              value={intent}
              onChange={(e) => setIntent(e.target.value)}
            />
          </Field>
          <Field label="module">
            <input
              className="border border-slate-200 rounded px-2 py-1 w-full"
              value={module}
              onChange={(e) => setModule(e.target.value)}
            />
          </Field>
          <Field label="steps (one per line, format: intent | expected)" full>
            <textarea
              className="border border-slate-200 rounded px-2 py-1 w-full font-mono text-xs"
              rows={Math.max(4, c.steps.length + 1)}
              value={stepsRaw}
              onChange={(e) => setStepsRaw(e.target.value)}
            />
          </Field>
          <Field label="assertions (one per line)" full>
            <textarea
              className="border border-slate-200 rounded px-2 py-1 w-full font-mono text-xs"
              rows={Math.max(2, c.assertions.length + 1)}
              value={assertionsRaw}
              onChange={(e) => setAssertionsRaw(e.target.value)}
            />
          </Field>
        </div>
        <div className="mt-3 flex items-center gap-2">
          <button
            className="bg-emerald-700 text-white text-sm px-3 py-1 rounded hover:bg-emerald-800 disabled:opacity-50"
            disabled={busy}
            onClick={submit}
          >
            {busy ? "saving…" : "save (→ pending)"}
          </button>
          <button
            className="bg-white text-slate-700 text-sm px-3 py-1 rounded border border-slate-200 hover:bg-slate-50"
            onClick={onCancel}
          >
            cancel
          </button>
          <span className="text-xs text-amber-700 ml-2">
            edited fields will be tracked and protected from future LLM regenerations.
          </span>
        </div>
      </td>
    </tr>
  );
}

function Field({
  label,
  children,
  full,
}: {
  label: string;
  children: React.ReactNode;
  full?: boolean;
}) {
  return (
    <label className={"block " + (full ? "col-span-2" : "")}>
      <span className="text-xs text-slate-500 mb-1 block uppercase tracking-wide">
        {label}
      </span>
      {children}
    </label>
  );
}

function StatusPill({ status }: { status: string }) {
  const colors: Record<string, string> = {
    pending: "bg-amber-50 text-amber-700",
    approved: "bg-emerald-50 text-emerald-700",
    rejected: "bg-red-50 text-red-700",
    stale: "bg-slate-100 text-slate-500",
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
      <div className="text-xs uppercase tracking-wide text-slate-400 mb-1">{title}</div>
      {children}
    </div>
  );
}

import { createFileRoute, Link } from "@tanstack/react-router";

export const Route = createFileRoute("/diagnosis/$id")({
  component: DiagnosisDetailPage,
});

function DiagnosisDetailPage() {
  const { id } = Route.useParams();
  return (
    <div className="space-y-4">
      <Link to="/runs" className="text-xs text-slate-500 hover:text-slate-900">
        ← runs
      </Link>
      <h1 className="text-2xl font-semibold">Diagnosis #{id}</h1>
      <div className="bg-amber-50 border border-amber-200 rounded p-4 text-sm">
        <div className="font-medium text-amber-900 mb-1">Day 11 placeholder</div>
        <div className="text-amber-800">
          AI diagnosis lands on Day 11. This page will show category
          (real_bug / flaky / selector_drift / vision_misjudge / env_issue / data_issue),
          confidence (0..1), the model's reasoning, a one-sentence fix suggestion,
          and human-feedback buttons (confirmed / wrong / partial) that feed the
          sediment loop.
        </div>
      </div>
    </div>
  );
}

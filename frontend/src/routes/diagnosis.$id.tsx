import { createFileRoute } from "@tanstack/react-router";

export const Route = createFileRoute("/diagnosis/$id")({
  component: DiagnosisDetailPage,
});

function DiagnosisDetailPage() {
  const { id } = Route.useParams();
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Diagnosis #{id}</h1>
      <p className="text-slate-500 text-sm">
        AI diagnosis page lands Day 11. Shows category, confidence, reasoning, fix suggestion, feedback buttons.
      </p>
    </div>
  );
}

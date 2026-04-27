import { createFileRoute } from "@tanstack/react-router";

export const Route = createFileRoute("/runs/$id")({
  component: RunDetailPage,
});

function RunDetailPage() {
  const { id } = Route.useParams();
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Run #{id}</h1>
      <p className="text-slate-500 text-sm">
        Full trace viewer + screenshot timeline lands Day 10.
      </p>
    </div>
  );
}

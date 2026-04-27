import { createFileRoute } from "@tanstack/react-router";

export const Route = createFileRoute("/prd")({
  component: PrdPage,
});

function PrdPage() {
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">PRD</h1>
      <p className="text-slate-500 text-sm">
        Upload a markdown PRD here. Chapter-level diff and AI case generation land Day 4.
      </p>
      <div className="bg-white border border-dashed border-slate-300 rounded-lg p-12 text-center text-slate-400">
        (upload area placeholder)
      </div>
    </div>
  );
}

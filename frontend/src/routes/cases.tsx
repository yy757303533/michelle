import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";

export const Route = createFileRoute("/cases")({
  component: CasesPage,
});

interface CasesResponse {
  data: Array<Record<string, unknown>>;
  count: number;
}

function CasesPage() {
  const { data, isLoading } = useQuery({
    queryKey: ["cases"],
    queryFn: async (): Promise<CasesResponse> => {
      const r = await fetch("/api/cases/");
      return r.json();
    },
  });

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Test Cases</h1>
      <p className="text-slate-500 text-sm">
        Review workflow lands Day 7-8. Currently lists what's in the database.
      </p>
      <div className="bg-white border border-slate-200 rounded-lg p-6">
        {isLoading ? (
          <span className="text-slate-400">loading…</span>
        ) : (data?.count ?? 0) === 0 ? (
          <span className="text-slate-400">no cases yet — upload a PRD on the PRD page</span>
        ) : (
          <pre className="text-xs">{JSON.stringify(data, null, 2)}</pre>
        )}
      </div>
    </div>
  );
}

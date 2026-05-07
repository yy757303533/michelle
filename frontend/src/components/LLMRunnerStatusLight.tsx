import type { LLMRunnerStatus, LLMRunnerStatusResponse } from "../lib/useLLMRunnerStatus";

export function isLLMRunnerBlocked(status?: LLMRunnerStatus) {
  return status === "down" || status === "starting";
}

export function LLMRunnerStatusLight({
  data,
  loading,
  compact = false,
}: {
  data?: LLMRunnerStatusResponse;
  loading?: boolean;
  compact?: boolean;
}) {
  const status: LLMRunnerStatus = loading ? "unknown" : (data?.status ?? "unknown");
  const meta = STATUS_META[status];
  const title = [
    `executor: ${status}`,
    data?.configured_loop ? `configured: ${data.configured_loop}` : "",
    data?.resolved_loop ? `resolved: ${data.resolved_loop}` : "",
    data?.mode ? `mode: ${data.mode}` : "",
    data?.generic_providers?.length
      ? `generic providers: ${data.generic_providers.join(", ")}`
      : "",
    data?.npx_available === false ? "npx: missing" : "",
    data?.claude_cli_available === false ? "claude CLI: missing" : "",
    data?.model ? `model: ${data.model}` : "",
    data?.base_url ? `base: ${data.base_url}` : "",
    data?.detail ? `detail: ${data.detail}` : "",
    data?.latency_ms ? `${data.latency_ms}ms` : "",
  ]
    .filter(Boolean)
    .join("\n");

  return (
    <span
      className={
        "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs " +
        meta.className
      }
      title={title}
    >
      <span className={"inline-block h-2 w-2 rounded-full " + meta.dotClass} />
      {!compact && (
        <>
          <span className="font-mono">executor</span>
          <span>{meta.label}</span>
        </>
      )}
    </span>
  );
}

const STATUS_META: Record<
  LLMRunnerStatus,
  { label: string; className: string; dotClass: string }
> = {
  ready: {
    label: "ready",
    className: "border-emerald-200 bg-emerald-50 text-emerald-700",
    dotClass: "bg-emerald-500",
  },
  starting: {
    label: "starting",
    className: "border-amber-200 bg-amber-50 text-amber-700",
    dotClass: "bg-amber-500 animate-pulse",
  },
  down: {
    label: "down",
    className: "border-red-200 bg-red-50 text-red-700",
    dotClass: "bg-red-500",
  },
  unknown: {
    label: "unknown",
    className: "border-slate-200 bg-slate-50 text-slate-500",
    dotClass: "bg-slate-400",
  },
};

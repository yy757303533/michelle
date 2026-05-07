/**
 * Polls the backend's resolved execution-loop status.
 *
 * The "Run case" button uses this to grey itself out when the proxy isn't
 * ready — safe to run cases
 * starting/down — selected loop cannot execute yet
 *
 * status meanings:
 *   ready    — selected loop is ready
 *   starting — selected loop is probably booting
 *   down     — selected loop is unavailable / misconfigured
 *   unknown  — endpoint unreachable for an unrelated reason; treat as ready
 *              and let the actual run surface the failure
 */
import { useQuery } from "@tanstack/react-query";

export type LLMRunnerStatus = "ready" | "starting" | "down" | "unknown";

export interface LLMRunnerStatusResponse {
  status: LLMRunnerStatus;
  mode?:
    | "generic_openai"
    | "claude_cli"
    | "claude_cli_subscription"
    | "unavailable";
  configured_loop?: "auto" | "generic_openai" | "claude_cli";
  resolved_loop?: "generic_openai" | "claude_cli" | null;
  generic_available?: boolean;
  generic_providers?: string[];
  claude_cli_available?: boolean;
  npx_available?: boolean;
  base_url: string;
  model?: string;
  detail: string;
  latency_ms: number;
}

export function useLLMRunnerStatus() {
  return useQuery<LLMRunnerStatusResponse>({
    queryKey: ["llm-runner-status"],
    queryFn: async () => {
      try {
        const r = await fetch("/api/llm/runner_status");
        if (!r.ok) {
          return {
            status: "unknown" as const,
            base_url: "",
            detail: `HTTP ${r.status}`,
            latency_ms: 0,
          };
        }
        const j = await r.json();
        return j.data as LLMRunnerStatusResponse;
      } catch {
        // Backend itself is down -- can't tell anything about the LLM
        // proxy from here. Don't block the Run button on this; the
        // backend down state will manifest elsewhere (top-level Backend
        // health pill on the dashboard).
        return {
          status: "unknown" as const,
          base_url: "",
          detail: "backend unreachable",
          latency_ms: 0,
        };
      }
    },
    // Poll every 5s. Cheap probe (2s timeout, no LLM call) so safe to be
    // chatty. Tighter than the 30s default keeps the indicator current
    // when the user is staring at the dashboard waiting for boot to finish.
    refetchInterval: 5000,
    refetchIntervalInBackground: false,
    staleTime: 4000,
  });
}

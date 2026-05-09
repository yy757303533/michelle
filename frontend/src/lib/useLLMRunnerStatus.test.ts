import { describe, expect, it } from "vitest";

import { unknownRunnerStatus } from "./useLLMRunnerStatus";

describe("unknownRunnerStatus", () => {
  it("returns the non-blocking fallback shape used by run buttons", () => {
    expect(unknownRunnerStatus("backend unreachable")).toEqual({
      status: "unknown",
      base_url: "",
      detail: "backend unreachable",
      latency_ms: 0,
    });
  });
});

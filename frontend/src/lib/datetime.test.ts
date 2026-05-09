import { describe, expect, it } from "vitest";

import { fmtMs } from "./datetime";

describe("fmtMs", () => {
  it("formats missing and sub-second durations", () => {
    expect(fmtMs(null)).toBe("—");
    expect(fmtMs(undefined)).toBe("—");
    expect(fmtMs(250)).toBe("250ms");
  });

  it("formats seconds and minutes", () => {
    expect(fmtMs(1500)).toBe("1.5s");
    expect(fmtMs(61_000)).toBe("1m1s");
  });
});

import { describe, expect, it } from "vitest";
import {
  parseStoredAutoGeneration,
  selectNextChapterBatch,
  serializeAutoGeneration,
} from "./prdAutoGeneration";

describe("selectNextChapterBatch", () => {
  it("returns the next sorted batch after already processed chapters", () => {
    expect(
      selectNextChapterBatch({
        selectedChapterIndices: [8, 0, 5, 3, 1, 4],
        processedChapterIndices: [0, 1, 3],
        batchSize: 2,
      }),
    ).toEqual([4, 5]);
  });

  it("deduplicates indices and stops when the queue is exhausted", () => {
    expect(
      selectNextChapterBatch({
        selectedChapterIndices: [2, 2, 3],
        processedChapterIndices: [2, 3],
        batchSize: 5,
      }),
    ).toEqual([]);
  });
});

describe("auto generation persistence", () => {
  const state = {
    active: true,
    selectedChapterIndices: [0, 1, 2, 3, 4, 5],
    processedChapterIndices: [0, 1],
    batchSize: 5,
  };

  it("round-trips valid auto generation state", () => {
    expect(parseStoredAutoGeneration(serializeAutoGeneration(state))).toEqual(state);
  });

  it("ignores malformed stored state", () => {
    expect(parseStoredAutoGeneration("{")).toBeNull();
    expect(parseStoredAutoGeneration(JSON.stringify({ active: true }))).toBeNull();
  });
});

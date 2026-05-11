import { describe, expect, it } from "vitest";
import {
  deriveHandledChapterIndices,
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

describe("deriveHandledChapterIndices", () => {
  const chapters = [
    { position: 0, level: 2, normalized_title: "overview" },
    { position: 1, level: 2, normalized_title: "settings" },
    { position: 2, level: 2, normalized_title: "metadata" },
  ];

  it("does not count historical job results after their generated cases are deleted", () => {
    expect(
      deriveHandledChapterIndices({
        chapters,
        cases: [],
        jobs: [
          {
            status: "done",
            results: [
              {
                chapter_index: 1,
                saved_case_ids: ["TC-OLD-1"],
              },
            ],
          },
        ],
        selectedChapterIndices: [0, 1, 2],
      }),
    ).toEqual([]);
  });

  it("counts current cases and non-actionable skipped chapters as handled", () => {
    expect(
      deriveHandledChapterIndices({
        chapters,
        cases: [
          {
            case_id: "TC-1",
            generated_from: "chapter:2:settings",
          },
        ],
        jobs: [
          {
            status: "done",
            results: [
              {
                chapter_index: 2,
                skipped: true,
                skip_action: "non_actionable",
              },
            ],
          },
        ],
        selectedChapterIndices: [0, 1, 2],
      }),
    ).toEqual([1, 2]);
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

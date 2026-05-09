export interface StoredAutoGenerationState {
  active: boolean;
  selectedChapterIndices: number[];
  processedChapterIndices: number[];
  batchSize: number;
}

export function selectNextChapterBatch({
  selectedChapterIndices,
  processedChapterIndices,
  batchSize,
}: {
  selectedChapterIndices: number[];
  processedChapterIndices: number[];
  batchSize: number;
}): number[] {
  if (batchSize < 1) return [];
  const processed = new Set(processedChapterIndices);
  return [...new Set(selectedChapterIndices)]
    .sort((a, b) => a - b)
    .filter((index) => !processed.has(index))
    .slice(0, batchSize);
}

function isNumberArray(value: unknown): value is number[] {
  return Array.isArray(value) && value.every((item) => Number.isInteger(item));
}

export function serializeAutoGeneration(state: StoredAutoGenerationState): string {
  return JSON.stringify(state);
}

export function parseStoredAutoGeneration(raw: string | null): StoredAutoGenerationState | null {
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as Partial<StoredAutoGenerationState>;
    const batchSize = parsed.batchSize;
    if (
      typeof parsed.active !== "boolean" ||
      !isNumberArray(parsed.selectedChapterIndices) ||
      !isNumberArray(parsed.processedChapterIndices) ||
      typeof batchSize !== "number" ||
      !Number.isInteger(batchSize) ||
      batchSize < 1
    ) {
      return null;
    }
    return {
      active: parsed.active,
      selectedChapterIndices: parsed.selectedChapterIndices,
      processedChapterIndices: parsed.processedChapterIndices,
      batchSize,
    };
  } catch {
    return null;
  }
}

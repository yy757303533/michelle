export interface StoredAutoGenerationState {
  active: boolean;
  selectedChapterIndices: number[];
  processedChapterIndices: number[];
  batchSize: number;
}

export interface AutoGenerationChapter {
  position: number;
  level: number;
  normalized_title: string;
}

export interface AutoGenerationCase {
  case_id: string;
  generated_from: string | null;
}

export interface AutoGenerationJobResult {
  chapter_index: number;
  error?: string;
  skipped?: boolean;
  skip_action?: string;
  saved_case_ids?: string[];
}

export interface AutoGenerationJob {
  status: string;
  results: AutoGenerationJobResult[];
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

export function deriveHandledChapterIndices({
  chapters,
  cases,
  jobs,
  selectedChapterIndices,
}: {
  chapters: AutoGenerationChapter[];
  cases: AutoGenerationCase[];
  jobs: AutoGenerationJob[];
  selectedChapterIndices: number[];
}): number[] {
  const selected = new Set(selectedChapterIndices);
  const existingCaseIds = new Set(cases.map((row) => row.case_id));
  const generatedFromWithCases = new Set(
    cases
      .map((row) => row.generated_from)
      .filter((value): value is string => Boolean(value)),
  );
  const handled = new Set<number>();

  for (const chapter of chapters) {
    if (!selected.has(chapter.position)) continue;
    const signature = `chapter:${chapter.level}:${chapter.normalized_title}`;
    if (generatedFromWithCases.has(signature)) handled.add(chapter.position);
  }

  for (const job of jobs) {
    if (job.status !== "done") continue;
    for (const result of job.results) {
      if (!selected.has(result.chapter_index) || result.error) continue;
      if (result.skipped && result.skip_action === "non_actionable") {
        handled.add(result.chapter_index);
        continue;
      }
      if (result.saved_case_ids?.some((caseId) => existingCaseIds.has(caseId))) {
        handled.add(result.chapter_index);
      }
    }
  }

  return selectedChapterIndices.filter((index) => handled.has(index));
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

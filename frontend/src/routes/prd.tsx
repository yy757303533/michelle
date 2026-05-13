import { createFileRoute, Link } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useCurrentProject } from "../lib/useCurrentProject";
import { ProjectTargetBadge } from "../components/ProjectTargetBadge";
import { apiFetch } from "../lib/adminAuth";
import { parseMarkdownPreview, type MarkdownBlock } from "../lib/markdownPreview";
import {
  deriveCoveredChapterIndices,
  deriveHandledChapterIndices as deriveHandledChapterIndicesFromState,
  parseStoredAutoGeneration,
  selectNextChapterBatch,
  serializeAutoGeneration,
  type StoredAutoGenerationState,
} from "../lib/prdAutoGeneration";

const PRD_URL_KEY = "prd_id";
const CHAPTERS_PER_RUN_KEY = "prd_chapters_per_run";
const GENERATION_TIMEOUT_KEY = "prd_analysis_timeout_seconds";
const OUTPUT_LANGUAGE_KEY = "prd_analysis_output_language";
const AUTO_GENERATION_KEY_PREFIX = "prd_auto_generation";
const RECOMMENDED_CHAPTERS_PER_RUN = 5;
const MAX_CHAPTERS_PER_RUN = 10;
const DEFAULT_GENERATION_TIMEOUT_SECONDS = 180;
type OutputLanguage = "zh" | "en" | "auto";

/** Read `?prd_id=` from the URL without going through the router so we
 * don't have to declare a search schema on the file route. */
function readPrdIdFromUrl(): string {
  if (typeof window === "undefined") return "";
  return new URLSearchParams(window.location.search).get(PRD_URL_KEY) ?? "";
}

function writePrdIdToUrl(prdId: string) {
  if (typeof window === "undefined") return;
  const url = new URL(window.location.href);
  if (prdId) url.searchParams.set(PRD_URL_KEY, prdId);
  else url.searchParams.delete(PRD_URL_KEY);
  window.history.replaceState({}, "", url.toString());
}

function readChaptersPerRun(): string {
  if (typeof window === "undefined") return String(RECOMMENDED_CHAPTERS_PER_RUN);
  const stored = Number.parseInt(window.localStorage.getItem(CHAPTERS_PER_RUN_KEY) ?? "", 10);
  if (!Number.isFinite(stored) || stored < 1) return String(RECOMMENDED_CHAPTERS_PER_RUN);
  return String(Math.min(MAX_CHAPTERS_PER_RUN, stored));
}

function readGenerationTimeout(): string {
  if (typeof window === "undefined") return String(DEFAULT_GENERATION_TIMEOUT_SECONDS);
  return (
    window.localStorage.getItem(GENERATION_TIMEOUT_KEY) ??
    String(DEFAULT_GENERATION_TIMEOUT_SECONDS)
  );
}

function readOutputLanguage(): OutputLanguage {
  if (typeof window === "undefined") return "zh";
  const stored = window.localStorage.getItem(OUTPUT_LANGUAGE_KEY);
  return stored === "en" || stored === "auto" || stored === "zh" ? stored : "zh";
}

function autoGenerationStorageKey(prdId: string): string {
  return `${AUTO_GENERATION_KEY_PREFIX}:${prdId}`;
}

function readStoredAutoGeneration(prdId: string): StoredAutoGenerationState | null {
  if (typeof window === "undefined") return null;
  return parseStoredAutoGeneration(window.localStorage.getItem(autoGenerationStorageKey(prdId)));
}

function writeStoredAutoGeneration(prdId: string, state: StoredAutoGenerationState | null) {
  if (typeof window === "undefined") return;
  const key = autoGenerationStorageKey(prdId);
  if (state) window.localStorage.setItem(key, serializeAutoGeneration(state));
  else window.localStorage.removeItem(key);
}

function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return "0s";
  const whole = Math.round(seconds);
  if (whole < 60) return `${whole}s`;
  const minutes = Math.floor(whole / 60);
  const rest = whole % 60;
  return rest ? `${minutes}m ${rest}s` : `${minutes}m`;
}

export const Route = createFileRoute("/prd")({
  component: PrdPage,
});

interface ChapterMeta {
  position: number;
  level: number;
  title: string;
  normalized_title: string;
  hash: string;
  body_chars: number;
  body?: string;
}

interface UploadResponse {
  data: {
    prd_id: string;
    version: number;
    title: string;
    raw_markdown?: string;
    chapters: ChapterMeta[];
    prior_version_id: string | null;
    diff_summary: Record<string, number> | null;
  };
}

interface ChapterResult {
  chapter_index: number;
  chapter_title: string;
  saved_count: number;
  saved_case_ids?: string[];
  coverage_notes?: string;
  error?: string;
  skipped?: boolean;
  skip_reason?: string;
  batch_id?: string;
  batch_latency_seconds?: number;
}

interface GenerationProgress {
  active_batches?: Array<{
    batch_id: string;
    chapter_indices: number[];
    chapter_titles: string[];
    target_cases: number;
  }>;
  completed_batches?: number;
  total_batches?: number;
  parallelism?: number;
  avg_batch_latency_seconds?: number | null;
  eta_seconds?: number | null;
  skip_requested?: boolean;
  last_batch?: {
    batch_id: string;
    chapter_indices: number[];
    chapter_titles: string[];
    target_cases: number;
    status?: string;
    latency_seconds?: number;
    error?: string | null;
  };
}

/** POST /analyze returns the created requirement and coverage ids. */
interface GenerateAcceptResponse {
  data: {
    prd_id: string;
    project_id: string;
    requirements_created: number;
    coverage_created: number;
    coverage_replaced?: number;
    requirement_ids: string[];
    coverage_ids: string[];
  };
}

interface GenerationJob {
  data: {
    job_id: string;
    prd_id: string;
    project_id: string;
    status: "pending" | "running" | "done" | "failed" | "cancelled";
    total_chapters: number;
    completed_chapters: number;
    saved_cases: number;
    results: ChapterResult[];
    request_payload: {
      progress?: GenerationProgress;
      parallelism?: number;
      [key: string]: unknown;
    };
    error: string | null;
    started_at: string | null;
    finished_at: string | null;
  };
}

interface GenerationJobsResponse {
  data: GenerationJob["data"][];
}

type AutoGenerationState = StoredAutoGenerationState;
type PrdViewTab = "chapters" | "preview" | "raw";

interface GenerateVariables {
  chapterIndices: number[];
  selectedChapterIndices?: number[];
  batchSize?: number;
}

interface PRDListItem {
  prd_id: string;
  project_id: string;
  name: string;
  version: number;
  chapter_count: number;
  uploaded_at: string;
}

interface RuntimeKnob<T = string> {
  value: T;
  default: T;
  min?: number;
  max?: number;
  choices?: string[];
  describe: string;
}

interface RuntimeSettingsResponse {
  data: {
    test_design_provider: RuntimeKnob<string>;
    test_design_preflight_timeout_seconds: RuntimeKnob<number>;
    case_drafting_provider: RuntimeKnob<string>;
  };
}

interface CoverageRow {
  coverage_id: string;
  project_id: string;
  prd_id: string;
  requirement_id: string;
  chapter_index: number;
  risk_type: string;
  coverage_type: string;
  title: string;
  scenario: string;
  rationale: string;
  priority: string;
  review_status: "proposed" | "accepted" | "rejected" | "stale";
  linked_case_id: string | null;
}

function PrdPage() {
  const qc = useQueryClient();
  // Project comes from the global header selector — single source of truth.
  // The page no longer owns a `projectId` state; that lived here only because
  // the original UX hard-coded "michelle".
  const { projectId } = useCurrentProject();
  const [name, setName] = useState("");
  const [markdown, setMarkdown] = useState("");
  const [uploaded, setUploaded] = useState<UploadResponse["data"] | null>(null);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [autoGeneration, setAutoGeneration] = useState<AutoGenerationState | null>(null);
  const handledTerminalJobIds = useRef<Set<string>>(new Set());
  // `?prd_id=` survives navigation away and back. Without it, the
  // user uploads a 90-chapter PRD, hops to /cases to check a result,
  // returns, and the chapter list is gone — all transient state
  // disappeared with the unmounted component. Persisting just the id
  // is enough: the chapters live on the server, we re-fetch on mount.
  const [activePrdId, setActivePrdIdState] = useState<string>(readPrdIdFromUrl);
  const [preflightTimeoutInput, setPreflightTimeoutInput] = useState("20");
  const [chaptersPerRunInput, setChaptersPerRunInput] = useState(readChaptersPerRun);
  const [generationTimeoutInput, setGenerationTimeoutInput] =
    useState(readGenerationTimeout);
  const [outputLanguage, setOutputLanguage] = useState<OutputLanguage>(readOutputLanguage);
  const [prdViewTab, setPrdViewTab] = useState<PrdViewTab>("chapters");

  const setActivePrdId = (id: string) => {
    setActivePrdIdState(id);
    writePrdIdToUrl(id);
  };

  const list = useQuery({
    queryKey: ["prd-list", projectId],
    enabled: Boolean(projectId),
    queryFn: async (): Promise<{ data: PRDListItem[] }> => {
      const r = await apiFetch(`/api/prd/?project_id=${encodeURIComponent(projectId)}`);
      return r.json();
    },
  });

  const runtimeSettings = useQuery({
    queryKey: ["runtime-settings"],
    queryFn: async (): Promise<RuntimeSettingsResponse> => {
      const r = await apiFetch("/api/settings/runtime");
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    },
  });

  const coverage = useQuery({
    queryKey: ["coverage", uploaded?.prd_id],
    enabled: Boolean(uploaded?.prd_id),
    refetchInterval: () => (autoGeneration?.active ? 5000 : false),
    queryFn: async (): Promise<{ data: CoverageRow[]; count: number }> => {
      if (!uploaded) throw new Error("upload first");
      const r = await apiFetch(`/api/coverage/?prd_id=${encodeURIComponent(uploaded.prd_id)}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    },
  });

  useEffect(() => {
    const value = runtimeSettings.data?.data.test_design_preflight_timeout_seconds.value;
    if (typeof value === "number") setPreflightTimeoutInput(String(value));
  }, [runtimeSettings.data]);

  async function updateRuntimeTimeout(seconds: number): Promise<RuntimeSettingsResponse> {
    const r = await apiFetch("/api/settings/runtime", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ test_design_preflight_timeout_seconds: seconds }),
    });
    if (!r.ok) throw new Error(await r.text());
    const data = (await r.json()) as RuntimeSettingsResponse;
    qc.setQueryData(["runtime-settings"], data);
    setPreflightTimeoutInput(String(data.data.test_design_preflight_timeout_seconds.value));
    return data;
  }

  function parseTimeoutInput(): number {
    const raw = Number.parseInt(preflightTimeoutInput, 10);
    const spec = runtimeSettings.data?.data.test_design_preflight_timeout_seconds;
    const min = spec?.min ?? 5;
    const max = spec?.max ?? 300;
    if (!Number.isFinite(raw) || raw < min || raw > max) {
      throw new Error(`timeout must be between ${min}s and ${max}s`);
    }
    return raw;
  }

  async function saveTimeoutIfChanged(): Promise<number> {
    const seconds = parseTimeoutInput();
    const current =
      runtimeSettings.data?.data.test_design_preflight_timeout_seconds.value;
    if (current !== seconds) await updateRuntimeTimeout(seconds);
    return seconds;
  }

  const saveTimeout = useMutation({
    mutationFn: updateRuntimeTimeout,
    onError: () => {
      const value = runtimeSettings.data?.data.test_design_preflight_timeout_seconds.value;
      if (typeof value === "number") setPreflightTimeoutInput(String(value));
    },
  });

  function persistTimeoutFromInput() {
    try {
      const seconds = parseTimeoutInput();
      if (
        seconds !== runtimeSettings.data?.data.test_design_preflight_timeout_seconds.value
      ) {
        saveTimeout.mutate(seconds);
      }
    } catch {
      const value = runtimeSettings.data?.data.test_design_preflight_timeout_seconds.value;
      setPreflightTimeoutInput(String(value ?? 20));
    }
  }

  function parseChaptersPerRunInput(): number {
    const raw = Number.parseInt(chaptersPerRunInput, 10);
    if (!Number.isFinite(raw) || raw < 1 || raw > MAX_CHAPTERS_PER_RUN) {
      throw new Error(`chapters per run must be between 1 and ${MAX_CHAPTERS_PER_RUN}`);
    }
    return raw;
  }

  function persistChaptersPerRunFromInput() {
    try {
      const value = parseChaptersPerRunInput();
      setChaptersPerRunInput(String(value));
      window.localStorage.setItem(CHAPTERS_PER_RUN_KEY, String(value));
    } catch {
      setChaptersPerRunInput(String(RECOMMENDED_CHAPTERS_PER_RUN));
      window.localStorage.setItem(
        CHAPTERS_PER_RUN_KEY,
        String(RECOMMENDED_CHAPTERS_PER_RUN),
      );
    }
  }

  function parseGenerationTimeoutInput(): number {
    const raw = Number.parseInt(generationTimeoutInput, 10);
    if (!Number.isFinite(raw) || raw < 30 || raw > 1800) {
      throw new Error("batch timeout must be between 30s and 1800s");
    }
    return raw;
  }

  function generationTimeoutSeconds(): number {
    try {
      return parseGenerationTimeoutInput();
    } catch {
      return DEFAULT_GENERATION_TIMEOUT_SECONDS;
    }
  }

  function persistGenerationTimeoutFromInput() {
    try {
      const value = parseGenerationTimeoutInput();
      setGenerationTimeoutInput(String(value));
      window.localStorage.setItem(GENERATION_TIMEOUT_KEY, String(value));
    } catch {
      setGenerationTimeoutInput(String(DEFAULT_GENERATION_TIMEOUT_SECONDS));
      window.localStorage.setItem(
        GENERATION_TIMEOUT_KEY,
        String(DEFAULT_GENERATION_TIMEOUT_SECONDS),
      );
    }
  }

  function updateOutputLanguage(value: OutputLanguage) {
    setOutputLanguage(value);
    window.localStorage.setItem(OUTPUT_LANGUAGE_KEY, value);
  }

  // Cases for the current project, used to overlay "✓ N cases generated"
  // per chapter so the user can see what was produced even after navigating
  // away mid-generation. Polls every 5s while a PRD is open so background
  // generation progress shows up live.
  interface CaseRow {
    case_id: string;
    generated_from: string | null;
    review_status: string;
  }
  const projectCases = useQuery({
    queryKey: ["cases-for-prd-overlay", projectId],
    enabled: Boolean(projectId && uploaded),
    refetchInterval: 5000,
    queryFn: async (): Promise<{ data: CaseRow[] }> => {
      const r = await apiFetch(
        `/api/cases/?project_id=${encodeURIComponent(projectId)}&limit=1000`,
      );
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    },
  });

  // Re-hydrate `uploaded` whenever `activePrdId` changes (mount, URL deep
  // link, history-table click). Doesn't run unless we have an id, so the
  // "upload first" empty state stays clean.
  const hydrate = useQuery({
    queryKey: ["prd-detail", activePrdId],
    enabled: Boolean(activePrdId),
    queryFn: async (): Promise<UploadResponse> => {
      const r = await apiFetch(`/api/prd/${activePrdId}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    },
  });

  useEffect(() => {
    if (hydrate.data) {
      setUploaded(hydrate.data.data);
      setSelected(new Set(hydrate.data.data.chapters.map((c) => c.position)));
      setPrdViewTab("chapters");
      setActiveJobId(null);
      setAutoGeneration(readStoredAutoGeneration(hydrate.data.data.prd_id));
      handledTerminalJobIds.current.clear();
    }
    if (!activePrdId) {
      setUploaded(null);
      setSelected(new Set());
      setActiveJobId(null);
      setAutoGeneration(null);
      if (uploaded?.prd_id) writeStoredAutoGeneration(uploaded.prd_id, null);
      handledTerminalJobIds.current.clear();
    }
  }, [hydrate.data, activePrdId, uploaded?.prd_id]);

  const upload = useMutation({
    mutationFn: async (): Promise<UploadResponse> => {
      const r = await apiFetch("/api/prd/upload", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          project_id: projectId,
          name: name || undefined,
          markdown,
        }),
      });
      if (!r.ok) throw new Error(await r.text());
      return r.json();
    },
    onSuccess: (resp) => {
      setUploaded(resp.data);
      setPrdViewTab("chapters");
      setActiveJobId(null);
      setAutoGeneration(null);
      writeStoredAutoGeneration(resp.data.prd_id, null);
      handledTerminalJobIds.current.clear();
      setSelected(new Set(resp.data.chapters.map((c) => c.position)));
      setActivePrdId(resp.data.prd_id);
      qc.invalidateQueries({ queryKey: ["prd-list"] });
    },
  });

  const deletePrd = useMutation({
    mutationFn: async (prdId: string) => {
      const r = await apiFetch(`/api/prd/${prdId}`, { method: "DELETE" });
      if (!r.ok && r.status !== 204) throw new Error(`HTTP ${r.status}: ${await r.text()}`);
    },
    onSuccess: (_void, prdId) => {
      // If the deleted row was the active one, clear the page state.
      if (prdId === activePrdId) setActivePrdId("");
      qc.invalidateQueries({ queryKey: ["prd-list"] });
      qc.invalidateQueries({ queryKey: ["prd-detail"] });
    },
  });

  const generate = useMutation({
    mutationFn: async ({ chapterIndices }: GenerateVariables): Promise<GenerateAcceptResponse> => {
      if (!uploaded) throw new Error("upload first");
      await saveTimeoutIfChanged();
      if (chapterIndices.length === 0) throw new Error("no chapters queued");
      const r = await apiFetch(`/api/prd/${uploaded.prd_id}/analyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          chapter_indices: chapterIndices,
          output_language: outputLanguage,
        }),
      });
      if (!r.ok) throw new Error(await r.text());
      return r.json();
    },
    onSuccess: (resp, variables) => {
      setActiveJobId(null);
      qc.invalidateQueries({ queryKey: ["coverage"] });
      qc.invalidateQueries({ queryKey: ["coverage", resp.data.prd_id] });
      qc.invalidateQueries({ queryKey: ["coverage-workbench"] });
      qc.invalidateQueries({ queryKey: ["cases"] });
      qc.invalidateQueries({ queryKey: ["cases-summary"] });

      const selectedChapterIndices = variables.selectedChapterIndices;
      const batchSize = variables.batchSize;
      if (!uploaded || !selectedChapterIndices || !batchSize) {
        setAutoGeneration(null);
        if (uploaded) writeStoredAutoGeneration(uploaded.prd_id, null);
        return;
      }

      const previousProcessed = autoGeneration?.processedChapterIndices ?? [];
      const processedChapterIndices = [
        ...new Set([...previousProcessed, ...variables.chapterIndices]),
      ].sort((a, b) => a - b);
      const nextBatch = selectNextChapterBatch({
        selectedChapterIndices,
        processedChapterIndices,
        batchSize,
      });
      const nextState = {
        active: nextBatch.length > 0,
        selectedChapterIndices,
        processedChapterIndices,
        batchSize,
        inFlightChapterIndices: nextBatch.length > 0 ? nextBatch : undefined,
        inFlightStartedAt: nextBatch.length > 0 ? Date.now() : undefined,
      };
      setAutoGeneration(nextBatch.length > 0 ? nextState : null);
      writeStoredAutoGeneration(uploaded.prd_id, nextBatch.length > 0 ? nextState : null);
      if (nextBatch.length > 0) {
        generate.mutate({ chapterIndices: nextBatch, selectedChapterIndices, batchSize });
      }
    },
    onError: () => {
      setAutoGeneration(null);
      if (uploaded) writeStoredAutoGeneration(uploaded.prd_id, null);
    },
  });

  const cancelJob = useMutation({
    mutationFn: async (jobId: string): Promise<GenerationJob> => {
      const r = await apiFetch(`/api/prd/jobs/${jobId}/cancel`, { method: "POST" });
      if (!r.ok) throw new Error(await r.text());
      return r.json();
    },
    onSuccess: (resp) => {
      setActiveJobId(resp.data.job_id);
      qc.invalidateQueries({ queryKey: ["prd-job", resp.data.job_id] });
      qc.invalidateQueries({ queryKey: ["prd-jobs", resp.data.prd_id] });
    },
  });

  const skipCurrentBatch = useMutation({
    mutationFn: async (jobId: string): Promise<GenerationJob> => {
      const r = await apiFetch(`/api/prd/jobs/${jobId}/skip-current`, { method: "POST" });
      if (!r.ok) throw new Error(await r.text());
      return r.json();
    },
    onSuccess: (resp) => {
      setActiveJobId(resp.data.job_id);
      qc.invalidateQueries({ queryKey: ["prd-job", resp.data.job_id] });
      qc.invalidateQueries({ queryKey: ["prd-jobs", resp.data.prd_id] });
    },
  });

  function deriveHandledChapterIndices(selectedChapterIndices: number[]): number[] {
    if (!uploaded) return [];
    return deriveHandledChapterIndicesFromState({
      chapters: uploaded.chapters,
      cases: projectCases.data?.data ?? [],
      jobs: jobs.data?.data ?? [],
      selectedChapterIndices,
    });
  }

  function startAutoGeneration() {
    if (!uploaded) return;
    const selectedChapterIndices = [...selected].sort((a, b) => a - b);
    setAutoGeneration(null);
    writeStoredAutoGeneration(uploaded.prd_id, null);
    handledTerminalJobIds.current.clear();
    if (selectedChapterIndices.length > 0) {
      const batchSize = chaptersPerRun;
      const nextBatch = selectNextChapterBatch({
        selectedChapterIndices,
        processedChapterIndices: [],
        batchSize,
      });
      const nextState = {
        active: nextBatch.length > 0,
        selectedChapterIndices,
        processedChapterIndices: [],
        batchSize,
        inFlightChapterIndices: nextBatch,
        inFlightStartedAt: Date.now(),
      };
      setAutoGeneration(nextState);
      writeStoredAutoGeneration(uploaded.prd_id, nextState);
      generate.mutate({ chapterIndices: nextBatch, selectedChapterIndices, batchSize });
    }
  }

  function cancelAutoGeneration(jobId: string) {
    setAutoGeneration((state) => {
      const nextState = state ? { ...state, active: false } : null;
      if (uploaded) writeStoredAutoGeneration(uploaded.prd_id, nextState);
      return nextState;
    });
    cancelJob.mutate(jobId);
  }

  const jobs = useQuery({
    queryKey: ["prd-jobs", uploaded?.prd_id],
    enabled: false,
    refetchInterval: (q) => {
      const rows = (q.state.data as GenerationJobsResponse | undefined)?.data ?? [];
      return rows.some((j) => j.status === "pending" || j.status === "running")
        ? 2000
        : 5000;
    },
    queryFn: async (): Promise<GenerationJobsResponse> => {
      if (!uploaded) throw new Error("upload first");
      const r = await apiFetch(`/api/prd/${uploaded.prd_id}/jobs`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    },
  });

  const activeServerJob =
    jobs.data?.data.find((j) => j.status === "pending" || j.status === "running") ?? null;

  useEffect(() => {
    if (activeServerJob && activeServerJob.job_id !== activeJobId) {
      setActiveJobId(activeServerJob.job_id);
    }
  }, [activeServerJob, activeJobId]);

  useEffect(() => {
    if (!uploaded || !activeServerJob || autoGeneration) return;
    const selectedChapterIndices = [...selected].sort((a, b) => a - b);
    if (selectedChapterIndices.length <= activeServerJob.total_chapters) return;
    const restoredState = {
      active: true,
      selectedChapterIndices,
      processedChapterIndices: deriveHandledChapterIndices(selectedChapterIndices),
      batchSize: Math.max(1, activeServerJob.total_chapters),
    };
    setAutoGeneration(restoredState);
    writeStoredAutoGeneration(uploaded.prd_id, restoredState);
  }, [activeServerJob, autoGeneration, selected, uploaded]);

  useEffect(() => {
    if (!uploaded || !autoGeneration || !projectCases.data || !jobs.data) return;
    const processedChapterIndices = deriveHandledChapterIndices(
      autoGeneration.selectedChapterIndices,
    );
    const current = autoGeneration.processedChapterIndices.join(",");
    const next = processedChapterIndices.join(",");
    if (current === next) return;
    const nextState = { ...autoGeneration, processedChapterIndices };
    setAutoGeneration(nextState);
    writeStoredAutoGeneration(uploaded.prd_id, nextState.active ? nextState : null);
  }, [autoGeneration, jobs.data, projectCases.data, uploaded]);

  /** Poll the active generation job until it reaches a terminal state. */
  const job = useQuery({
    queryKey: ["prd-job", activeJobId],
    enabled: false,
    refetchInterval: (q) => {
      const status = (q.state.data as GenerationJob | undefined)?.data?.status;
      return status === "done" || status === "failed" || status === "cancelled" ? false : 2000;
    },
    queryFn: async (): Promise<GenerationJob> => {
      const r = await apiFetch(`/api/prd/jobs/${activeJobId}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    },
  });

  const effectiveJob = job.data?.data ?? activeServerJob;
  const isAutoGenerationActive =
    generate.isPending ||
    Boolean(autoGeneration?.active) ||
    effectiveJob?.status === "pending" ||
    effectiveJob?.status === "running";

  useEffect(() => {
    if (!uploaded || !autoGeneration?.active || !coverage.data || generate.isPending) return;
    if (effectiveJob?.status === "pending" || effectiveJob?.status === "running") return;

    const coveredChapterIndices = deriveCoveredChapterIndices({
      coverage: coverage.data.data,
      selectedChapterIndices: autoGeneration.selectedChapterIndices,
    });
    const processedChapterIndices = [
      ...new Set([
        ...autoGeneration.processedChapterIndices,
        ...coveredChapterIndices,
      ]),
    ].sort((a, b) => a - b);
    const processed = new Set(processedChapterIndices);
    const inFlightChapterIndices = autoGeneration.inFlightChapterIndices ?? [];
    const inFlightStartedAt = autoGeneration.inFlightStartedAt ?? 0;
    const inFlightCovered =
      inFlightChapterIndices.length > 0 &&
      inFlightChapterIndices.every((index) => processed.has(index));
    const inFlightExpired =
      inFlightChapterIndices.length > 0 &&
      Date.now() - inFlightStartedAt > generationTimeoutSeconds() * 1000 + 5000;

    if (inFlightChapterIndices.length > 0 && !inFlightCovered && !inFlightExpired) {
      if (
        processedChapterIndices.join(",") !==
        autoGeneration.processedChapterIndices.join(",")
      ) {
        const nextState = {
          ...autoGeneration,
          processedChapterIndices,
        };
        setAutoGeneration(nextState);
        writeStoredAutoGeneration(uploaded.prd_id, nextState);
      }
      return;
    }

    const nextBatch = selectNextChapterBatch({
      selectedChapterIndices: autoGeneration.selectedChapterIndices,
      processedChapterIndices,
      batchSize: autoGeneration.batchSize,
    });
    if (nextBatch.length === 0) {
      setAutoGeneration(null);
      writeStoredAutoGeneration(uploaded.prd_id, null);
      return;
    }

    const nextState = {
      ...autoGeneration,
      active: true,
      processedChapterIndices,
      inFlightChapterIndices: nextBatch,
      inFlightStartedAt: Date.now(),
    };
    setAutoGeneration(nextState);
    writeStoredAutoGeneration(uploaded.prd_id, nextState);
    generate.mutate({
      chapterIndices: nextBatch,
      selectedChapterIndices: autoGeneration.selectedChapterIndices,
      batchSize: autoGeneration.batchSize,
    });
  }, [autoGeneration, coverage.data, effectiveJob?.status, generate.isPending, uploaded]);

  useEffect(() => {
    if (!autoGeneration?.active || !effectiveJob) return;
    if (
      effectiveJob.status !== "done" &&
      effectiveJob.status !== "failed" &&
      effectiveJob.status !== "cancelled"
    ) {
      return;
    }
    if (handledTerminalJobIds.current.has(effectiveJob.job_id)) return;
    handledTerminalJobIds.current.add(effectiveJob.job_id);

    if (effectiveJob.status !== "done") {
      setAutoGeneration((state) => {
        const nextState = state ? { ...state, active: false } : null;
        if (uploaded) writeStoredAutoGeneration(uploaded.prd_id, nextState);
        return nextState;
      });
      return;
    }

    const processedChapterIndices = [
      ...new Set([
        ...autoGeneration.processedChapterIndices,
        ...effectiveJob.results.map((result) => result.chapter_index),
      ]),
    ];
    const nextBatch = selectNextChapterBatch({
      selectedChapterIndices: autoGeneration.selectedChapterIndices,
      processedChapterIndices,
      batchSize: autoGeneration.batchSize,
    });
    const nextState = {
      ...autoGeneration,
      active: nextBatch.length > 0,
      processedChapterIndices,
      inFlightChapterIndices: nextBatch.length > 0 ? nextBatch : undefined,
      inFlightStartedAt: nextBatch.length > 0 ? Date.now() : undefined,
    };
    setAutoGeneration(nextState);
    if (uploaded) {
      writeStoredAutoGeneration(uploaded.prd_id, nextBatch.length > 0 ? nextState : null);
    }
    if (nextBatch.length > 0) {
      generate.mutate({
        chapterIndices: nextBatch,
        selectedChapterIndices: autoGeneration.selectedChapterIndices,
        batchSize: autoGeneration.batchSize,
      });
    }
  }, [autoGeneration, effectiveJob, generate, uploaded]);

  const chaptersPerRun = (() => {
    const parsed = Number.parseInt(chaptersPerRunInput, 10);
    if (!Number.isFinite(parsed) || parsed < 1) return RECOMMENDED_CHAPTERS_PER_RUN;
    return Math.min(MAX_CHAPTERS_PER_RUN, parsed);
  })();
  const runChapterCount = Math.min(selected.size, chaptersPerRun);
  const autoProgress = (() => {
    if (!autoGeneration) return null;
    const currentJobIndices =
      effectiveJob?.status === "pending" || effectiveJob?.status === "running"
        ? effectiveJob.results.map((result) => result.chapter_index)
        : [];
    const processed = new Set([
      ...autoGeneration.processedChapterIndices,
      ...currentJobIndices,
    ]);
    return {
      total: autoGeneration.selectedChapterIndices.length,
      processed: Math.min(processed.size, autoGeneration.selectedChapterIndices.length),
      active: autoGeneration.active,
    };
  })();

  const toggleChapter = (pos: number) => {
    const next = new Set(selected);
    if (next.has(pos)) next.delete(pos);
    else next.add(pos);
    setSelected(next);
  };

  /** Map case provenance → count of drafted cases. New drafts are linked
   * through coverage ids, while older imported data may still carry chapter
   * signatures from the retired flow. */
  const casesByChapter = (() => {
    const out: Record<string, { total: number; pending: number; approved: number }> = {};
    if (!projectCases.data || !uploaded) return out;
    for (const c of projectCases.data.data) {
      if (!c.generated_from) continue;
      const bucket = (out[c.generated_from] ??= { total: 0, pending: 0, approved: 0 });
      bucket.total += 1;
      if (c.review_status === "pending") bucket.pending += 1;
      else if (c.review_status === "approved") bucket.approved += 1;
    }
    return out;
  })();

  const chapterCount = (chapter: ChapterMeta): { total: number; pending: number; approved: number } => {
    const sig = `chapter:${chapter.level}:${chapter.normalized_title}`;
    return casesByChapter[sig] ?? { total: 0, pending: 0, approved: 0 };
  };

  const totalCasesForPrd = uploaded
    ? uploaded.chapters.reduce((sum, c) => sum + chapterCount(c).total, 0)
    : 0;

  const onFilePicked = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (!f) return;
    // Cap at 2 MB so a stray binary doesn't lock the browser; PRDs are
    // markdown text so the cap is generous.
    if (f.size > 2 * 1024 * 1024) {
      alert(`file too large (${(f.size / 1024 / 1024).toFixed(1)} MB) — paste contents instead`);
      e.target.value = "";
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      const text = typeof reader.result === "string" ? reader.result : "";
      setMarkdown(text);
      // If `name` is empty, default to filename without extension so the
      // user doesn't have to type it.
      if (!name) {
        const stem = f.name.replace(/\.(md|markdown|txt)$/i, "");
        setName(stem);
      }
    };
    reader.readAsText(f);
    // Reset input so picking the same file again still triggers onChange.
    e.target.value = "";
  };

  const loadUploadedIntoEditor = () => {
    if (!uploaded?.raw_markdown) return;
    setMarkdown(uploaded.raw_markdown);
    setName(uploaded.title);
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">
          PRD ingest
          {projectId && (
            <span className="text-slate-400 text-base font-normal"> / {projectId}</span>
          )}
        </h1>
        {projectId && (
          <div className="mt-1">
            <ProjectTargetBadge projectId={projectId} />
          </div>
        )}
        <p className="text-slate-500 text-sm mt-1">
          Paste markdown, see chapters detected, then analyze selected chapters into reviewable coverage.
        </p>
      </div>

      {!projectId ? (
        <div className="bg-white border border-slate-200 rounded-lg p-8 text-center text-sm text-slate-500">
          Pick a project from the header dropdown to upload PRDs.
        </div>
      ) : null}

      {/* Upload form */}
      <div className={"bg-white border border-slate-200 rounded-lg p-4 space-y-3 " + (projectId ? "" : "opacity-50 pointer-events-none")}>
        <div className="text-xs text-slate-500">
          uploading to project <code className="font-mono">{projectId || "—"}</code>
        </div>
        <Field label="name (optional, defaults to first H1)">
          <input
            className="border border-slate-200 rounded px-2 py-1 w-full text-sm"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Michelle PRD v0.5"
          />
        </Field>
        <Field label="markdown">
          <div className="flex items-center gap-2 mb-1.5 text-xs text-slate-500">
            <label className="bg-slate-100 hover:bg-slate-200 text-slate-700 px-2 py-0.5 rounded cursor-pointer">
              📄 choose .md file
              <input
                type="file"
                accept=".md,.markdown,.txt,text/markdown,text/plain"
                onChange={onFilePicked}
                className="hidden"
              />
            </label>
            <span>or paste contents below</span>
            {markdown && (
              <span className="ml-auto font-mono">
                {markdown.length.toLocaleString()} chars
              </span>
            )}
          </div>
          <textarea
            className="border border-slate-200 rounded p-2 w-full text-sm font-mono"
            rows={10}
            value={markdown}
            onChange={(e) => setMarkdown(e.target.value)}
            placeholder="# My PRD&#10;&#10;## Goals&#10;&#10;..."
          />
        </Field>
        <button
          className="bg-slate-900 text-white text-sm px-3 py-1.5 rounded hover:bg-slate-700 disabled:opacity-50"
          disabled={!markdown.trim() || upload.isPending}
          onClick={() => upload.mutate()}
        >
          {upload.isPending ? "uploading…" : "Upload + parse"}
        </button>
        {upload.error && (
          <pre className="text-xs text-red-600 whitespace-pre-wrap">
            {(upload.error as Error).message}
          </pre>
        )}
      </div>

      {/* After upload: chapter list + diff + generate */}
      {uploaded && (
        <div className="bg-white border border-slate-200 rounded-lg p-4 space-y-3">
          <div className="flex flex-wrap justify-between items-baseline gap-2">
            <div>
              <span className="text-xs uppercase tracking-wide text-slate-400">
                uploaded
              </span>{" "}
              <code className="text-sm">{uploaded.title}</code>
              <span className="ml-2 text-xs text-slate-500">v{uploaded.version}</span>
              <span className="ml-2 text-xs text-slate-400">
                · prd_id <code>{uploaded.prd_id.slice(0, 8)}</code>…
              </span>
            </div>
            <div className="flex items-center gap-2">
              {uploaded.raw_markdown && (
                <button
                  className="text-xs px-2 py-1 rounded border border-slate-200 bg-white text-slate-600 hover:border-slate-400"
                  onClick={loadUploadedIntoEditor}
                >
                  Load into editor
                </button>
              )}
            </div>
            {uploaded.diff_summary && (
              <div className="text-xs">
                {Object.entries(uploaded.diff_summary).map(([k, v]) => (
                  <span
                    key={k}
                    className={
                      "ml-2 px-2 py-0.5 rounded " +
                      (k === "added"
                        ? "bg-emerald-50 text-emerald-700"
                        : k === "modified"
                          ? "bg-amber-50 text-amber-700"
                          : k === "removed"
                            ? "bg-red-50 text-red-700"
                            : "bg-slate-50 text-slate-600")
                    }
                  >
                    {k}: {v}
                  </span>
                ))}
              </div>
            )}
          </div>

          {/* Drafted case status banner. Cases now come from accepted coverage,
              not from direct PRD generation. */}
          {totalCasesForPrd > 0 && (
            <div className="bg-emerald-50 border border-emerald-200 rounded p-2 text-xs flex items-center gap-2">
              <span className="text-emerald-700 font-medium">
                ✓ {totalCasesForPrd} case drafts linked to this PRD
              </span>
              <span className="text-slate-500">
                (live count, refreshes every 5s)
              </span>
              <a
                href={`/cases?project_id=${encodeURIComponent(projectId)}`}
                className="ml-auto text-blue-700 hover:underline"
              >
                review them →
              </a>
            </div>
          )}

          <div className="text-sm">
            <div className="flex flex-wrap items-center justify-between gap-3 mb-3">
              <div className="inline-flex rounded border border-slate-200 bg-slate-50 p-0.5">
                {(["chapters", "preview", "raw"] as PrdViewTab[]).map((tab) => (
                  <button
                    key={tab}
                    className={
                      "px-2.5 py-1 text-xs rounded capitalize " +
                      (prdViewTab === tab
                        ? "bg-white text-slate-900 shadow-sm"
                        : "text-slate-500 hover:text-slate-800")
                    }
                    onClick={() => setPrdViewTab(tab)}
                  >
                    {tab}
                  </button>
                ))}
              </div>
              {prdViewTab === "chapters" && (
                <div className="flex items-center gap-3">
                  <span className="text-slate-500">
                    {uploaded.chapters.length} chapters · {selected.size} selected
                  </span>
                  <button
                    className="text-xs text-slate-500 underline hover:text-slate-700"
                    onClick={() => setSelected(new Set(uploaded.chapters.map((c) => c.position)))}
                  >
                    select all
                  </button>
                  <button
                    className="text-xs text-slate-500 underline hover:text-slate-700"
                    onClick={() => setSelected(new Set())}
                  >
                    clear
                  </button>
                </div>
              )}
            </div>
            {prdViewTab === "chapters" && (
              <table className="w-full text-sm">
                <thead className="text-left text-slate-400">
                  <tr>
                    <th className="pb-1 w-8"></th>
                    <th className="pb-1 w-12">#</th>
                    <th className="pb-1">title</th>
                    <th className="pb-1 w-16">level</th>
                    <th className="pb-1 w-20">chars</th>
                    <th className="pb-1 w-24">hash</th>
                    <th className="pb-1 w-32">cases</th>
                  </tr>
                </thead>
                <tbody>
                  {uploaded.chapters.map((c) => {
                    const counts = chapterCount(c);
                    return (
                      <tr key={c.position} className="border-t border-slate-100">
                        <td className="py-1">
                          <input
                            type="checkbox"
                            checked={selected.has(c.position)}
                            onChange={() => toggleChapter(c.position)}
                          />
                        </td>
                        <td className="text-slate-400 font-mono text-xs">{c.position}</td>
                        <td>
                          <code>{c.title}</code>{" "}
                          <span className="text-xs text-slate-400">
                            ({c.normalized_title})
                          </span>
                        </td>
                        <td className="text-slate-500">H{c.level}</td>
                        <td className="text-slate-500 font-mono text-xs">{c.body_chars}</td>
                        <td className="text-slate-400 font-mono text-xs">{c.hash}</td>
                        <td className="text-xs">
                          {counts.total === 0 ? (
                            <span className="text-slate-300">—</span>
                          ) : (
                            <span
                              className="text-emerald-700"
                              title={`${counts.pending} pending, ${counts.approved} approved`}
                            >
                              ✓ {counts.total}
                            </span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
            {prdViewTab === "preview" && (
              <MarkdownPreview markdown={uploaded.raw_markdown || ""} />
            )}
            {prdViewTab === "raw" && (
              <pre className="max-h-[560px] overflow-auto rounded border border-slate-200 bg-slate-950 p-4 text-xs leading-5 text-slate-100 whitespace-pre-wrap">
                {uploaded.raw_markdown || "(raw markdown is not available for this PRD)"}
              </pre>
            )}
          </div>

          <div className="border-t border-slate-100 pt-3">
            <div className="flex flex-wrap items-center gap-2 rounded border border-slate-200 bg-slate-50 p-3 text-sm">
              <span className="text-slate-600">
                Coverage review now lives in the dedicated workspace.
              </span>
              <span className="text-xs text-slate-400">
                {coverage.data?.count ?? 0} items for this PRD
              </span>
              <Link
                to="/coverage"
                search={{ prd_id: uploaded.prd_id }}
                className="ml-auto rounded bg-slate-900 px-3 py-1.5 text-xs text-white hover:bg-slate-700"
              >
                Open Coverage
              </Link>
            </div>
          </div>

          <div>
            <div className="flex flex-wrap items-center gap-2">
              <button
                className="bg-emerald-700 text-white text-sm px-3 py-1.5 rounded hover:bg-emerald-800 disabled:opacity-50"
                disabled={
                  selected.size === 0 ||
                  jobs.isLoading ||
                  isAutoGenerationActive ||
                  saveTimeout.isPending
                }
                onClick={startAutoGeneration}
              >
                {generate.isPending
                  ? "scheduling…"
                  : effectiveJob?.status === "running" || effectiveJob?.status === "pending"
                    ? autoProgress?.active
                      ? `generating ${autoProgress.processed}/${autoProgress.total} selected…`
                      : `generating ${effectiveJob.completed_chapters}/${effectiveJob.total_chapters}…`
                    : autoProgress?.active
                      ? `generating ${autoProgress.processed}/${autoProgress.total} selected…`
                    : `Analyze coverage for ${selected.size} chapter${selected.size === 1 ? "" : "s"}`}
              </button>
              <label className="inline-flex items-center gap-1 text-xs text-slate-500">
                Chapters/run
                <input
                  type="number"
                  min={1}
                  max={MAX_CHAPTERS_PER_RUN}
                  step={1}
                  value={chaptersPerRunInput}
                  disabled={isAutoGenerationActive}
                  onChange={(e) => setChaptersPerRunInput(e.target.value)}
                  onBlur={persistChaptersPerRunFromInput}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.currentTarget.blur();
                    }
                  }}
                  className="w-16 border border-slate-200 rounded px-2 py-1 text-xs text-slate-700 disabled:bg-slate-50"
                />
              </label>
              {autoProgress ? (
                <span className="text-xs text-slate-500">
                  {Math.max(autoProgress.total - autoProgress.processed, 0)} selected remaining
                </span>
              ) : selected.size > runChapterCount ? (
                <span className="text-xs text-slate-500">
                  {selected.size - runChapterCount} queued for auto-run
                </span>
              ) : null}
              <label className="inline-flex items-center gap-1 text-xs text-slate-500">
                Output
                <select
                  value={outputLanguage}
                  disabled={isAutoGenerationActive}
                  onChange={(e) => updateOutputLanguage(e.target.value as OutputLanguage)}
                  className="border border-slate-200 rounded px-2 py-1 text-xs text-slate-700 disabled:bg-slate-50"
                >
                  <option value="zh">中文</option>
                  <option value="en">English</option>
                  <option value="auto">Auto</option>
                </select>
              </label>
              <label className="inline-flex items-center gap-1 text-xs text-slate-500">
                Probe timeout
                <input
                  type="number"
                  min={runtimeSettings.data?.data.test_design_preflight_timeout_seconds.min ?? 5}
                  max={runtimeSettings.data?.data.test_design_preflight_timeout_seconds.max ?? 300}
                  step={5}
                  value={preflightTimeoutInput}
                  disabled={runtimeSettings.isLoading || saveTimeout.isPending || isAutoGenerationActive}
                  onChange={(e) => setPreflightTimeoutInput(e.target.value)}
                  onBlur={persistTimeoutFromInput}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.currentTarget.blur();
                    }
                  }}
                  className="w-16 border border-slate-200 rounded px-2 py-1 text-xs text-slate-700 disabled:bg-slate-50"
                />
                s
              </label>
              <label className="inline-flex items-center gap-1 text-xs text-slate-500">
                Analysis timeout
                <input
                  type="number"
                  min={30}
                  max={1800}
                  step={30}
                  value={generationTimeoutInput}
                  disabled={isAutoGenerationActive}
                  onChange={(e) => setGenerationTimeoutInput(e.target.value)}
                  onBlur={persistGenerationTimeoutFromInput}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.currentTarget.blur();
                    }
                  }}
                  className="w-20 border border-slate-200 rounded px-2 py-1 text-xs text-slate-700 disabled:bg-slate-50"
                />
                s
              </label>
              <span className="text-xs text-slate-500">
                design provider:{" "}
                <code>
                  {runtimeSettings.data?.data.test_design_provider.value ?? "auto"}
                </code>
              </span>
            </div>
            {saveTimeout.error && (
              <pre className="text-xs text-red-600 whitespace-pre-wrap mt-2">
                {(saveTimeout.error as Error).message}
              </pre>
            )}
            {generate.error && (
              <pre className="text-xs text-red-600 whitespace-pre-wrap mt-2">
                {(generate.error as Error).message}
              </pre>
            )}
          </div>

          {/* Live job progress. Survives page reload because activeJobId is
              in React state populated from the mutation or restored from
              /api/prd/<id>/jobs after refresh. */}
          {effectiveJob && (
            <JobProgressPanel
              job={effectiveJob}
              onCancel={() => cancelAutoGeneration(effectiveJob.job_id)}
              onSkipCurrent={() => skipCurrentBatch.mutate(effectiveJob.job_id)}
              cancelling={cancelJob.isPending}
              skipping={skipCurrentBatch.isPending}
              autoProgress={autoProgress}
            />
          )}
        </div>
      )}

      {/* PRD history */}
      <div className="bg-white border border-slate-200 rounded-lg p-4">
        <div className="text-xs uppercase tracking-wide text-slate-400 mb-2">
          PRD history (project: <code>{projectId}</code>)
        </div>
        {list.data?.data?.length ? (
          <table className="w-full text-sm">
            <thead className="text-left text-slate-400">
              <tr>
                <th className="pb-1">name</th>
                <th className="pb-1 w-16">v</th>
                <th className="pb-1 w-20">chapters</th>
                <th className="pb-1 w-44">uploaded_at</th>
                <th className="pb-1 w-20"></th>
              </tr>
            </thead>
            <tbody>
              {list.data.data.map((p) => {
                const isActive = p.prd_id === activePrdId;
                return (
                  <tr
                    key={p.prd_id}
                    className={
                      "border-t border-slate-100 cursor-pointer hover:bg-slate-50 " +
                      (isActive ? "bg-blue-50" : "")
                    }
                    onClick={() => setActivePrdId(p.prd_id)}
                  >
                    <td className="py-1">
                      <code className={isActive ? "text-blue-700 font-medium" : ""}>
                        {p.name}
                      </code>
                      <span className="text-xs text-slate-400 ml-2">
                        ({p.prd_id.slice(0, 8)})
                      </span>
                    </td>
                    <td className="text-slate-500">v{p.version}</td>
                    <td className="text-slate-500">{p.chapter_count}</td>
                    <td className="text-slate-400 font-mono text-xs">
                      {new Date(p.uploaded_at).toLocaleString()}
                    </td>
                    <td onClick={(e) => e.stopPropagation()}>
                      <button
                        className="text-xs text-slate-400 hover:text-red-600 disabled:opacity-50"
                        disabled={
                          deletePrd.isPending && deletePrd.variables === p.prd_id
                        }
                        onClick={() => {
                          if (
                            window.confirm(
                              `Delete ${p.name} v${p.version}?\n\n` +
                                `Generated cases keep living — only the PRD record itself is removed.`,
                            )
                          ) {
                            deletePrd.mutate(p.prd_id);
                          }
                        }}
                        title="delete this PRD"
                      >
                        🗑 delete
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        ) : (
          <span className="text-slate-400 text-sm">no PRDs yet</span>
        )}
        {deletePrd.error && (
          <div className="text-xs text-red-600 mt-2">
            delete error: {(deletePrd.error as Error).message}
          </div>
        )}
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block text-sm">
      <span className="text-slate-500 mb-1 block text-xs uppercase tracking-wide">
        {label}
      </span>
      {children}
    </label>
  );
}

function MarkdownPreview({ markdown }: { markdown: string }) {
  const blocks = parseMarkdownPreview(markdown);
  if (!blocks.length) {
    return (
      <div className="rounded border border-slate-200 bg-slate-50 p-4 text-sm text-slate-400">
        No PRD content available.
      </div>
    );
  }
  return (
    <div className="max-h-[560px] overflow-auto rounded border border-slate-200 bg-white p-5 text-sm leading-6 text-slate-700">
      {blocks.map((block, index) => (
        <MarkdownBlockView block={block} key={index} />
      ))}
    </div>
  );
}

function MarkdownBlockView({ block }: { block: MarkdownBlock }) {
  if (block.type === "heading") {
    const size =
      block.level === 1
        ? "text-2xl mt-0"
        : block.level === 2
          ? "text-xl"
          : block.level === 3
            ? "text-lg"
            : "text-base";
    return (
      <div className={`${size} font-semibold text-slate-900 mt-5 mb-2`}>
        {renderInlineMarkdown(block.text)}
      </div>
    );
  }
  if (block.type === "list") {
    const ListTag = block.ordered ? "ol" : "ul";
    return (
      <ListTag
        className={
          "my-2 pl-5 space-y-1 " + (block.ordered ? "list-decimal" : "list-disc")
        }
      >
        {block.items.map((item, index) => (
          <li key={index}>{renderInlineMarkdown(item)}</li>
        ))}
      </ListTag>
    );
  }
  if (block.type === "code") {
    return (
      <pre className="my-3 overflow-auto rounded bg-slate-950 p-3 text-xs leading-5 text-slate-100">
        {block.text}
      </pre>
    );
  }
  return <p className="my-2">{renderInlineMarkdown(block.text)}</p>;
}

function renderInlineMarkdown(text: string): React.ReactNode[] {
  const nodes: React.ReactNode[] = [];
  const re = /(`[^`]+`|\*\*[^*]+\*\*)/g;
  let last = 0;
  let match: RegExpExecArray | null;
  while ((match = re.exec(text)) !== null) {
    if (match.index > last) nodes.push(text.slice(last, match.index));
    const token = match[0];
    if (token.startsWith("`")) {
      nodes.push(
        <code
          className="rounded bg-slate-100 px-1 py-0.5 font-mono text-xs text-slate-800"
          key={nodes.length}
        >
          {token.slice(1, -1)}
        </code>,
      );
    } else {
      nodes.push(
        <strong className="font-semibold text-slate-900" key={nodes.length}>
          {token.slice(2, -2)}
        </strong>,
      );
    }
    last = match.index + token.length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

function JobProgressPanel({
  job,
  onCancel,
  onSkipCurrent,
  cancelling,
  skipping,
  autoProgress,
}: {
  job: GenerationJob["data"];
  onCancel: () => void;
  onSkipCurrent: () => void;
  cancelling: boolean;
  skipping: boolean;
  autoProgress: { total: number; processed: number; active: boolean } | null;
}) {
  const isTerminal =
    job.status === "done" || job.status === "failed" || job.status === "cancelled";
  const batchPct = job.total_chapters
    ? Math.round((job.completed_chapters / job.total_chapters) * 100)
    : 0;
  const pct = autoProgress?.active && autoProgress.total > 0
    ? Math.round((autoProgress.processed / autoProgress.total) * 100)
    : batchPct;
  const progress = job.request_payload?.progress;
  const activeBatches = progress?.active_batches ?? [];
  const activeBatchLabel = activeBatches
    .map((batch) => `#${batch.chapter_indices.join(",#")}`)
    .join(" · ");
  const etaLabel =
    typeof progress?.eta_seconds === "number" && progress.eta_seconds > 0
      ? formatDuration(progress.eta_seconds)
      : null;
  const colour =
    job.status === "failed"
      ? "bg-red-50 border-red-200"
      : job.status === "cancelled"
        ? "bg-slate-50 border-slate-200"
      : job.status === "done"
        ? "bg-emerald-50 border-emerald-200"
        : "bg-blue-50 border-blue-200";

  return (
    <div className={`rounded p-3 text-sm border ${colour}`}>
      <div className="flex items-center justify-between mb-2">
        <div className="font-medium">
          {job.status === "pending" && "Job queued…"}
          {job.status === "running" && (
            <>
              {autoProgress?.active
                ? `Generating ${autoProgress.processed}/${autoProgress.total} selected chapters`
                : `Generating ${job.completed_chapters}/${job.total_chapters} chapters`}
              {" · "}
              {autoProgress?.active && (
                <>
                  current batch {job.completed_chapters}/{job.total_chapters}
                  {" · "}
                </>
              )}
              <span className="text-emerald-700">{job.saved_cases} cases saved</span>
            </>
          )}
          {job.status === "done" && (
            <>
              ✓ Generated <span className="text-emerald-700">{job.saved_cases}</span> cases
              across {job.total_chapters} chapter{job.total_chapters > 1 ? "s" : ""}
            </>
          )}
          {job.status === "failed" && (
            <span className="text-red-700">✗ Job failed: {job.error || "unknown error"}</span>
          )}
          {job.status === "cancelled" && (
            <span className="text-slate-600">Job cancelled</span>
          )}
        </div>
        {!isTerminal && (
          <div className="flex items-center gap-2">
            <span className="text-xs text-slate-500 font-mono tabular-nums">{pct}%</span>
            {activeBatches.length > 0 && (
              <button
                type="button"
                onClick={onSkipCurrent}
                disabled={skipping || progress?.skip_requested}
                className="text-xs border border-amber-300 bg-white px-2 py-0.5 rounded text-amber-700 hover:bg-amber-50 disabled:opacity-50"
              >
                {progress?.skip_requested ? "skip requested" : skipping ? "skipping…" : "skip batch"}
              </button>
            )}
            <button
              type="button"
              onClick={onCancel}
              disabled={cancelling}
              className="text-xs border border-slate-300 bg-white px-2 py-0.5 rounded hover:bg-slate-50 disabled:opacity-50"
            >
              {cancelling ? "cancelling…" : "cancel"}
            </button>
          </div>
        )}
      </div>
      {!isTerminal && (
        <>
          <div className="w-full h-1.5 bg-slate-200/60 rounded-full overflow-hidden mb-2">
            <div
              className="h-full bg-blue-500 transition-all duration-300"
              style={{ width: `${pct}%` }}
            />
          </div>
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-500">
            {activeBatches.length > 0 && (
              <span>
                current {activeBatchLabel} · target{" "}
                {activeBatches.reduce((sum, batch) => sum + batch.target_cases, 0)} cases
              </span>
            )}
            {typeof progress?.completed_batches === "number" &&
              typeof progress?.total_batches === "number" && (
                <span>
                  batches {progress.completed_batches}/{progress.total_batches}
                  {progress.parallelism ? ` · parallel ${progress.parallelism}` : ""}
                </span>
              )}
            {typeof progress?.avg_batch_latency_seconds === "number" && (
              <span>last avg {formatDuration(progress.avg_batch_latency_seconds)}</span>
            )}
            {etaLabel && <span>ETA {etaLabel}</span>}
            {progress?.last_batch && (
              <span>
                last {progress.last_batch.status} #{progress.last_batch.chapter_indices.join(",#")}
                {typeof progress.last_batch.latency_seconds === "number"
                  ? ` · ${formatDuration(progress.last_batch.latency_seconds)}`
                  : ""}
                {progress.last_batch.error ? ` · ${progress.last_batch.error}` : ""}
              </span>
            )}
          </div>
        </>
      )}
      {job.results.length > 0 && (
        <ul className="space-y-1 mt-2 max-h-60 overflow-y-auto">
          {job.results
            .slice()
            .reverse()
            .map((r) => (
              <li key={r.chapter_index} className="text-xs">
                <code>#{r.chapter_index}</code> {r.chapter_title}:{" "}
                {r.error ? (
                  <span className="text-red-600">error: {r.error}</span>
                ) : r.skipped ? (
                  <span className="text-slate-500">
                    skipped ({r.skip_reason || "no change"})
                  </span>
                ) : (
                  <span className="text-slate-600">
                    {r.saved_count} cases saved
                  </span>
                )}
              </li>
            ))}
        </ul>
      )}
    </div>
  );
}

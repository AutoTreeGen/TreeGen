/**
 * Типизированный fetch-клиент к parser-service.
 *
 * Типы зеркалят ``services/parser-service/src/parser_service/schemas.py``.
 * При изменении Pydantic-схем — обновлять руками. Phase 4.2 заменит ручной
 * клиент на OpenAPI-codegen.
 *
 * Phase 4.6 (ADR-0041): typed errors + retry с exponential backoff.
 * Низкоуровневый ``fetchJson`` оборачивается в ``withRetry`` для
 * idempotent-методов (GET); 401 триггерит редирект на /sign-in.
 */

import { ApiError, AuthError, NetworkError, classifyHttpError } from "./errors";
import { withRetry } from "./retry";

const API_BASE = process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") ?? "http://localhost:8000";

/**
 * Ре-экспортим типизированную иерархию из ``./errors`` для backward-compat:
 * существующий код, импортирующий ``ApiError`` из ``@/lib/api``, продолжает
 * работать без правок. Новый код может импортировать конкретные подклассы
 * напрямую из ``@/lib/errors``.
 */
export { ApiError, AuthError, NetworkError, ServerError, ValidationError } from "./errors";

// ---- Types (зеркало parser_service.schemas) ---------------------------------

export type PersonMatchType = "substring" | "phonetic";

export type PersonSummary = {
  id: string;
  gedcom_xref: string | null;
  sex: string;
  confidence_score: number;
  primary_name: string | null;
  match_type: PersonMatchType | null;
};

export type PersonListResponse = {
  tree_id: string;
  total: number;
  limit: number;
  offset: number;
  items: PersonSummary[];
};

export type NameSummary = {
  id: string;
  given_name: string | null;
  surname: string | null;
  sort_order: number;
};

export type EventSummary = {
  id: string;
  event_type: string;
  date_raw: string | null;
  date_start: string | null;
  date_end: string | null;
  place_id: string | null;
};

export type PersonDetail = {
  id: string;
  tree_id: string;
  gedcom_xref: string | null;
  sex: string;
  status: string;
  confidence_score: number;
  names: NameSummary[];
  events: EventSummary[];
};

// ---- Low-level fetch with typed errors + retry ------------------------------

/**
 * Hook для тестов: подменяет глобальный fetch без monkey-patch'а ``window``.
 */
let _fetchImpl: typeof fetch = (...args) => fetch(...args);

export function setFetchImpl(impl: typeof fetch): void {
  _fetchImpl = impl;
}

/**
 * Hook на 401: редирект на /sign-in. По умолчанию — навигация браузера;
 * тесты подменяют через ``setUnauthorizedHandler``.
 */
let _onUnauthorized: () => void = () => {
  if (typeof window !== "undefined") {
    window.location.assign("/sign-in");
  }
};

export function setUnauthorizedHandler(handler: () => void): void {
  _onUnauthorized = handler;
}

/**
 * Однократный fetch + классификация HTTP-ошибок в типизированный ``ApiError``.
 * Не ретраит — это делает ``withRetry`` снаружи.
 */
async function fetchOnce(path: string, init?: RequestInit): Promise<Response> {
  let response: Response;
  // Phase 4.10: auth-header через ``authHeaders()``-singleton (см. ниже).
  // Без зависимости — auth опциональный (anon-fetch'и landing-роутов).
  const auth = await authHeaders();
  try {
    response = await _fetchImpl(`${API_BASE}${path}`, {
      ...init,
      headers: { Accept: "application/json", ...auth, ...init?.headers },
    });
  } catch (err) {
    // fetch бросает только при network/abort/CORS-настройках. Любую
    // такую ошибку считаем network — retry-safe.
    throw new NetworkError(err instanceof Error ? err.message : String(err));
  }
  if (!response.ok) {
    const detail = await safeReadDetail(response);
    const message = detail ?? `Request to ${path} failed with ${response.status}`;
    throw classifyHttpError(response.status, message);
  }
  return response;
}

/**
 * Phase 4.10: getter для Bearer JWT, выпущенного Clerk.
 *
 * Все API-вызовы идут через ``getJson`` / ``fetch``-wrapper'ы; они
 * читают токен через этот глобальный getter и подставляют в
 * ``Authorization``-header. Setter регистрируется один раз в
 * ``providers.tsx`` (через ``useAuth().getToken``).
 *
 * Дизайн «глобальный setter» вместо «передавать token каждой
 * ApiCall'у» — компромисс: API-функции уже сейчас вызываются из
 * множества мест (server-actions, hooks), и protocoling каждой
 * сигнатуры под token-passing раздул бы PR на ровном месте.
 *
 * SSR-режим: на server этот getter возвращает null (Clerk-context
 * отсутствует), запросы уйдут без Bearer'а — в server-actions
 * добавляйте `headers: { Authorization: ... }` руками.
 */
type AuthTokenProvider = () => Promise<string | null>;

let authTokenProvider: AuthTokenProvider | null = null;

export function setAuthTokenProvider(provider: AuthTokenProvider | null): void {
  authTokenProvider = provider;
}

async function authHeaders(): Promise<Record<string, string>> {
  if (authTokenProvider === null) {
    return {};
  }
  const token = await authTokenProvider();
  if (!token) {
    return {};
  }
  return { Authorization: `Bearer ${token}` };
}

async function getJson<T>(path: string, init?: RequestInit): Promise<T> {
  // Phase 4.6: retry применяется ко всем GET'ам и body-less запросам;
  // POST/PATCH/DELETE caller'ы оборачивают сами (через ``fetchOnceJson``).
  // Phase 4.10: auth-header добавляется внутри ``fetchOnce``.
  try {
    const response = await withRetry(() => fetchOnce(path, init));
    return (await response.json()) as T;
  } catch (err) {
    if (err instanceof AuthError && err.status === 401) {
      _onUnauthorized();
    }
    throw err;
  }
}

/**
 * Public-helper для non-idempotent (POST/PATCH/DELETE) call-site'ов:
 * прокидывает single-attempt fetch с typed-error mapping. Caller
 * сам решает, ретраить ли (например, idempotency_key в payload'е).
 *
 * Также автоматически реагирует на 401 — вызывает unauthorized-handler.
 */
export async function fetchOnceJson<T>(path: string, init?: RequestInit): Promise<T> {
  try {
    const response = await fetchOnce(path, init);
    return (await response.json()) as T;
  } catch (err) {
    if (err instanceof AuthError && err.status === 401) {
      _onUnauthorized();
    }
    throw err;
  }
}

// ---- Public surface ---------------------------------------------------------

export function fetchPersons(treeId: string, limit = 50, offset = 0): Promise<PersonListResponse> {
  const params = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
  });
  return getJson<PersonListResponse>(`/trees/${treeId}/persons?${params.toString()}`);
}

export type PersonSearchParams = {
  q?: string;
  /** Phonetic mode: Daitch-Mokotoff bucket overlap (Phase 4.4.1). */
  phonetic?: boolean;
  birthYearMin?: number;
  birthYearMax?: number;
  limit?: number;
  offset?: number;
};

export function searchPersons(
  treeId: string,
  { q, phonetic, birthYearMin, birthYearMax, limit = 50, offset = 0 }: PersonSearchParams = {},
): Promise<PersonListResponse> {
  const params = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
  });
  if (q) params.set("q", q);
  if (phonetic) params.set("phonetic", "true");
  if (birthYearMin !== undefined && Number.isFinite(birthYearMin)) {
    params.set("birth_year_min", String(birthYearMin));
  }
  if (birthYearMax !== undefined && Number.isFinite(birthYearMax)) {
    params.set("birth_year_max", String(birthYearMax));
  }
  return getJson<PersonListResponse>(`/trees/${treeId}/persons/search?${params.toString()}`);
}

export function fetchPerson(personId: string): Promise<PersonDetail> {
  return getJson<PersonDetail>(`/persons/${personId}`);
}

// ---- Pedigree (Phase 4.3) ---------------------------------------------------

export type AncestorTreeNode = {
  id: string;
  primary_name: string | null;
  birth_year: number | null;
  death_year: number | null;
  sex: string;
  dna_tested?: boolean;
  father: AncestorTreeNode | null;
  mother: AncestorTreeNode | null;
};

export type AncestorsResponse = {
  person_id: string;
  generations_requested: number;
  generations_loaded: number;
  root: AncestorTreeNode;
};

export function fetchAncestors(personId: string, generations = 5): Promise<AncestorsResponse> {
  const params = new URLSearchParams({ generations: String(generations) });
  return getJson<AncestorsResponse>(`/persons/${personId}/ancestors?${params.toString()}`);
}

// ---- Tree statistics (Phase 6.5 — ADR-0051) ---------------------------------

export type TopSurname = {
  surname: string;
  person_count: number;
};

export type TreeStatisticsResponse = {
  tree_id: string;
  persons_count: number;
  families_count: number;
  events_count: number;
  sources_count: number;
  hypotheses_count: number;
  dna_matches_count: number;
  places_count: number;
  pedigree_max_depth: number;
  oldest_birth_year: number | null;
  top_surnames: TopSurname[];
};

export function fetchTreeStatistics(treeId: string): Promise<TreeStatisticsResponse> {
  return getJson<TreeStatisticsResponse>(`/trees/${treeId}/statistics`);
}

// ---- Duplicate suggestions (Phase 4.5) -------------------------------------

export type DuplicateEntityType = "person" | "source" | "place";

export type DuplicateSuggestion = {
  entity_type: DuplicateEntityType;
  entity_a_id: string;
  entity_b_id: string;
  confidence: number;
  components: Record<string, number>;
  evidence: Record<string, unknown>;
};

export type DuplicateSuggestionListResponse = {
  tree_id: string;
  entity_type: DuplicateEntityType | null;
  min_confidence: number;
  total: number;
  limit: number;
  offset: number;
  items: DuplicateSuggestion[];
};

export function fetchDuplicateSuggestions(
  treeId: string,
  entityType: DuplicateEntityType,
  minConfidence = 0.8,
  limit = 100,
  offset = 0,
): Promise<DuplicateSuggestionListResponse> {
  const params = new URLSearchParams({
    entity_type: entityType,
    min_confidence: String(minConfidence),
    limit: String(limit),
    offset: String(offset),
  });
  return getJson<DuplicateSuggestionListResponse>(
    `/trees/${treeId}/duplicate-suggestions?${params.toString()}`,
  );
}

// ---- Person merge (Phase 4.6 — ADR-0022) ----------------------------------

export type SurvivorChoice = "left" | "right";
export type HypothesisCheckStatus = "no_hypotheses_found" | "no_conflicts" | "conflicts_blocking";

export type MergeFieldDiff = {
  field: string;
  survivor_value: unknown;
  merged_value: unknown;
  after_merge_value: unknown;
};

export type MergeNameDiff = {
  name_id: string;
  old_sort_order: number;
  new_sort_order: number;
};

export type MergeEventDiff = {
  event_id: string;
  action: "reparent" | "collapse_into_survivor" | "keep_separate";
  collapsed_into: string | null;
};

export type MergeFamilyMembershipDiff = {
  table: "families.husband_id" | "families.wife_id" | "family_children.child_person_id";
  row_id: string;
};

export type MergeHypothesisConflict = {
  reason: "rejected_same_person" | "subject_already_merged" | "cross_relationship_conflict";
  hypothesis_id: string | null;
  detail: string;
};

export type MergePreviewResponse = {
  survivor_id: string;
  merged_id: string;
  default_survivor_id: string;
  fields: MergeFieldDiff[];
  names: MergeNameDiff[];
  events: MergeEventDiff[];
  family_memberships: MergeFamilyMembershipDiff[];
  hypothesis_check: HypothesisCheckStatus;
  conflicts: MergeHypothesisConflict[];
};

export type MergeCommitRequest = {
  target_id: string;
  confirm: true;
  confirm_token: string;
  survivor_choice?: SurvivorChoice | null;
};

export type MergeCommitResponse = {
  merge_id: string;
  survivor_id: string;
  merged_id: string;
  merged_at: string;
  confirm_token: string;
};

export function fetchMergePreview(
  personId: string,
  payload: { target_id: string; survivor_choice?: SurvivorChoice | null; confirm_token: string },
): Promise<MergePreviewResponse> {
  return getJson<MergePreviewResponse>(`/persons/${personId}/merge/preview`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      target_id: payload.target_id,
      survivor_choice: payload.survivor_choice ?? null,
      confirm: true,
      confirm_token: payload.confirm_token,
    }),
  });
}

export async function commitMerge(
  personId: string,
  payload: MergeCommitRequest,
): Promise<MergeCommitResponse> {
  return getJson<MergeCommitResponse>(`/persons/${personId}/merge`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

// Phase 6.4 — manual merge UI: undo + history.
// 90-day window enforced server-side (ADR-0022); UI mirrors the same threshold
// so the undo button can be hidden once expired without a round-trip.
export const MERGE_UNDO_WINDOW_DAYS = 90;

export type MergeUndoResponse = {
  merge_id: string;
  survivor_id: string;
  merged_id: string;
  undone_at: string;
};

export type MergeHistoryItem = {
  merge_id: string;
  survivor_id: string;
  merged_id: string;
  merged_at: string;
  undone_at: string | null;
  purged_at: string | null;
};

export type MergeHistoryResponse = {
  person_id: string;
  items: MergeHistoryItem[];
};

export async function undoMerge(mergeId: string): Promise<MergeUndoResponse> {
  return fetchOnceJson<MergeUndoResponse>(`/persons/merge/${mergeId}/undo`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });
}

export function fetchMergeHistory(personId: string): Promise<MergeHistoryResponse> {
  return getJson<MergeHistoryResponse>(`/persons/${personId}/merge-history`);
}

/**
 * UI-side mirror of ADR-0022 §90-day window: возвращает true, если undo
 * ещё доступен. Server остаётся source of truth (вернёт 410 если протухло
 * между запросами), но эта функция нужна, чтобы НЕ показывать undo-кнопку
 * для очевидно протухших merge'ей. Также скрывает undo для уже-undone
 * (``undone_at != null``) или physically-purged (``purged_at != null``).
 *
 * Использует ``Date.now()`` (а не ``new Date()``) чтобы тесты могли
 * мокать «текущее время» через ``vi.spyOn(Date, "now")`` без useFakeTimers.
 */
export function isMergeUndoable(item: MergeHistoryItem, nowMs: number = Date.now()): boolean {
  if (item.undone_at !== null || item.purged_at !== null) return false;
  const mergedAt = new Date(item.merged_at);
  if (Number.isNaN(mergedAt.getTime())) return false;
  const ageMs = nowMs - mergedAt.getTime();
  const windowMs = MERGE_UNDO_WINDOW_DAYS * 24 * 60 * 60 * 1000;
  return ageMs < windowMs;
}

// ---- Hypotheses (Phase 4.9: review UI; Phase 7.2: persistence) -------------

export type HypothesisReviewStatus = "pending" | "confirmed" | "rejected" | "deferred";

export type HypothesisType =
  | "same_person"
  | "parent_child"
  | "siblings"
  | "marriage"
  | "duplicate_source"
  | "duplicate_place";

export type HypothesisSummary = {
  id: string;
  tree_id: string;
  hypothesis_type: HypothesisType;
  subject_a_type: string;
  subject_a_id: string;
  subject_b_type: string;
  subject_b_id: string;
  composite_score: number;
  computed_at: string;
  rules_version: string;
  reviewed_status: HypothesisReviewStatus;
  reviewed_at: string | null;
};

export type HypothesisEvidence = {
  id: string;
  rule_id: string;
  direction: "supports" | "contradicts" | "neutral";
  weight: number;
  observation: string;
  source_provenance: Record<string, unknown>;
};

export type HypothesisResponse = HypothesisSummary & {
  review_note: string | null;
  reviewed_by_user_id: string | null;
  evidences: HypothesisEvidence[];
};

export type HypothesisListResponse = {
  tree_id: string;
  total: number;
  limit: number;
  offset: number;
  items: HypothesisSummary[];
};

export type HypothesisListFilters = {
  reviewStatus?: HypothesisReviewStatus | null;
  hypothesisType?: HypothesisType | null;
  subjectId?: string | null;
  minConfidence?: number;
  limit?: number;
  offset?: number;
};

export function fetchHypotheses(
  treeId: string,
  filters: HypothesisListFilters = {},
): Promise<HypothesisListResponse> {
  const {
    reviewStatus,
    hypothesisType,
    subjectId,
    minConfidence = 0.5,
    limit = 50,
    offset = 0,
  } = filters;
  const params = new URLSearchParams({
    min_confidence: String(minConfidence),
    limit: String(limit),
    offset: String(offset),
  });
  if (reviewStatus) params.set("review_status", reviewStatus);
  if (hypothesisType) params.set("hypothesis_type", hypothesisType);
  if (subjectId) params.set("subject_id", subjectId);
  return getJson<HypothesisListResponse>(`/trees/${treeId}/hypotheses?${params.toString()}`);
}

export function fetchHypothesis(hypothesisId: string): Promise<HypothesisResponse> {
  return getJson<HypothesisResponse>(`/hypotheses/${hypothesisId}`);
}

export function reviewHypothesis(
  hypothesisId: string,
  payload: { status: HypothesisReviewStatus; note?: string | null },
): Promise<HypothesisResponse> {
  return getJson<HypothesisResponse>(`/hypotheses/${hypothesisId}/review`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      status: payload.status,
      note: payload.note ?? null,
    }),
  });
}

// ---- Bulk hypothesis compute (Phase 7.5) ----------------------------------

export type HypothesisComputeJobStatus =
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled";

export type HypothesisComputeJobProgress = {
  processed: number;
  total: number;
  hypotheses_created: number;
};

export type HypothesisComputeJobResponse = {
  id: string;
  tree_id: string;
  status: HypothesisComputeJobStatus;
  rule_ids: string[] | null;
  progress: HypothesisComputeJobProgress;
  cancel_requested: boolean;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  events_url: string | null;
};

/** Полный URL SSE-стрима bulk-compute job'а (для useEventSource). */
export function bulkComputeEventsUrl(treeId: string, jobId: string): string {
  return `${API_BASE}/trees/${treeId}/hypotheses/compute-jobs/${jobId}/events`;
}

export async function startBulkCompute(
  treeId: string,
  ruleIds?: string[] | null,
): Promise<HypothesisComputeJobResponse> {
  const response = await fetch(`${API_BASE}/trees/${treeId}/hypotheses/compute-all`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ rule_ids: ruleIds ?? null }),
  });
  if (!response.ok) {
    const detail = await safeReadDetail(response);
    throw new ApiError(response.status, detail ?? `Compute-all failed with ${response.status}`);
  }
  return (await response.json()) as HypothesisComputeJobResponse;
}

export function fetchBulkComputeJob(
  treeId: string,
  jobId: string,
): Promise<HypothesisComputeJobResponse> {
  return getJson<HypothesisComputeJobResponse>(`/trees/${treeId}/hypotheses/compute-jobs/${jobId}`);
}

export async function cancelBulkComputeJob(jobId: string): Promise<HypothesisComputeJobResponse> {
  const response = await fetch(`${API_BASE}/hypotheses/compute-jobs/${jobId}/cancel`, {
    method: "PATCH",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    const detail = await safeReadDetail(response);
    throw new ApiError(response.status, detail ?? `Cancel failed with ${response.status}`);
  }
  return (await response.json()) as HypothesisComputeJobResponse;
}

// ---- Sources & citations (Phase 4.7) --------------------------------------

export type SourceSummary = {
  id: string;
  gedcom_xref: string | null;
  title: string;
  abbreviation: string | null;
  author: string | null;
  publication: string | null;
  repository: string | null;
  source_type: string;
  citation_count: number;
};

export type SourceListResponse = {
  tree_id: string;
  total: number;
  limit: number;
  offset: number;
  items: SourceSummary[];
};

export type SourceLinkedEntity = {
  table: "person" | "family" | "event";
  id: string;
  page: string | null;
  quay_raw: number | null;
  quality: number;
  /**
   * Денормализованный label (Phase 4.7-finalize): имя person'а,
   * "EVENT_TYPE YEAR" для event'а, "Husband × Wife" для family.
   * `null` если backend не смог разрешить (orphan FK / soft-delete).
   */
  display_label: string | null;
};

export type SourceDetail = {
  id: string;
  tree_id: string;
  gedcom_xref: string | null;
  title: string;
  abbreviation: string | null;
  author: string | null;
  publication: string | null;
  repository: string | null;
  text_excerpt: string | null;
  source_type: string;
  linked: SourceLinkedEntity[];
};

export type PersonCitationDetail = {
  id: string;
  source_id: string;
  source_title: string;
  source_abbreviation: string | null;
  entity_type: "person" | "family" | "event";
  entity_id: string;
  page: string | null;
  quay_raw: number | null;
  quality: number;
  event_type: string | null;
  role: string | null;
  note: string | null;
  quoted_text: string | null;
};

export type PersonCitationsResponse = {
  person_id: string;
  total: number;
  items: PersonCitationDetail[];
};

export type SourcesListParams = {
  /** Substring search по title/abbreviation/author (Phase 4.7-finalize). */
  q?: string;
  limit?: number;
  offset?: number;
};

export function fetchSources(
  treeId: string,
  params: SourcesListParams = {},
): Promise<SourceListResponse> {
  const { q, limit = 50, offset = 0 } = params;
  const search = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (q) search.set("q", q);
  return getJson<SourceListResponse>(`/trees/${treeId}/sources?${search.toString()}`);
}

export function fetchSource(sourceId: string): Promise<SourceDetail> {
  return getJson<SourceDetail>(`/sources/${sourceId}`);
}

export function fetchPersonCitations(personId: string): Promise<PersonCitationsResponse> {
  return getJson<PersonCitationsResponse>(`/persons/${personId}/citations`);
}

// ---- Imports (Phase 3.5) ---------------------------------------------------

export type ImportJobStatus = "queued" | "processing" | "succeeded" | "failed" | "canceled";

export type ImportJobResponse = {
  id: string;
  tree_id: string;
  status: ImportJobStatus | string;
  source_filename: string | null;
  source_sha256: string | null;
  stats: Record<string, number>;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
};

/**
 * URL для подписки на SSE-стрим прогресса. Возвращается строкой —
 * используется в ``useEventSource(url)``.
 */
export function importEventsUrl(jobId: string): string {
  return `${API_BASE}/imports/${jobId}/events`;
}

export async function postImport(file: File): Promise<ImportJobResponse> {
  const body = new FormData();
  body.append("file", file);
  const response = await fetch(`${API_BASE}/imports`, {
    method: "POST",
    body,
  });
  if (!response.ok) {
    const detail = await safeReadDetail(response);
    throw new ApiError(response.status, detail ?? `Upload failed with ${response.status}`);
  }
  return (await response.json()) as ImportJobResponse;
}

export function fetchImport(jobId: string): Promise<ImportJobResponse> {
  return getJson<ImportJobResponse>(`/imports/${jobId}`);
}

export async function cancelImport(jobId: string): Promise<ImportJobResponse> {
  const response = await fetch(`${API_BASE}/imports/${jobId}/cancel`, {
    method: "PATCH",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    const detail = await safeReadDetail(response);
    throw new ApiError(response.status, detail ?? `Cancel failed with ${response.status}`);
  }
  return (await response.json()) as ImportJobResponse;
}

// ---- FamilySearch (Phase 5.1) ----------------------------------------------

export type FamilySearchOAuthStartResponse = {
  authorize_url: string;
  state: string;
  expires_in: number;
};

export type FamilySearchAccountInfo = {
  connected: boolean;
  fs_user_id: string | null;
  scope: string | null;
  expires_at: string | null;
  needs_refresh: boolean;
};

export type FamilySearchPedigreePreviewPerson = {
  fs_person_id: string;
  primary_name: string | null;
  lifespan: string | null;
};

export type FamilySearchPedigreePreviewResponse = {
  fs_focus_person_id: string;
  generations: number;
  person_count: number;
  sample_persons: FamilySearchPedigreePreviewPerson[];
  fs_user_id: string | null;
};

/**
 * Стартовать OAuth flow. Возвращает authorize_url, по которому надо
 * редиректить браузер. Cookie с CSRF state сервер выставляет сам;
 * fetch с ``credentials=include`` нужен, чтобы браузер сохранил cookie.
 */
export async function startFamilySearchOAuth(): Promise<FamilySearchOAuthStartResponse> {
  const response = await fetch(`${API_BASE}/imports/familysearch/oauth/start`, {
    method: "GET",
    credentials: "include",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    const detail = await safeReadDetail(response);
    throw new ApiError(response.status, detail ?? `OAuth start failed with ${response.status}`);
  }
  return (await response.json()) as FamilySearchOAuthStartResponse;
}

/**
 * Получить статус подключения. Не возвращает access_token (security).
 */
export async function fetchFamilySearchAccount(): Promise<FamilySearchAccountInfo> {
  return getJson<FamilySearchAccountInfo>("/imports/familysearch/me");
}

/**
 * Удалить токен (disconnect). 204 No Content при успехе.
 */
export async function disconnectFamilySearch(): Promise<void> {
  const response = await fetch(`${API_BASE}/imports/familysearch/disconnect`, {
    method: "DELETE",
    credentials: "include",
  });
  if (!response.ok && response.status !== 204) {
    const detail = await safeReadDetail(response);
    throw new ApiError(response.status, detail ?? `Disconnect failed with ${response.status}`);
  }
}

/**
 * Read-only preview pedigree (count + sample persons). Не запускает импорт.
 */
export function fetchFamilySearchPreview(
  fsPersonId: string,
  generations = 4,
): Promise<FamilySearchPedigreePreviewResponse> {
  const params = new URLSearchParams({
    fs_person_id: fsPersonId,
    generations: String(generations),
  });
  return getJson<FamilySearchPedigreePreviewResponse>(
    `/imports/familysearch/pedigree/preview?${params.toString()}`,
  );
}

/**
 * Запустить async-импорт. Использует server-side OAuth-токен.
 */
export async function startFamilySearchImport(payload: {
  fs_person_id: string;
  tree_id: string;
  generations: number;
}): Promise<ImportJobResponse> {
  const response = await fetch(`${API_BASE}/imports/familysearch/import`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const detail = await safeReadDetail(response);
    throw new ApiError(response.status, detail ?? `FS import failed with ${response.status}`);
  }
  return (await response.json()) as ImportJobResponse;
}

async function safeReadDetail(response: Response): Promise<string | null> {
  // FastAPI кладёт человекочитаемое сообщение в `detail`; если его нет
  // или body пустой — отдадим null, чтобы caller сделал fallback на статус.
  try {
    const payload = (await response.json()) as { detail?: unknown };
    if (typeof payload.detail === "string") return payload.detail;
    return null;
  } catch {
    return null;
  }
}

// =============================================================================
// Phase 11.1 — sharing API client (зеркалит parser_service.api.sharing).
// =============================================================================

export type ShareRole = "owner" | "editor" | "viewer";

export type Member = {
  id: string;
  user_id: string;
  email: string;
  display_name: string | null;
  role: ShareRole;
  invited_by: string | null;
  joined_at: string;
  revoked_at: string | null;
};

export type MemberListResponse = {
  tree_id: string;
  items: Member[];
};

export type Invitation = {
  id: string;
  tree_id: string;
  invitee_email: string;
  role: ShareRole;
  token: string;
  invite_url: string;
  expires_at: string;
  accepted_at: string | null;
  revoked_at: string | null;
  created_at: string;
};

export type InvitationListResponse = {
  tree_id: string;
  items: Invitation[];
};

export type InvitationAcceptResponse = {
  tree_id: string;
  membership_id: string;
  role: ShareRole;
};

export function fetchMembers(treeId: string): Promise<MemberListResponse> {
  return getJson<MemberListResponse>(`/trees/${treeId}/members`);
}

export function fetchInvitations(treeId: string): Promise<InvitationListResponse> {
  return getJson<InvitationListResponse>(`/trees/${treeId}/invitations`);
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const detail = await safeReadDetail(response);
    throw new ApiError(response.status, detail ?? `${path} failed with ${response.status}`);
  }
  return (await response.json()) as T;
}

async function patchJson<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const detail = await safeReadDetail(response);
    throw new ApiError(response.status, detail ?? `${path} failed with ${response.status}`);
  }
  return (await response.json()) as T;
}

async function deleteEmpty(path: string): Promise<void> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: "DELETE",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    const detail = await safeReadDetail(response);
    throw new ApiError(response.status, detail ?? `${path} failed with ${response.status}`);
  }
}

export function createInvitation(
  treeId: string,
  email: string,
  role: "editor" | "viewer",
): Promise<Invitation> {
  return postJson<Invitation>(`/trees/${treeId}/invitations`, { email, role });
}

export function revokeInvitation(invitationId: string): Promise<void> {
  return deleteEmpty(`/invitations/${invitationId}`);
}

export function acceptInvitation(token: string): Promise<InvitationAcceptResponse> {
  return postJson<InvitationAcceptResponse>(`/invitations/${token}/accept`, {});
}

export function updateMemberRole(membershipId: string, role: "editor" | "viewer"): Promise<Member> {
  return patchJson<Member>(`/memberships/${membershipId}`, { role });
}

export function revokeMember(membershipId: string): Promise<void> {
  return deleteEmpty(`/memberships/${membershipId}`);
}

export function resendInvitation(token: string): Promise<{
  invitation_id: string;
  invitee_email: string;
  resent_at: string;
  next_resend_allowed_at: string;
}> {
  return postJson(`/trees/invitations/${token}/resend`, {});
}

export function transferOwnership(
  treeId: string,
  newOwnerEmail: string,
  currentOwnerEmail: string,
): Promise<{
  tree_id: string;
  previous_owner_user_id: string;
  new_owner_user_id: string;
  transferred_at: string;
}> {
  return patchJson(`/trees/${treeId}/transfer-owner`, {
    new_owner_email: newOwnerEmail,
    current_owner_email_confirmation: currentOwnerEmail,
  });
}

/** Mask middle of an email so members list doesn't leak full address visually. */
export function maskEmail(email: string): string {
  const [local, domain] = email.split("@");
  if (!local || !domain) return email;
  if (local.length <= 2) return `${local[0]}*@${domain}`;
  return `${local[0]}${"*".repeat(Math.min(local.length - 2, 4))}${local[local.length - 1]}@${domain}`;
}

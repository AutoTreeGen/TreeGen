/**
 * Типизированный fetch-клиент к parser-service.
 *
 * Типы зеркалят ``services/parser-service/src/parser_service/schemas.py``.
 * При изменении Pydantic-схем — обновлять руками. Phase 4.2 заменит ручной
 * клиент на OpenAPI-codegen.
 */

const API_BASE = process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") ?? "http://localhost:8000";

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

// ---- HTTP error -------------------------------------------------------------

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function getJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { Accept: "application/json", ...init?.headers },
  });
  if (!response.ok) {
    throw new ApiError(
      response.status,
      `Request to ${path} failed with ${response.status} ${response.statusText}`,
    );
  }
  return (await response.json()) as T;
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

export function fetchSources(treeId: string, limit = 50, offset = 0): Promise<SourceListResponse> {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  return getJson<SourceListResponse>(`/trees/${treeId}/sources?${params.toString()}`);
}

export function fetchSource(sourceId: string): Promise<SourceDetail> {
  return getJson<SourceDetail>(`/sources/${sourceId}`);
}

export function fetchPersonCitations(personId: string): Promise<PersonCitationsResponse> {
  return getJson<PersonCitationsResponse>(`/persons/${personId}/citations`);
}
}

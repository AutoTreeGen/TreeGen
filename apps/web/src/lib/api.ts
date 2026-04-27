/**
 * Типизированный fetch-клиент к parser-service.
 *
 * Типы зеркалят ``services/parser-service/src/parser_service/schemas.py``.
 * При изменении Pydantic-схем — обновлять руками. Phase 4.2 заменит ручной
 * клиент на OpenAPI-codegen.
 */

const API_BASE = process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") ?? "http://localhost:8000";

// ---- Types (зеркало parser_service.schemas) ---------------------------------

export type PersonSummary = {
  id: string;
  gedcom_xref: string | null;
  sex: string;
  confidence_score: number;
  primary_name: string | null;
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

/**
 * Типизированный fetch-клиент к dna-service (Phase 6.3).
 *
 * Зеркало `services/dna-service/src/dna_service/schemas.py` — обновлять
 * руками при изменении Pydantic-схем (Phase 4.2 заменит на codegen).
 *
 * Privacy: SegmentSummary — это **agg-only** (chromosome/start/end/cM/snp_count).
 * Никаких rsid/genotypes на клиенте, см. ADR-0014 §«Privacy guards».
 */

import { ApiError } from "./api";

const DNA_API_BASE = (
  process.env.NEXT_PUBLIC_DNA_API_URL ??
  process.env.NEXT_PUBLIC_API_URL ??
  "http://localhost:8001"
).replace(/\/$/, "");

// ---- Types ---------------------------------------------------------------

export type DnaKitSummary = {
  id: string;
  tree_id: string;
  owner_user_id: string;
  person_id: string | null;
  source_platform: string;
  external_kit_id: string | null;
  display_name: string | null;
  ethnicity_population: string;
};

export type DnaKitListResponse = {
  owner_user_id: string;
  total: number;
  items: DnaKitSummary[];
};

export type DnaMatchListItem = {
  id: string;
  kit_id: string;
  tree_id: string;
  external_match_id: string | null;
  display_name: string | null;
  total_cm: number | null;
  largest_segment_cm: number | null;
  segment_count: number | null;
  predicted_relationship: string | null;
  confidence: string | null;
  shared_match_count: number | null;
  matched_person_id: string | null;
};

export type DnaMatchListResponse = {
  kit_id: string;
  total: number;
  limit: number;
  offset: number;
  min_cm: number | null;
  items: DnaMatchListItem[];
};

export type DnaMatchSegment = {
  chromosome: number;
  start_bp: number;
  end_bp: number;
  cm: number;
  num_snps: number | null;
};

export type DnaSharedAncestorHint = {
  label: string;
  person_id: string | null;
  source: string | null;
};

export type DnaMatchDetail = DnaMatchListItem & {
  notes: string | null;
  segments: DnaMatchSegment[];
  shared_ancestor_hint: DnaSharedAncestorHint | null;
};

// ---- Fetch helpers -------------------------------------------------------

async function getJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${DNA_API_BASE}${path}`, {
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

// ---- Public surface ------------------------------------------------------

export function fetchDnaKits(ownerUserId: string): Promise<DnaKitListResponse> {
  const params = new URLSearchParams({ owner_user_id: ownerUserId });
  return getJson<DnaKitListResponse>(`/dna-kits?${params.toString()}`);
}

export type DnaMatchesQuery = {
  limit?: number;
  offset?: number;
  /** Минимальный total cM. NULL/пропуск — без фильтра. */
  minCm?: number | null;
  /** Substring по `predicted_relationship` (case-insensitive). */
  predicted?: string | null;
};

export function fetchDnaMatches(
  kitId: string,
  query: DnaMatchesQuery = {},
): Promise<DnaMatchListResponse> {
  const { limit = 50, offset = 0, minCm = null, predicted = null } = query;
  const params = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
  });
  if (minCm !== null && Number.isFinite(minCm)) params.set("min_cm", String(minCm));
  if (predicted?.trim()) params.set("predicted", predicted.trim());
  return getJson<DnaMatchListResponse>(`/dna-kits/${kitId}/matches?${params.toString()}`);
}

export function fetchDnaMatchDetail(matchId: string): Promise<DnaMatchDetail> {
  return getJson<DnaMatchDetail>(`/dna-matches/${matchId}`);
}

export function linkDnaMatchToPerson(
  matchId: string,
  payload: { tree_id: string; person_id: string },
): Promise<DnaMatchDetail> {
  return getJson<DnaMatchDetail>(`/dna-matches/${matchId}/link`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function unlinkDnaMatch(matchId: string): Promise<DnaMatchDetail> {
  return getJson<DnaMatchDetail>(`/dna-matches/${matchId}/link`, {
    method: "DELETE",
  });
}

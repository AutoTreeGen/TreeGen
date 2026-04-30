/**
 * Phase 15.1 — relationship-level evidence API client.
 *
 * Зеркалит pydantic-схемы из
 * ``services/parser-service/src/parser_service/schemas.py``
 * (Relationship*Response). Тонкий wrapper над общим ``getJson``:
 * compute-on-demand endpoint, кэшируем через react-query на 60 сек
 * (sources меняются редко).
 */

import { fetchOnceJson } from "@/lib/api";

export type RelationshipKind = "parent_child" | "spouse" | "sibling";

export type RelationshipReference = {
  kind: RelationshipKind;
  subject_person_id: string;
  object_person_id: string;
};

export type RelationshipEvidenceSourceKind = "citation" | "inference_rule";

export type RelationshipEvidenceSource = {
  source_id: string | null;
  citation_id: string | null;
  title: string;
  repository: string | null;
  reliability: number | null;
  citation: string | null;
  snippet: string | null;
  url: string | null;
  added_at: string;
  kind: RelationshipEvidenceSourceKind;
  rule_id: string | null;
};

export type RelationshipEvidenceConfidenceMethod = "bayesian_fusion_v2" | "naive_count";

export type RelationshipEvidenceConfidence = {
  score: number;
  method: RelationshipEvidenceConfidenceMethod;
  computed_at: string;
  hypothesis_id: string | null;
};

export type RelationshipEvidenceProvenance = {
  source_files: string[];
  import_job_id: string | null;
  manual_edits: Array<Record<string, unknown>>;
};

export type RelationshipEvidenceResponse = {
  relationship: RelationshipReference;
  supporting: RelationshipEvidenceSource[];
  contradicting: RelationshipEvidenceSource[];
  confidence: RelationshipEvidenceConfidence;
  provenance: RelationshipEvidenceProvenance;
};

/** GET /trees/{tree_id}/relationships/{kind}/{subject}/{object}/evidence */
export async function fetchRelationshipEvidence(params: {
  treeId: string;
  kind: RelationshipKind;
  subjectId: string;
  objectId: string;
}): Promise<RelationshipEvidenceResponse> {
  const path = `/trees/${encodeURIComponent(params.treeId)}/relationships/${params.kind}/${encodeURIComponent(params.subjectId)}/${encodeURIComponent(params.objectId)}/evidence`;
  return fetchOnceJson<RelationshipEvidenceResponse>(path);
}

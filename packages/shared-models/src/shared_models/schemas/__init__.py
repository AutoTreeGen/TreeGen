"""Pydantic v2 DTO для shared-models.

Стандартный паттерн на сущность:

- ``XBase``    — общие поля (без id/timestamps).
- ``XCreate``  — для POST.
- ``XUpdate``  — для PATCH (все поля Optional).
- ``XRead``    — ответ API (включает id/timestamps/version_id).

Маппинг ORM → Pydantic — через ``model_config = ConfigDict(from_attributes=True)``.
"""

from __future__ import annotations

from shared_models.schemas.common import (
    ProvenanceSchema,
    SoftTimestamps,
    StatusFields,
)
from shared_models.schemas.dna import (
    DnaImportRead,
    DnaKitCreate,
    DnaKitRead,
    DnaMatchRead,
    SharedMatchRead,
)
from shared_models.schemas.entities import (
    EventCreate,
    EventRead,
    EventUpdate,
    FamilyCreate,
    FamilyRead,
    FamilyUpdate,
    NameCreate,
    NameRead,
    NameUpdate,
    PersonCreate,
    PersonRead,
    PersonUpdate,
    PlaceAliasCreate,
    PlaceAliasRead,
    PlaceCreate,
    PlaceRead,
    PlaceUpdate,
    SourceCreate,
    SourceRead,
    SourceUpdate,
)
from shared_models.schemas.evidence import (
    DocumentType,
    Provenance,
    ProvenanceChannel,
    default_unknown_provenance,
)
from shared_models.schemas.management import (
    ImportJobProgress,
    ImportJobRead,
    ImportStage,
    TreeCreate,
    TreeRead,
    TreeUpdate,
    UserCreate,
    UserRead,
)
from shared_models.schemas.merge import (
    MergeApplyBatchRead,
    MergeApplyRequest,
    MergeDecisionInput,
    MergeDecisionRead,
    MergeFieldHint,
    MergeRef,
    MergeSessionCreate,
    MergeSessionRead,
    MergeSessionSummary,
)

__all__ = [
    "DnaImportRead",
    "DnaKitCreate",
    "DnaKitRead",
    "DnaMatchRead",
    "DocumentType",
    "EventCreate",
    "EventRead",
    "EventUpdate",
    "FamilyCreate",
    "FamilyRead",
    "FamilyUpdate",
    "ImportJobProgress",
    "ImportJobRead",
    "ImportStage",
    "MergeApplyBatchRead",
    "MergeApplyRequest",
    "MergeDecisionInput",
    "MergeDecisionRead",
    "MergeFieldHint",
    "MergeRef",
    "MergeSessionCreate",
    "MergeSessionRead",
    "MergeSessionSummary",
    "NameCreate",
    "NameRead",
    "NameUpdate",
    "PersonCreate",
    "PersonRead",
    "PersonUpdate",
    "PlaceAliasCreate",
    "PlaceAliasRead",
    "PlaceCreate",
    "PlaceRead",
    "PlaceUpdate",
    "Provenance",
    "ProvenanceChannel",
    "ProvenanceSchema",
    "SharedMatchRead",
    "SoftTimestamps",
    "SourceCreate",
    "SourceRead",
    "SourceUpdate",
    "StatusFields",
    "TreeCreate",
    "TreeRead",
    "TreeUpdate",
    "UserCreate",
    "UserRead",
    "default_unknown_provenance",
]

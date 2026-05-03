"""SQLAlchemy 2 async ORM-модели AutoTreeGen.

Каждая модель — один файл. Импортируем все модули здесь, чтобы Alembic
``--autogenerate`` увидел их при загрузке ``Base.metadata``.
"""

from __future__ import annotations

from shared_models.orm.audio_session import AudioSession, AudioSessionStatus
from shared_models.orm.audit_log import AuditLog
from shared_models.orm.chat import ChatMessage, ChatMessageRole, ChatSession
from shared_models.orm.citation import Citation
from shared_models.orm.completeness_assertion import (
    CompletenessAssertion,
    CompletenessAssertionSource,
)
from shared_models.orm.dna_cluster import DnaCluster, DnaClusterMember
from shared_models.orm.dna_consent import DnaConsent
from shared_models.orm.dna_import import DnaImport
from shared_models.orm.dna_kit import DnaKit
from shared_models.orm.dna_match import DnaMatch
from shared_models.orm.dna_pile_up_region import DnaPileUpRegion
from shared_models.orm.dna_test_record import DnaTestRecord
from shared_models.orm.email_send_log import EmailSendLog
from shared_models.orm.event import Event, EventParticipant
from shared_models.orm.evidence import (
    DocumentTypeWeight,
    Evidence,
    reset_document_type_weight_cache,
)
from shared_models.orm.extracted_fact import ExtractedFact
from shared_models.orm.family import Family, FamilyChild
from shared_models.orm.fs_dedup_attempt import FsDedupAttempt
from shared_models.orm.hypothesis import Hypothesis, HypothesisEvidence
from shared_models.orm.hypothesis_compute_job import HypothesisComputeJob
from shared_models.orm.import_job import ImportJob
from shared_models.orm.membership import TreeInvitation, TreeMembership
from shared_models.orm.merge_session import (
    ChosenSource,
    DecisionMethod,
    MergeApplyBatch,
    MergeDecision,
    MergeDecisionScope,
    MergeRefKind,
    MergeSession,
    MergeSessionStatus,
)
from shared_models.orm.multimedia import EntityMultimedia, MultimediaObject
from shared_models.orm.name import Name
from shared_models.orm.note import EntityNote, Note
from shared_models.orm.notification import Notification
from shared_models.orm.notification_preference import NotificationPreference
from shared_models.orm.person import Person
from shared_models.orm.person_merge_log import PersonMergeLog
from shared_models.orm.place import Place, PlaceAlias
from shared_models.orm.public_tree_share import PublicTreeShare
from shared_models.orm.report_bundle_job import (
    BundleOutputFormat,
    BundleStatus,
    ReportBundleJob,
)
from shared_models.orm.seed_reference import (
    CountryArchiveDirectorySeed,
    FabricationPatternSeed,
    PlaceLookupSeed,
    SurnameTransliterationSeed,
    SurnameVariantSeed,
)
from shared_models.orm.shared_match import SharedMatch
from shared_models.orm.source import Source
from shared_models.orm.source_extraction import SourceExtraction
from shared_models.orm.stripe_customer import StripeCustomer
from shared_models.orm.stripe_event_log import StripeEventLog
from shared_models.orm.subscription import Subscription
from shared_models.orm.telegram_user_link import TelegramUserLink
from shared_models.orm.tree import Tree, TreeCollaborator
from shared_models.orm.user import User
from shared_models.orm.user_action_request import UserActionRequest
from shared_models.orm.version import Version
from shared_models.orm.voice_extracted_proposal import (
    ExtractionJobStatus,
    ProposalStatus,
    ProposalType,
    VoiceExtractedProposal,
)
from shared_models.orm.waitlist_entry import WaitlistEntry

__all__ = [
    "AudioSession",
    "AudioSessionStatus",
    "AuditLog",
    "BundleOutputFormat",
    "BundleStatus",
    "ChatMessage",
    "ChatMessageRole",
    "ChatSession",
    "ChosenSource",
    "Citation",
    "CompletenessAssertion",
    "CompletenessAssertionSource",
    "CountryArchiveDirectorySeed",
    "DecisionMethod",
    "DnaCluster",
    "DnaClusterMember",
    "DnaConsent",
    "DnaImport",
    "DnaKit",
    "DnaMatch",
    "DnaPileUpRegion",
    "DnaTestRecord",
    "DocumentTypeWeight",
    "EmailSendLog",
    "EntityMultimedia",
    "EntityNote",
    "Event",
    "EventParticipant",
    "Evidence",
    "ExtractedFact",
    "ExtractionJobStatus",
    "FabricationPatternSeed",
    "Family",
    "FamilyChild",
    "FsDedupAttempt",
    "Hypothesis",
    "HypothesisComputeJob",
    "HypothesisEvidence",
    "ImportJob",
    "MergeApplyBatch",
    "MergeDecision",
    "MergeDecisionScope",
    "MergeRefKind",
    "MergeSession",
    "MergeSessionStatus",
    "MultimediaObject",
    "Name",
    "Note",
    "Notification",
    "NotificationPreference",
    "Person",
    "PersonMergeLog",
    "Place",
    "PlaceAlias",
    "ProposalStatus",
    "ProposalType",
    "PlaceLookupSeed",
    "PublicTreeShare",
    "ReportBundleJob",
    "SharedMatch",
    "Source",
    "SourceExtraction",
    "StripeCustomer",
    "StripeEventLog",
    "Subscription",
    "SurnameTransliterationSeed",
    "SurnameVariantSeed",
    "TelegramUserLink",
    "Tree",
    "TreeCollaborator",
    "TreeInvitation",
    "TreeMembership",
    "User",
    "UserActionRequest",
    "Version",
    "VoiceExtractedProposal",
    "WaitlistEntry",
    "reset_document_type_weight_cache",
]

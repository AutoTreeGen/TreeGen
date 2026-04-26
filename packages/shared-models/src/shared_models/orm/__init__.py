"""SQLAlchemy 2 async ORM-модели AutoTreeGen.

Каждая модель — один файл. Импортируем все модули здесь, чтобы Alembic
``--autogenerate`` увидел их при загрузке ``Base.metadata``.
"""

from __future__ import annotations

from shared_models.orm.audit_log import AuditLog
from shared_models.orm.citation import Citation
from shared_models.orm.dna_import import DnaImport
from shared_models.orm.dna_kit import DnaKit
from shared_models.orm.dna_match import DnaMatch
from shared_models.orm.event import Event, EventParticipant
from shared_models.orm.family import Family, FamilyChild
from shared_models.orm.import_job import ImportJob
from shared_models.orm.multimedia import EntityMultimedia, MultimediaObject
from shared_models.orm.name import Name
from shared_models.orm.note import EntityNote, Note
from shared_models.orm.person import Person
from shared_models.orm.place import Place, PlaceAlias
from shared_models.orm.shared_match import SharedMatch
from shared_models.orm.source import Source
from shared_models.orm.tree import Tree, TreeCollaborator
from shared_models.orm.user import User
from shared_models.orm.version import Version

__all__ = [
    "AuditLog",
    "Citation",
    "DnaImport",
    "DnaKit",
    "DnaMatch",
    "EntityMultimedia",
    "EntityNote",
    "Event",
    "EventParticipant",
    "Family",
    "FamilyChild",
    "ImportJob",
    "MultimediaObject",
    "Name",
    "Note",
    "Person",
    "Place",
    "PlaceAlias",
    "SharedMatch",
    "Source",
    "Tree",
    "TreeCollaborator",
    "User",
    "Version",
]

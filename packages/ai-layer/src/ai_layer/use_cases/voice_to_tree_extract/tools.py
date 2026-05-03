"""Tool schemas для 3-pass voice extraction (ADR-0075 §«Tool schema»).

5 tools, разделённые по passes через явный allowlist. Анти-allowlist tool-call
(модель попыталась использовать tool не из своего pass'а) — ignored, log как
``unexpected_tool`` в телеметрии.

Schemas — JSON-Schema dialect, который Anthropic SDK принимает в ``tools=``.
Минимально-инвазивные (брать что есть в transcript'е, не придумывать). Все
tools требуют ``confidence`` + ``evidence_snippets`` — это работает как
quote-grounding (ADR-0059 паттерн): caller валидирует, что snippet существует
в transcript'е.
"""

from __future__ import annotations

from typing import Any, Final

# Pass 1 — entities (persons + places).

TOOL_CREATE_PERSON: Final[dict[str, Any]] = {
    "name": "create_person",
    "description": (
        "Predict a person mentioned in the transcript. "
        "Each fact must be backed by an evidence_snippet (verbatim from transcript)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "given_name": {"type": "string"},
            "surname": {"type": "string"},
            "patronymic": {"type": "string"},
            "sex": {"type": "string", "enum": ["M", "F", "U"]},
            "birth_year_estimate": {
                "type": "integer",
                "minimum": 1500,
                "maximum": 2100,
            },
            "death_year_estimate": {
                "type": "integer",
                "minimum": 1500,
                "maximum": 2100,
            },
            "is_alive": {"type": "boolean"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "evidence_snippets": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
            },
        },
        "required": ["confidence", "evidence_snippets"],
    },
}

TOOL_ADD_PLACE: Final[dict[str, Any]] = {
    "name": "add_place",
    "description": (
        "Predict a place (city, shtetl, region, country) mentioned in the transcript. "
        "Use the most-specific level you can support with the snippet."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "name_raw": {"type": "string"},
            "place_type": {
                "type": "string",
                "enum": ["city", "town", "shtetl", "region", "country", "unknown"],
            },
            "country_hint": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "evidence_snippets": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
            },
        },
        "required": ["name_raw", "confidence", "evidence_snippets"],
    },
}

# Pass 2 — relationships.

TOOL_LINK_RELATIONSHIP: Final[dict[str, Any]] = {
    "name": "link_relationship",
    "description": (
        "Link two persons from pass 1. Refer to them by `subject_index` / `object_index` "
        "(1-based indices into the persons array provided in the user message)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "subject_index": {"type": "integer", "minimum": 1},
            "object_index": {"type": "integer", "minimum": 1},
            "relation": {
                "type": "string",
                "enum": ["parent_of", "spouse_of", "sibling_of", "witness_of"],
            },
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "evidence_snippets": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
            },
        },
        "required": [
            "subject_index",
            "object_index",
            "relation",
            "confidence",
            "evidence_snippets",
        ],
    },
}

# Pass 3 — temporal-spatial events.

TOOL_ADD_EVENT: Final[dict[str, Any]] = {
    "name": "add_event",
    "description": (
        "Anchor a temporal-spatial event to a person from pass 1. "
        "Date precision: prefer year; range OK. place_index is 1-based into pass-1 places[]."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "person_index": {"type": "integer", "minimum": 1},
            "event_type": {
                "type": "string",
                "enum": [
                    "birth",
                    "death",
                    "marriage",
                    "migration",
                    "occupation",
                    "other",
                ],
            },
            "date_start_year": {
                "type": "integer",
                "minimum": 1500,
                "maximum": 2100,
            },
            "date_end_year": {
                "type": "integer",
                "minimum": 1500,
                "maximum": 2100,
            },
            "place_index": {"type": "integer", "minimum": 1},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "evidence_snippets": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
            },
        },
        "required": [
            "person_index",
            "event_type",
            "confidence",
            "evidence_snippets",
        ],
    },
}

# Все 3 passes — эскейп-hatch для модели когда что-то не лезет в schema.

TOOL_FLAG_UNCERTAIN: Final[dict[str, Any]] = {
    "name": "flag_uncertain",
    "description": (
        "Use when the transcript mentions something genealogically relevant "
        "but cannot fit into the structured tools (e.g. ambiguous pronoun, "
        "contradictory dates). The reviewer will resolve manually."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "enum": [
                    "ambiguous_reference",
                    "contradiction",
                    "unparseable_date",
                    "unknown_relation",
                    "other",
                ],
            },
            "note": {"type": "string"},
            "evidence_snippets": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
            },
        },
        "required": ["category", "note", "evidence_snippets"],
    },
}

# Per-pass allowlist'ы (передаются в Anthropic ``tools=``)
PASS_1_TOOLS: Final[list[dict[str, Any]]] = [
    TOOL_CREATE_PERSON,
    TOOL_ADD_PLACE,
    TOOL_FLAG_UNCERTAIN,
]
PASS_2_TOOLS: Final[list[dict[str, Any]]] = [
    TOOL_LINK_RELATIONSHIP,
    TOOL_FLAG_UNCERTAIN,
]
PASS_3_TOOLS: Final[list[dict[str, Any]]] = [
    TOOL_ADD_EVENT,
    TOOL_FLAG_UNCERTAIN,
]

TOOLS_BY_PASS: Final[dict[int, list[dict[str, Any]]]] = {
    1: PASS_1_TOOLS,
    2: PASS_2_TOOLS,
    3: PASS_3_TOOLS,
}


def pass_allowed_tool_names(pass_number: int) -> set[str]:
    """Имена tool'ов, разрешённых для данного pass'а (для unexpected-фильтра)."""
    return {tool["name"] for tool in TOOLS_BY_PASS[pass_number]}


__all__ = [
    "PASS_1_TOOLS",
    "PASS_2_TOOLS",
    "PASS_3_TOOLS",
    "TOOLS_BY_PASS",
    "TOOL_ADD_EVENT",
    "TOOL_ADD_PLACE",
    "TOOL_CREATE_PERSON",
    "TOOL_FLAG_UNCERTAIN",
    "TOOL_LINK_RELATIONSHIP",
    "pass_allowed_tool_names",
]

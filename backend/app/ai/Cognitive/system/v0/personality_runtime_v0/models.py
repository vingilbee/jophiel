"""Immutable source and error contracts for Personality Runtime Mapper V0.

The source is a closed projection of exactly the ten CoreMemory values V0
consumes. It is built from plain JSON groups the application layer copies out
of the ORM row; this module never sees a database object.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, PlainSerializer, model_validator

PERSONALITY_SOURCE_SCHEMA_VERSION = "core-personality-source-v0.1"
PERSONALITY_MAPPING_MANIFEST_VERSION = "personality-runtime-mapping-v0.1"
PERSONALITY_MAPPER_VERSION = "personality-runtime-mapper-v0.1"

# CoreMemory.schema_version values whose group layout V0 understands.
SUPPORTED_CORE_SCHEMA_VERSIONS: frozenset[int] = frozenset({1})


class PersonalityMappingErrorCode(StrEnum):
    CORE_MEMORY_UNAVAILABLE = "core_memory_unavailable"
    CORE_MEMORY_INACTIVE = "core_memory_inactive"
    CORE_MEMORY_SCHEMA_UNSUPPORTED = "core_memory_schema_unsupported"
    CORE_MEMORY_LINEAGE_INVALID = "core_memory_lineage_invalid"
    SOURCE_FIELD_MISSING = "personality_source_field_missing"
    SOURCE_TYPE_INVALID = "personality_source_type_invalid"
    SOURCE_NONFINITE = "personality_source_nonfinite"
    SOURCE_OUT_OF_RANGE = "personality_source_out_of_range"
    PROJECTION_INVALID = "personality_projection_invalid"
    INTERNAL_CONTRACT = "personality_mapper_internal_contract"


_SAFE_MESSAGES = {
    PersonalityMappingErrorCode.CORE_MEMORY_UNAVAILABLE: "No core identity record is available.",
    PersonalityMappingErrorCode.CORE_MEMORY_INACTIVE: "The core identity record is not active.",
    PersonalityMappingErrorCode.CORE_MEMORY_SCHEMA_UNSUPPORTED: "The core identity schema version is unsupported.",
    PersonalityMappingErrorCode.CORE_MEMORY_LINEAGE_INVALID: "The core identity lineage is invalid.",
    PersonalityMappingErrorCode.SOURCE_FIELD_MISSING: "A required personality source value is missing.",
    PersonalityMappingErrorCode.SOURCE_TYPE_INVALID: "A personality source value is not a JSON number.",
    PersonalityMappingErrorCode.SOURCE_NONFINITE: "A personality source value is not finite.",
    PersonalityMappingErrorCode.SOURCE_OUT_OF_RANGE: "A personality source value is outside [0, 1].",
    PersonalityMappingErrorCode.PROJECTION_INVALID: "The compiled personality projection failed validation.",
    PersonalityMappingErrorCode.INTERNAL_CONTRACT: "The personality mapper violated its own contract.",
}


class PersonalityMappingError(ValueError):
    """A typed, content-free mapping failure.

    ``field`` names a source path only (never a value), so logs cannot leak
    personality content.
    """

    def __init__(self, code: PersonalityMappingErrorCode, *, field: str | None = None) -> None:
        self.code = code
        self.field = field
        self.safe_message = _SAFE_MESSAGES[code]
        super().__init__(self.safe_message)


def unit_decimal(value: Any, *, field: str = "value") -> Decimal:
    """Validate one consumed value: a finite JSON number in [0, 1].

    Booleans, strings, containers, and null are rejected rather than coerced.
    Floats convert through ``Decimal(str(value))`` so the decimal equals the
    number as written in JSON.
    """

    if value is None:
        raise PersonalityMappingError(PersonalityMappingErrorCode.SOURCE_FIELD_MISSING, field=field)
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise PersonalityMappingError(PersonalityMappingErrorCode.SOURCE_TYPE_INVALID, field=field)
    if isinstance(value, float) and not math.isfinite(value):
        raise PersonalityMappingError(PersonalityMappingErrorCode.SOURCE_NONFINITE, field=field)
    if isinstance(value, Decimal) and not value.is_finite():
        raise PersonalityMappingError(PersonalityMappingErrorCode.SOURCE_NONFINITE, field=field)
    decimal = value if isinstance(value, Decimal) else Decimal(str(value))
    if not Decimal(0) <= decimal <= Decimal(1):
        raise PersonalityMappingError(PersonalityMappingErrorCode.SOURCE_OUT_OF_RANGE, field=field)
    return decimal


UnitDecimal = Annotated[
    Decimal,
    BeforeValidator(unit_decimal),
    # JSON numbers, not strings: the round trip re-enters ``unit_decimal``.
    PlainSerializer(float, return_type=float, when_used="json"),
]


def core_memory_ref_for(name: str, version: int) -> str:
    return f"core_memory:{name}:v{version}"


def validate_core_identity(*, name: Any, version: Any, schema_version: Any) -> str:
    """Return the lineage ref, or raise a typed code for an unusable identity."""

    if isinstance(schema_version, bool) or not isinstance(schema_version, int) or schema_version < 1:
        raise PersonalityMappingError(PersonalityMappingErrorCode.CORE_MEMORY_SCHEMA_UNSUPPORTED)
    if schema_version not in SUPPORTED_CORE_SCHEMA_VERSIONS:
        raise PersonalityMappingError(PersonalityMappingErrorCode.CORE_MEMORY_SCHEMA_UNSUPPORTED)
    if not isinstance(name, str) or not name.strip() or name != name.strip() or ":" in name:
        raise PersonalityMappingError(PersonalityMappingErrorCode.CORE_MEMORY_LINEAGE_INVALID)
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise PersonalityMappingError(PersonalityMappingErrorCode.CORE_MEMORY_LINEAGE_INVALID)
    return core_memory_ref_for(name, version)


class _FrozenSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CorePersonalitySourceV0(_FrozenSource):
    source_schema_version: Literal["core-personality-source-v0.1"] = PERSONALITY_SOURCE_SCHEMA_VERSION
    core_memory_ref: str
    core_memory_name: str
    core_memory_version: int
    core_schema_version: int

    optimism_prior: UnitDecimal
    charitable_interpretation: UnitDecimal
    trust_prior: UnitDecimal
    ambiguity_tolerance: UnitDecimal
    threat_sensitivity: UnitDecimal
    affiliation: UnitDecimal

    curiosity: UnitDecimal
    warmth: UnitDecimal
    playfulness: UnitDecimal
    directness: UnitDecimal

    @model_validator(mode="after")
    def lineage(self) -> CorePersonalitySourceV0:
        expected = validate_core_identity(
            name=self.core_memory_name, version=self.core_memory_version,
            schema_version=self.core_schema_version,
        )
        if self.core_memory_ref != expected:
            raise PersonalityMappingError(PersonalityMappingErrorCode.CORE_MEMORY_LINEAGE_INVALID)
        return self


# The only CoreMemory paths V0 reads, keyed by source field.
SOURCE_PATHS: dict[str, tuple[str, str]] = {
    "optimism_prior": ("interpretation_biases", "optimism_prior"),
    "charitable_interpretation": ("interpretation_biases", "charitable_interpretation"),
    "trust_prior": ("interpretation_biases", "trust_prior"),
    "ambiguity_tolerance": ("interpretation_biases", "ambiguity_tolerance"),
    "threat_sensitivity": ("perception_priors", "threat_sensitivity"),
    "affiliation": ("drives", "affiliation"),
    "curiosity": ("drives", "curiosity"),
    "warmth": ("action_style", "warmth"),
    "playfulness": ("action_style", "playfulness"),
    "directness": ("action_style", "directness"),
}


def source_path(field: str) -> str:
    group, key = SOURCE_PATHS[field]
    return f"{group}.{key}"


def build_source(
    *, name: Any, version: Any, schema_version: Any, groups: Mapping[str, Any],
) -> CorePersonalitySourceV0:
    """Copy exactly the ten consumed paths from plain CoreMemory JSON groups.

    Every other group and key is ignored. Any missing, null, Boolean,
    nonnumeric, non-finite, or out-of-range consumed value fails the whole
    build with a typed code; nothing is defaulted.
    """

    core_ref = validate_core_identity(name=name, version=version, schema_version=schema_version)
    values: dict[str, Decimal] = {}
    for field, (group_name, key) in SOURCE_PATHS.items():
        group = groups.get(group_name)
        path = source_path(field)
        if not isinstance(group, Mapping) or key not in group:
            raise PersonalityMappingError(PersonalityMappingErrorCode.SOURCE_FIELD_MISSING, field=path)
        values[field] = unit_decimal(group[key], field=path)
    try:
        return CorePersonalitySourceV0(
            core_memory_ref=core_ref, core_memory_name=name, core_memory_version=version,
            core_schema_version=schema_version, **values,
        )
    except PersonalityMappingError:
        raise
    except ValueError as exc:
        raise PersonalityMappingError(PersonalityMappingErrorCode.INTERNAL_CONTRACT) from exc


__all__ = [
    "CorePersonalitySourceV0", "PERSONALITY_MAPPER_VERSION", "PERSONALITY_MAPPING_MANIFEST_VERSION",
    "PERSONALITY_SOURCE_SCHEMA_VERSION", "PersonalityMappingError", "PersonalityMappingErrorCode",
    "SOURCE_PATHS", "SUPPORTED_CORE_SCHEMA_VERSIONS", "build_source", "core_memory_ref_for", "source_path",
    "unit_decimal", "validate_core_identity",
]

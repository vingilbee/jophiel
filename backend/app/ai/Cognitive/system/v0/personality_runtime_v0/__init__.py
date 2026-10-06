"""Personality Runtime Mapper V0: a pure, deterministic identity-configuration compiler.

It compiles one authorized CoreMemory source into the three canonical
personality profiles. It is not a learner, an assessment, or a user profile,
and it never infers personality from conversation text.
"""

from .manifest import MANIFEST, MappingManifest, MappingRule, OutputProfile, Transform
from .mapper import PersonalityRuntimeMapperV0, projection_id_for
from .models import (
    PERSONALITY_MAPPER_VERSION,
    PERSONALITY_MAPPING_MANIFEST_VERSION,
    PERSONALITY_SOURCE_SCHEMA_VERSION,
    CorePersonalitySourceV0,
    PersonalityMappingError,
    PersonalityMappingErrorCode,
    build_source,
    validate_core_identity,
)

__all__ = [
    "MANIFEST", "MappingManifest", "MappingRule", "OutputProfile", "PERSONALITY_MAPPER_VERSION",
    "PERSONALITY_MAPPING_MANIFEST_VERSION", "PERSONALITY_SOURCE_SCHEMA_VERSION", "CorePersonalitySourceV0",
    "PersonalityMappingError", "PersonalityMappingErrorCode", "PersonalityRuntimeMapperV0", "Transform",
    "build_source", "projection_id_for", "validate_core_identity",
]

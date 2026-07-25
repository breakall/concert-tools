"""Utilities for inspecting and transforming concert bundles."""

from .catalog import scan_patch_bundles
from .composition import compose_patch
from .concert import ConcertInfo, InspectionIssue, inspect_concert, load_concert_data
from .enumeration import (
    ChannelStripEntry,
    PatchEntry,
    PluginEntry,
    PluginParameter,
    enrich_parameters,
    enumerate_channel_strips,
    enumerate_parameters,
    enumerate_patches,
    enumerate_plugins,
)
from .manifest import ChannelManifest, PatchManifest, inspect_patch_manifest
from .parameter_metadata import (
    ParameterMetadataEntry,
    ParameterMetadataTable,
    default_parameter_metadata_roots,
    discover_parameter_metadata,
    load_parameter_metadata,
)
from .patch import PatchInfo, clone_patch, inspect_patch, read_patch_text, rewrite_patch_text
from .validation import ValidationIssue, validate_concert

__all__ = [
    "ChannelManifest",
    "ChannelStripEntry",
    "ConcertInfo",
    "InspectionIssue",
    "ParameterMetadataEntry",
    "ParameterMetadataTable",
    "PatchEntry",
    "PatchInfo",
    "PatchManifest",
    "PluginEntry",
    "PluginParameter",
    "ValidationIssue",
    "clone_patch",
    "compose_patch",
    "default_parameter_metadata_roots",
    "discover_parameter_metadata",
    "enrich_parameters",
    "enumerate_channel_strips",
    "enumerate_parameters",
    "enumerate_patches",
    "enumerate_plugins",
    "inspect_concert",
    "inspect_patch",
    "inspect_patch_manifest",
    "load_concert_data",
    "load_parameter_metadata",
    "read_patch_text",
    "rewrite_patch_text",
    "scan_patch_bundles",
    "validate_concert",
]
__version__ = "0.1.0"

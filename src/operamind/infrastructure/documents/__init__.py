"""Safe source-document adapters that emit domain-level structural signals."""

from operamind.infrastructure.documents.office import (
    DocumentSignalExtractorRegistry,
    ExtractionLimits,
    OfficeDocumentError,
    OfficeDocumentSecurityError,
    UnsupportedDocumentTypeError,
)

__all__ = [
    "DocumentSignalExtractorRegistry",
    "ExtractionLimits",
    "OfficeDocumentError",
    "OfficeDocumentSecurityError",
    "UnsupportedDocumentTypeError",
]

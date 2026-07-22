"""Safe source-document adapters that emit domain-level structural signals."""

from operamind.infrastructure.documents.office import (
    DocumentSignalExtractorRegistry,
    ExtractionLimits,
    OfficeDocumentError,
    OfficeDocumentSecurityError,
    UnsupportedDocumentTypeError,
)
from operamind.infrastructure.documents.proposal import (
    DocumentCellChange,
    DocumentProposalWriteResult,
    XlsxDocumentProposalWriter,
)

__all__ = [
    "DocumentCellChange",
    "DocumentProposalWriteResult",
    "DocumentSignalExtractorRegistry",
    "ExtractionLimits",
    "OfficeDocumentError",
    "OfficeDocumentSecurityError",
    "UnsupportedDocumentTypeError",
    "XlsxDocumentProposalWriter",
]

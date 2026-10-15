from .base import DocumentRecord, MeetingRecord, SourceAdapter
from .legistar import LegistarAdapter
from .primegov import PrimeGovAdapter
from .granicus import GranicusAdapter
from .pdf_directory import PdfDirectoryAdapter

ADAPTERS = {
    "legistar": LegistarAdapter,
    "primegov": PrimeGovAdapter,
    "granicus": GranicusAdapter,
    "pdf_directory": PdfDirectoryAdapter,
}


def build_adapter(source_type, source_config, http_get):
    try:
        cls = ADAPTERS[source_type]
    except KeyError:
        raise ValueError(
            f"unknown source_type {source_type!r}, expected one of {sorted(ADAPTERS)}"
        )
    return cls(source_config, http_get)


__all__ = [
    "DocumentRecord",
    "MeetingRecord",
    "SourceAdapter",
    "ADAPTERS",
    "build_adapter",
]

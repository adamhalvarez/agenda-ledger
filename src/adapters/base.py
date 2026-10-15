"""Source adapter interface.

An adapter's job is discovery only: given a body's source_config, say what
meetings exist and what documents belong to each one. It never fetches raw
bytes for hashing and never touches Postgres or storage -- scan.py does that
identically for every adapter, so caching, hashing, and storage never fork
per source type. This is also why an adapter takes an injected `http_get`:
production wires in httpclient.real_http_get, tests wire in a
FixtureHttpClient, and neither the adapter nor scan.py needs to know which.
"""

from dataclasses import dataclass, field
from typing import Iterator, Tuple


@dataclass(frozen=True)
class DocumentRecord:
    doc_type: str  # 'agenda' | 'minutes' | 'attachment' | 'other'
    source_url: str


@dataclass(frozen=True)
class MeetingRecord:
    meeting_date: str  # ISO date, YYYY-MM-DD
    source_url: str
    documents: Tuple[DocumentRecord, ...] = field(default_factory=tuple)


class SourceAdapter:
    def __init__(self, source_config: dict, http_get):
        self.source_config = source_config or {}
        self.http_get = http_get

    def discover(self) -> Iterator[MeetingRecord]:
        raise NotImplementedError

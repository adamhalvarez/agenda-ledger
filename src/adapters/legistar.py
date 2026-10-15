"""Legistar public read API.

webservices.legistar.com/v1/<client>/Events returns JSON, unauthenticated,
for any city running Legistar. None of this pilot's three LA bodies turned
out to run on Legistar (verified live 2026-08-30: lacity, losangeles, and
la-city all 404 on webservices.legistar.com) -- LA City Council runs on
PrimeGov instead, see primegov.py. This adapter is still a required piece of
the source-adapter interface for the many other cities that do run Legistar,
and it is wired into bodies.yaml.example so a future body needs only a config
entry, no code.

Field names (EventId, EventBodyId, EventDate, EventAgendaFile,
EventMinutesFile, EventInSiteURL) come from Legistar's documented public
schema, not a live capture against one of the pilot bodies -- there was
nothing in the pilot to capture them from. Revalidate against a live
Legistar client's actual response the first time this adapter is pointed at
a real body.
"""

from datetime import date, timedelta

from .base import DocumentRecord, MeetingRecord, SourceAdapter

BASE = "https://webservices.legistar.com/v1"


class LegistarAdapter(SourceAdapter):
    """source_config:
    client        Legistar client slug, e.g. "lacounty"        (required)
    event_body_id Legistar EventBodyId to filter to one body   (optional)
    days_back     how far back to look for events, default 30 (optional)
    """

    def discover(self):
        client = self.source_config["client"]
        days_back = self.source_config.get("days_back", 30)
        body_filter = self.source_config.get("event_body_id")

        cutoff = (date.today() - timedelta(days=days_back)).isoformat()
        url = f"{BASE}/{client}/Events"
        params = {"$filter": f"EventDate ge datetime'{cutoff}'"}

        resp = self.http_get(url, params=params)
        events = resp.json()

        for ev in events:
            if body_filter is not None and ev.get("EventBodyId") != body_filter:
                continue

            docs = []
            if ev.get("EventAgendaFile"):
                docs.append(DocumentRecord("agenda", ev["EventAgendaFile"]))
            if ev.get("EventMinutesFile"):
                docs.append(DocumentRecord("minutes", ev["EventMinutesFile"]))
            if not docs:
                continue

            meeting_date = ev["EventDate"].split("T")[0]
            source_url = ev.get("EventInSiteURL") or url
            yield MeetingRecord(meeting_date, source_url, tuple(docs))

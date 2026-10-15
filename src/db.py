"""Postgres access, service role only. Every write here is either an upsert
keyed on a real-world identity (body name+jurisdiction, meeting date+url,
document url) or a plain insert into the append-only document_versions
table -- there is no update path into document_versions from this module,
matching the DB trigger that forbids it outright.
"""

import os

from supabase import create_client

_client = None


def get_client():
    global _client
    if _client is None:
        url = os.environ["SUPABASE_URL"]
        key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
        _client = create_client(url, key)
    return _client


def sync_body(name, jurisdiction, source_type, source_config, active=True):
    client = get_client()
    row = {
        "name": name,
        "jurisdiction": jurisdiction,
        "source_type": source_type,
        "source_config": source_config,
        "active": active,
    }
    res = (
        client.table("bodies")
        .upsert(row, on_conflict="name,jurisdiction")
        .execute()
    )
    return res.data[0]["id"]


def get_or_create_meeting(body_id, meeting_date, source_url):
    client = get_client()
    row = {"body_id": body_id, "meeting_date": meeting_date, "source_url": source_url}
    res = (
        client.table("meetings")
        .upsert(row, on_conflict="body_id,meeting_date,source_url", ignore_duplicates=True)
        .execute()
    )
    if res.data:
        return res.data[0]["id"], True
    existing = (
        client.table("meetings")
        .select("id")
        .eq("body_id", body_id)
        .eq("meeting_date", meeting_date)
        .eq("source_url", source_url)
        .limit(1)
        .execute()
    )
    return existing.data[0]["id"], False


def get_or_create_document(meeting_id, doc_type, source_url):
    client = get_client()
    row = {"meeting_id": meeting_id, "doc_type": doc_type, "source_url": source_url}
    res = (
        client.table("documents")
        .upsert(row, on_conflict="meeting_id,source_url", ignore_duplicates=True)
        .execute()
    )
    if res.data:
        return res.data[0]["id"], True
    existing = (
        client.table("documents")
        .select("id")
        .eq("meeting_id", meeting_id)
        .eq("source_url", source_url)
        .limit(1)
        .execute()
    )
    return existing.data[0]["id"], False


def latest_version(document_id):
    client = get_client()
    res = (
        client.table("document_versions")
        .select("id,sha256,captured_at,text_extracted")
        .eq("document_id", document_id)
        .order("captured_at", desc=True)
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else None


def version_exists(document_id, sha256):
    client = get_client()
    res = (
        client.table("document_versions")
        .select("id")
        .eq("document_id", document_id)
        .eq("sha256", sha256)
        .limit(1)
        .execute()
    )
    return bool(res.data)


def insert_version(document_id, sha256, storage_path, text_extracted, prev_version_id, diff_summary=None):
    client = get_client()
    row = {
        "document_id": document_id,
        "sha256": sha256,
        "storage_path": storage_path,
        "text_extracted": text_extracted,
        "prev_version_id": prev_version_id,
        "diff_summary": diff_summary,
    }
    res = client.table("document_versions").insert(row).execute()
    return res.data[0]["id"]


def versions_missing_extraction(limit=100):
    """document_versions with no agenda_items row yet. Postgrest has no
    NOT EXISTS join, so this is two queries: every version id already
    extracted, then every version excluding those -- fine at this pilot's
    scale, and boring beats a database function for a query this simple."""
    client = get_client()
    extracted = client.table("agenda_items").select("document_version_id").execute()
    extracted_ids = {row["document_version_id"] for row in (extracted.data or [])}

    versions = (
        client.table("document_versions")
        .select("id,document_id,text_extracted,captured_at,prev_version_id")
        .order("captured_at", desc=False)
        .limit(limit)
        .execute()
    )
    return [v for v in (versions.data or []) if v["id"] not in extracted_ids]


def version_text(version_id):
    client = get_client()
    res = client.table("document_versions").select("text_extracted").eq("id", version_id).limit(1).execute()
    return res.data[0]["text_extracted"] if res.data else None


def meeting_id_for_document(document_id):
    client = get_client()
    res = client.table("documents").select("meeting_id").eq("id", document_id).limit(1).execute()
    return res.data[0]["meeting_id"] if res.data else None


def meeting_date_for_meeting(meeting_id):
    client = get_client()
    res = client.table("meetings").select("meeting_date").eq("id", meeting_id).limit(1).execute()
    return res.data[0]["meeting_date"] if res.data else None


def insert_agenda_item(meeting_id, document_version_id, item):
    client = get_client()
    row = {
        "meeting_id": meeting_id,
        "document_version_id": document_version_id,
        "item_number": item.get("item_number"),
        "title": item["title"],
        "action_type": item.get("action_type"),
        "dollar_amount": item.get("dollar_amount"),
        "addresses": item.get("addresses", []),
        "entities": item.get("entities", []),
        "extraction_model": item.get("extraction_model"),
        "extraction_prompt_version": item.get("extraction_prompt_version"),
    }
    res = client.table("agenda_items").insert(row).execute()
    return res.data[0]["id"]


def insert_mention(agenda_item_id, mention):
    """Skips (rather than errors) a mention already recorded for this item
    -- the same (agenda_item_id, url) can legitimately be discovered twice
    if an entity's GDELT and outlet-RSS windows overlap."""
    client = get_client()
    row = {
        "agenda_item_id": agenda_item_id,
        "source": mention["source"],
        "url": mention["url"],
        "published_at": mention.get("published_at"),
        "matched_entity": mention.get("matched_entity"),
    }
    res = (
        client.table("mentions")
        .upsert(row, on_conflict="agenda_item_id,url", ignore_duplicates=True)
        .execute()
    )
    return res.data[0]["id"] if res.data else None


def insert_salience_score(agenda_item_id, record_activity, coverage_count, ratio):
    client = get_client()
    row = {
        "agenda_item_id": agenda_item_id,
        "record_activity": record_activity,
        "coverage_count": coverage_count,
        "ratio": ratio,
    }
    res = client.table("salience_scores").insert(row).execute()
    return res.data[0]["id"]


def least_covered_items(limit=25):
    client = get_client()
    res = client.table("least_covered_items").select("*").order("ratio", desc=True).limit(limit).execute()
    return res.data or []

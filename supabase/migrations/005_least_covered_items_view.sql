-- Phase 3 report: the least covered agenda items for the most recent
-- meeting of each active body, highest ratio (least coverage relative to
-- record activity) first. A view rather than client-side joins through
-- PostgREST, since "most recent meeting per body" is a window-function
-- query Postgres is the right tool for.
create view least_covered_items as
with latest_meetings as (
    select distinct on (body_id) id as meeting_id, body_id, meeting_date
    from meetings
    order by body_id, meeting_date desc
)
select
    b.id as body_id,
    b.name as body_name,
    lm.meeting_id,
    lm.meeting_date,
    ai.id as agenda_item_id,
    ai.item_number,
    ai.title,
    ai.action_type,
    ai.dollar_amount,
    ai.document_version_id,
    ss.record_activity,
    ss.coverage_count,
    ss.ratio,
    ss.computed_at
from latest_meetings lm
join bodies b on b.id = lm.body_id
join agenda_items ai on ai.meeting_id = lm.meeting_id
join salience_scores ss on ss.agenda_item_id = ai.id
where b.active
order by b.name, ss.ratio desc;

alter view least_covered_items set (security_invoker = true);

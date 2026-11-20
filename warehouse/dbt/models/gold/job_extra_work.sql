-- Per job: did the customer ask for extra work, did the contractor agree, did the crew do it?
with m as (select * from {{ source('signals', 'message_labels') }}),
requests as (
  select job_id, min(sent_at) as asked_at, any_value(item) as item
  from m where label = 'extra_request' group by 1
),
cancels as (
  select distinct job_id, item from m where label = 'cancel_extra'
),
replies as (
  select job_id, bool_or(label = 'accept') as agreed, bool_or(label = 'decline') as declined
  from m where direction = 'outbound' group by 1
),
notes as (
  select job_id, bool_or(mentions_extra) as done_in_notes from {{ source('signals', 'note_labels') }} group by 1
)
select
  r.job_id, r.asked_at, r.item,
  coalesce(p.agreed, false) as agreed,
  coalesce(p.declined, false) as declined,
  coalesce(n.done_in_notes, false) as done_in_notes,
  c.job_id is not null as cancelled
from requests r
left join replies p using (job_id)
left join notes n using (job_id)
left join cancels c on c.job_id = r.job_id and c.item = r.item

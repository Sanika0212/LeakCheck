select
  entity_id as job_id,
  business_id,
  payload->>'customer_id' as customer_id,
  payload->>'quote_id' as quote_id,
  payload->>'pricing' as pricing,
  (payload->>'start_date')::date as start_date,
  (payload->>'completed_at')::timestamp as completed_at,
  payload->>'status' as status,
  payload->>'job_type' as job_type
from {{ bronze_entity('job') }}

select
  entity_id as visit_id,
  business_id,
  payload->>'job_id' as job_id,
  (payload->>'date')::date as visit_date,
  (payload->>'hours')::double as hours,
  payload->>'note' as note
from {{ bronze_entity('visit') }}

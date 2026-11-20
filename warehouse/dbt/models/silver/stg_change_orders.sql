select
  entity_id as change_order_id,
  business_id,
  payload->>'job_id' as job_id,
  (payload->>'created_at')::timestamp as created_at,
  payload->>'description' as description,
  (payload->>'amount')::double as amount,
  (payload->>'approved')::boolean as approved
from {{ bronze_entity('change_order') }}

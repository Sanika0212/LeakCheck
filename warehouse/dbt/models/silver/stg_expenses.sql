select
  entity_id as expense_id,
  business_id,
  payload->>'job_id' as job_id,
  (payload->>'date')::date as expense_date,
  payload->>'vendor' as vendor,
  (payload->>'amount')::double as amount,
  payload->>'description' as description
from {{ bronze_entity('expense') }}

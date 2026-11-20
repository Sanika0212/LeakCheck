select
  entity_id as invoice_id,
  business_id,
  payload->>'job_id' as job_id,
  payload->>'kind' as kind,
  (payload->>'created_at')::timestamp as created_at,
  (payload->>'total')::double as total,
  payload->'lines' as lines
from {{ bronze_entity('invoice') }}

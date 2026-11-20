select
  entity_id as message_id,
  business_id,
  payload->>'job_id' as job_id,
  {{ mask_pii("payload->>'customer_id'") }} as customer_key,
  (payload->>'sent_at')::timestamp as sent_at,
  payload->>'direction' as direction,
  payload->>'body' as body
from {{ bronze_entity('message') }}

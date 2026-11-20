-- PII masked before it is stored: names, emails, phones and addresses become salted hashes.
select
  entity_id as customer_id,
  business_id,
  {{ mask_pii("payload->>'name'") }} as name_hash,
  {{ mask_pii("payload->>'email'") }} as email_hash,
  {{ mask_pii("payload->>'phone'") }} as phone_hash,
  {{ mask_pii("payload->>'address'") }} as address_hash,
  regexp_extract(payload->>'address', ',\s*([^,]+),\s*([A-Z]{2})$', 1) as city
from {{ bronze_entity('customer') }}

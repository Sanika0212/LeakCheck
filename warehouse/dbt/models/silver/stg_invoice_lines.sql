select
  i.invoice_id,
  i.business_id,
  i.job_id,
  i.kind as invoice_kind,
  line->>'kind' as line_kind,
  line->>'description' as description,
  (line->>'hours')::double as hours,
  (line->>'amount')::double as amount
from {{ ref('stg_invoices') }} i,
  unnest(from_json(i.lines, '["json"]')) as t(line)

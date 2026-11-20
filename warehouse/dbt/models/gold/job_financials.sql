-- Everything known about each job's money: what was quoted, worked, bought and invoiced.
-- No-charge visits (callbacks, warranty fixes, as read from crew notes) and shop-stock
-- purchases are not billable, so they are left out of what should have been billed.
with visits as (
  select v.job_id,
         sum(v.hours) filter (where not coalesce(n.no_charge, false)) as logged_hours,
         sum(v.hours) filter (where coalesce(n.no_charge, false)) as no_charge_hours,
         count(*) as visits
  from {{ ref('stg_visits') }} v
  left join {{ source('signals', 'note_labels') }} n on n.visit_id = v.visit_id
  group by 1
),
expenses as (
  select job_id, sum(amount) filter (where description not ilike '%shop stock%') as materials_cost
  from {{ ref('stg_expenses') }} group by 1
),
credits as (
  select job_id, sum(amount) as promised_credits
  from {{ source('signals', 'message_labels') }}
  where label = 'concession' and amount is not null group by 1
),
lines as (
  select
    job_id,
    sum(amount) filter (where invoice_kind <> 'deposit' or line_kind = 'deposit') as line_total,
    sum(hours) filter (where line_kind = 'labor') as invoiced_hours,
    sum(amount) filter (where line_kind = 'materials') as invoiced_materials,
    sum(amount) filter (where line_kind = 'fixed_price') as invoiced_fixed,
    sum(amount) filter (where line_kind = 'discount') as explicit_discounts,
    sum(amount) filter (where line_kind = 'change_order') as invoiced_change_orders
  from {{ ref('stg_invoice_lines') }} group by 1
),
invoices as (
  select job_id, sum(total) as invoiced_total, bool_or(kind = 'final') as has_final_invoice,
         min(created_at) filter (where kind = 'final') as final_invoiced_at
  from {{ ref('stg_invoices') }} group by 1
),
change_orders as (
  select job_id, sum(amount) filter (where approved) as approved_change_orders, count(*) as change_orders
  from {{ ref('stg_change_orders') }} group by 1
)
select
  j.job_id, j.business_id, j.pricing, j.job_type, j.start_date, j.completed_at,
  q.total as quote_total, q.est_hours, q.est_materials, q.rate, q.markup,
  coalesce(v.logged_hours, 0) as logged_hours,
  coalesce(v.no_charge_hours, 0) as no_charge_hours,
  coalesce(cr.promised_credits, 0) as promised_credits,
  coalesce(e.materials_cost, 0) as materials_cost,
  coalesce(i.invoiced_total, 0) as invoiced_total,
  coalesce(i.has_final_invoice, false) as has_final_invoice,
  i.final_invoiced_at,
  coalesce(l.invoiced_hours, 0) as invoiced_hours,
  coalesce(l.invoiced_materials, 0) as invoiced_materials,
  l.invoiced_fixed,
  coalesce(l.explicit_discounts, 0) as explicit_discounts,
  coalesce(l.invoiced_change_orders, 0) as invoiced_change_orders,
  coalesce(c.approved_change_orders, 0) as approved_change_orders,
  coalesce(c.change_orders, 0) as change_orders,
  case when j.pricing = 'fixed' then q.total + coalesce(c.approved_change_orders, 0)
       else coalesce(v.logged_hours, 0) * q.rate + coalesce(e.materials_cost, 0) * (1 + q.markup)
  end as expected_value
from {{ ref('stg_jobs') }} j
join {{ ref('stg_quotes') }} q on q.quote_id = j.quote_id
left join visits v on v.job_id = j.job_id
left join expenses e on e.job_id = j.job_id
left join invoices i on i.job_id = j.job_id
left join lines l on l.job_id = j.job_id
left join change_orders c on c.job_id = j.job_id
left join credits cr on cr.job_id = j.job_id

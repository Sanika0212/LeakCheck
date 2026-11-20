-- Revenue the business earned but did not bill, one row per job and kind of leak.
{% set as_of = "date '" ~ var('as_of') ~ "'" %}
with f as (select * from {{ ref('job_financials') }}),
book as (select * from {{ ref('price_book') }}),
detected as (
  -- 1. Completed and never invoiced, well past the usual billing lag.
  select job_id, business_id, 'uninvoiced_job' as finding_type, expected_value as amount,
         'completed ' || strftime(completed_at, '%Y-%m-%d') || ', no invoice' as evidence
  from f
  where not has_final_invoice
    and completed_at < {{ as_of }} - interval {{ var('invoice_grace_days') }} day

  union all
  -- 2. Time and materials: hours worked but not on the invoice.
  select job_id, business_id, 'unbilled_hours', round((logged_hours - invoiced_hours) * rate, 2),
         round(logged_hours, 1) || ' h logged, ' || round(invoiced_hours, 1) || ' h invoiced'
  from f
  where pricing = 'time_materials' and has_final_invoice and logged_hours - invoiced_hours > 0.25

  union all
  -- 3. Time and materials: materials bought but not billed.
  select job_id, business_id, 'unbilled_materials',
         round(materials_cost * (1 + markup) - invoiced_materials, 2),
         '$' || round(materials_cost, 2) || ' bought, $' || round(invoiced_materials, 2) || ' billed'
  from f
  where pricing = 'time_materials' and has_final_invoice
    and materials_cost * (1 + markup) - invoiced_materials > 1

  union all
  -- 4. Fixed price: extra work asked for and done, with no change order.
  select f.job_id, f.business_id, 'unbilled_extra_work',
         round(coalesce(b.standard_hours, 4) * f.rate + coalesce(b.standard_materials, 30) * (1 + f.markup), 2),
         'asked for the ' || coalesce(x.item, 'extra work') ||
           case when x.done_in_notes then ', crew notes say done' else ', contractor agreed' end ||
           ', no change order'
  from f join {{ ref('job_extra_work') }} x using (job_id)
  left join book b on b.item = x.item
  where f.pricing = 'fixed' and f.has_final_invoice and f.change_orders = 0
    and (x.agreed or x.done_in_notes) and not x.declined and not x.cancelled

  union all
  -- 5. Fixed price billed under the approved quote, beyond any discount line or credit the
  --    owner promised in a message.
  select job_id, business_id, 'unapproved_discount',
         round(quote_total - invoiced_fixed - promised_credits, 2),
         'quoted $' || round(quote_total, 2) || ', invoiced $' || round(invoiced_fixed, 2) ||
           case when promised_credits > 0 then ', $' || round(promised_credits) || ' credit promised' else '' end ||
           ', no discount line'
  from f
  where pricing = 'fixed' and has_final_invoice
    and quote_total - invoiced_fixed - promised_credits > 1
)
select
  md5(job_id || finding_type) as finding_id,
  business_id, job_id, finding_type, amount, evidence,
  {{ as_of }} as as_of,
  'open' as status
from detected
where amount > 0

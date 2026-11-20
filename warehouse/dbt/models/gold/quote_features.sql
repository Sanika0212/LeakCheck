-- What is known when a job is quoted, and (for training) the billable hours it really took.
-- Jobs where the customer asked for extra work are marked, because their hours include it.
select
  q.job_id,
  q.business_id,
  q.created_at as quoted_at,
  q.job_type,
  q.condition,
  coalesce(q.stories, 1) as stories,
  coalesce(q.rooms, 0) as rooms,
  coalesce(q.doors, 0) as doors,
  q.area_sqft,
  month(q.created_at) as month,
  q.est_hours,
  q.rate,
  f.logged_hours as actual_hours,
  x.job_id is not null as had_extra_request
from {{ ref('stg_quotes') }} q
join {{ ref('job_financials') }} f on f.job_id = q.job_id
left join {{ ref('job_extra_work') }} x on x.job_id = q.job_id

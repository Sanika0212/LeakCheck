{#
  Latest version of each entity from the Iceberg bronze table, duplicates dropped.
  Kafka delivery is at least once, so the same event can arrive twice.
#}
{% macro bronze_entity(entity) -%}
  (
    select * exclude (rn) from (
      select *, row_number() over (partition by entity_id order by occurred_at desc, received_at desc) as rn
      from (
        select distinct on (event_id) *
        from iceberg_scan('{{ var("bronze_metadata") }}')
        where entity = '{{ entity }}'
      )
    ) where rn = 1
  )
{%- endmacro %}

{#
  Customer identifiers never reach silver in the clear. Locally they are replaced by a
  salted hash (still joinable); in Snowflake the same columns carry a masking policy.
#}
{% macro mask_pii(column) -%}
  substr(sha256(concat('{{ var("pii_salt") }}', coalesce({{ column }}, ''))), 1, 16)
{%- endmacro %}

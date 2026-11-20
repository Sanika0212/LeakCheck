output "database" {
  value = snowflake_database.lc.name
}

output "warehouse" {
  value = snowflake_warehouse.transform.name
}

output "masking_policy" {
  value = snowflake_masking_policy.pii.fully_qualified_name
}

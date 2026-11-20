# Snowflake side of LeakCheck: silver and gold, the same dbt models as locally.
#
# The dbt project runs unchanged against this database (dbt-snowflake target). Customer
# identifiers are protected by a masking policy instead of the salted hash used on DuckDB:
# only the transform role sees them; analysts see a masked value.

resource "snowflake_database" "lc" {
  name    = "LEAKCHECK"
  comment = "Revenue leakage findings for field-service contractors"
}

resource "snowflake_schema" "layers" {
  for_each = toset(["SILVER", "GOLD", "SIGNALS"])
  database = snowflake_database.lc.name
  name     = each.key
}

resource "snowflake_warehouse" "transform" {
  name                = "LEAKCHECK_WH"
  warehouse_size      = var.warehouse_size
  auto_suspend        = var.auto_suspend_seconds
  auto_resume         = true
  initially_suspended = true
}

resource "snowflake_account_role" "transform" {
  name    = "LEAKCHECK_TRANSFORM"
  comment = "dbt and the extraction job"
}

resource "snowflake_account_role" "analyst" {
  name    = "LEAKCHECK_ANALYST"
  comment = "Reads gold; sees customer identifiers masked"
}

resource "snowflake_grant_privileges_to_account_role" "transform_db" {
  account_role_name = snowflake_account_role.transform.name
  privileges        = ["USAGE", "CREATE SCHEMA"]
  on_account_object {
    object_type = "DATABASE"
    object_name = snowflake_database.lc.name
  }
}

resource "snowflake_grant_privileges_to_account_role" "transform_wh" {
  account_role_name = snowflake_account_role.transform.name
  privileges        = ["USAGE", "OPERATE"]
  on_account_object {
    object_type = "WAREHOUSE"
    object_name = snowflake_warehouse.transform.name
  }
}

resource "snowflake_grant_privileges_to_account_role" "analyst_wh" {
  account_role_name = snowflake_account_role.analyst.name
  privileges        = ["USAGE"]
  on_account_object {
    object_type = "WAREHOUSE"
    object_name = snowflake_warehouse.transform.name
  }
}

resource "snowflake_grant_privileges_to_account_role" "analyst_gold" {
  account_role_name = snowflake_account_role.analyst.name
  privileges        = ["SELECT"]
  on_schema_object {
    future {
      object_type_plural = "TABLES"
      in_schema          = "\"${snowflake_database.lc.name}\".\"${snowflake_schema.layers["GOLD"].name}\""
    }
  }
}

resource "snowflake_masking_policy" "pii" {
  name     = "PII_MASK"
  database = snowflake_database.lc.name
  schema   = snowflake_schema.layers["SILVER"].name
  argument {
    name = "VAL"
    type = "VARCHAR"
  }
  body             = "CASE WHEN CURRENT_ROLE() = 'LEAKCHECK_TRANSFORM' THEN VAL ELSE '***MASKED***' END"
  return_data_type = "VARCHAR"
  comment          = "Applied to customer name, email, phone and address columns by dbt post-hooks"
}

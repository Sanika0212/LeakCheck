terraform {
  required_version = ">= 1.6"
  required_providers {
    snowflake = { source = "snowflakedb/snowflake", version = ">= 2.0" }
  }
}

# Credentials come from the environment (SNOWFLAKE_ORGANIZATION_NAME, SNOWFLAKE_ACCOUNT_NAME,
# SNOWFLAKE_USER, SNOWFLAKE_PRIVATE_KEY), never from this repository.
provider "snowflake" {}

terraform {
  required_version = ">= 1.6"
  required_providers {
    confluent = { source = "confluentinc/confluent", version = ">= 2.10" }
  }
}

# CONFLUENT_CLOUD_API_KEY and CONFLUENT_CLOUD_API_SECRET come from the environment.
provider "confluent" {}

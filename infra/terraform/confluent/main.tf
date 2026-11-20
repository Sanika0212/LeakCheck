# Event bus between the AWS intake and its consumers: one topic per entity.
#
# Two service accounts with the least access each needs: the webhook can only write, the
# bronze consumer can only read (and commit its group's offsets).

resource "confluent_environment" "lc" {
  display_name = "leakcheck"
}

resource "confluent_kafka_cluster" "lc" {
  display_name = "leakcheck"
  availability = "SINGLE_ZONE"
  cloud        = "AWS"
  region       = var.region
  basic {}
  environment { id = confluent_environment.lc.id }
}

resource "confluent_service_account" "admin" {
  display_name = "leakcheck-admin"
  description  = "Creates topics and ACLs"
}

resource "confluent_role_binding" "admin" {
  principal   = "User:${confluent_service_account.admin.id}"
  role_name   = "CloudClusterAdmin"
  crn_pattern = confluent_kafka_cluster.lc.rbac_crn
}

resource "confluent_api_key" "admin" {
  display_name = "leakcheck-admin"
  owner {
    id          = confluent_service_account.admin.id
    api_version = confluent_service_account.admin.api_version
    kind        = confluent_service_account.admin.kind
  }
  managed_resource {
    id          = confluent_kafka_cluster.lc.id
    api_version = confluent_kafka_cluster.lc.api_version
    kind        = confluent_kafka_cluster.lc.kind
    environment { id = confluent_environment.lc.id }
  }
  depends_on = [confluent_role_binding.admin]
}

resource "confluent_kafka_topic" "entity" {
  for_each         = toset(var.topics)
  topic_name       = each.key
  partitions_count = 3
  rest_endpoint    = confluent_kafka_cluster.lc.rest_endpoint
  config           = { "retention.ms" = "604800000" } # replayable for 7 days
  kafka_cluster { id = confluent_kafka_cluster.lc.id }
  credentials {
    key    = confluent_api_key.admin.id
    secret = confluent_api_key.admin.secret
  }
}

resource "confluent_service_account" "app" {
  for_each     = toset(["webhook", "bronze"])
  display_name = "leakcheck-${each.key}"
}

locals {
  acls = {
    webhook_write = { account = "webhook", type = "TOPIC", name = "lc.", operation = "WRITE" }
    bronze_read   = { account = "bronze", type = "TOPIC", name = "lc.", operation = "READ" }
    bronze_group  = { account = "bronze", type = "GROUP", name = "leakcheck-bronze", operation = "READ" }
  }
}

resource "confluent_kafka_acl" "app" {
  for_each      = local.acls
  resource_type = each.value.type
  resource_name = each.value.name
  pattern_type  = "PREFIXED"
  principal     = "User:${confluent_service_account.app[each.value.account].id}"
  host          = "*"
  operation     = each.value.operation
  permission    = "ALLOW"
  rest_endpoint = confluent_kafka_cluster.lc.rest_endpoint
  kafka_cluster { id = confluent_kafka_cluster.lc.id }
  credentials {
    key    = confluent_api_key.admin.id
    secret = confluent_api_key.admin.secret
  }
}

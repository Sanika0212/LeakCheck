output "bootstrap" {
  value = confluent_kafka_cluster.lc.bootstrap_endpoint
}

output "topics" {
  value = [for t in confluent_kafka_topic.entity : t.topic_name]
}

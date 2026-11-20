output "webhook_url" {
  value = "${aws_apigatewayv2_api.webhooks.api_endpoint}/webhooks/{account}"
}

output "lake_bucket" {
  value = aws_s3_bucket.lake.bucket
}

output "glue_database" {
  value = aws_glue_catalog_database.bronze.name
}

output "athena_workgroup" {
  value = aws_athena_workgroup.lake.name
}

# AWS side of LeakCheck: webhook intake, the Kafka-to-Iceberg consumer, the bronze lake.
#
#   Field-service webhooks -> API Gateway (HTTP API) -> Lambda "webhook" -> Kafka (Confluent)
#   Kafka -> Lambda "bronze" (self-managed Kafka event source) -> Iceberg on S3, Glue catalog
#   Athena queries bronze; Snowflake reads the same Iceberg tables through its catalog link.

data "aws_caller_identity" "me" {}

locals {
  name = "leakcheck"
}

# --------------------------------------------------------------------- storage

resource "aws_kms_key" "lake" {
  description             = "LeakCheck lake and secrets"
  enable_key_rotation     = true
  deletion_window_in_days = 7
}

resource "aws_s3_bucket" "lake" {
  bucket_prefix = "${local.name}-lake-"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "lake" {
  bucket                  = aws_s3_bucket.lake.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "lake" {
  bucket = aws_s3_bucket.lake.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "lake" {
  bucket = aws_s3_bucket.lake.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.lake.arn
    }
  }
}

resource "aws_glue_catalog_database" "bronze" {
  name         = "${local.name}_bronze"
  location_uri = "s3://${aws_s3_bucket.lake.bucket}/bronze/"
}

resource "aws_athena_workgroup" "lake" {
  name          = local.name
  force_destroy = true
  configuration {
    enforce_workgroup_configuration = true
    result_configuration {
      output_location = "s3://${aws_s3_bucket.lake.bucket}/athena-results/"
      encryption_configuration {
        encryption_option = "SSE_KMS"
        kms_key_arn       = aws_kms_key.lake.arn
      }
    }
  }
}

# --------------------------------------------------------------------- secrets

resource "aws_secretsmanager_secret" "webhooks" {
  name_prefix = "${local.name}/webhook-secrets-"
  kms_key_id  = aws_kms_key.lake.arn
}

resource "aws_secretsmanager_secret" "kafka" {
  name_prefix = "${local.name}/kafka-credentials-"
  kms_key_id  = aws_kms_key.lake.arn
}

# --------------------------------------------------------------------- lambdas

data "aws_iam_policy_document" "assume_lambda" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "webhook" {
  name_prefix        = "${local.name}-webhook-"
  assume_role_policy = data.aws_iam_policy_document.assume_lambda.json
}

resource "aws_iam_role" "bronze" {
  name_prefix        = "${local.name}-bronze-"
  assume_role_policy = data.aws_iam_policy_document.assume_lambda.json
}

# Least privilege: the webhook reads its two secrets and writes logs; nothing else.
data "aws_iam_policy_document" "webhook" {
  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.webhooks.arn, aws_secretsmanager_secret.kafka.arn]
  }
  statement {
    actions   = ["kms:Decrypt"]
    resources = [aws_kms_key.lake.arn]
  }
  statement {
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.webhook.arn}:*"]
  }
}

# The consumer reads Kafka credentials and writes bronze tables: S3 under bronze/ and the
# Glue database, nothing else.
data "aws_iam_policy_document" "bronze" {
  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.kafka.arn]
  }
  statement {
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.lake.arn}/bronze/*"]
  }
  statement {
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.lake.arn]
  }
  statement {
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [aws_kms_key.lake.arn]
  }
  statement {
    actions = ["glue:GetDatabase", "glue:GetTable", "glue:CreateTable", "glue:UpdateTable"]
    resources = [
      "arn:aws:glue:${var.region}:${data.aws_caller_identity.me.account_id}:catalog",
      aws_glue_catalog_database.bronze.arn,
      "arn:aws:glue:${var.region}:${data.aws_caller_identity.me.account_id}:table/${aws_glue_catalog_database.bronze.name}/*",
    ]
  }
  statement {
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.bronze.arn}:*"]
  }
}

resource "aws_iam_role_policy" "webhook" {
  role   = aws_iam_role.webhook.id
  policy = data.aws_iam_policy_document.webhook.json
}

resource "aws_iam_role_policy" "bronze" {
  role   = aws_iam_role.bronze.id
  policy = data.aws_iam_policy_document.bronze.json
}

resource "aws_cloudwatch_log_group" "webhook" {
  name              = "/aws/lambda/${local.name}-webhook"
  retention_in_days = 14
  kms_key_id        = aws_kms_key.lake.arn
}

resource "aws_cloudwatch_log_group" "bronze" {
  name              = "/aws/lambda/${local.name}-bronze"
  retention_in_days = 14
  kms_key_id        = aws_kms_key.lake.arn
}

resource "aws_lambda_function" "webhook" {
  function_name    = "${local.name}-webhook"
  role             = aws_iam_role.webhook.arn
  runtime          = "python3.12"
  architectures    = ["arm64"]
  handler          = "leakcheck.ingest.lambda_handler"
  filename         = var.lambda_package
  source_code_hash = filebase64sha256(var.lambda_package)
  timeout          = 10
  memory_size      = 256
  environment {
    variables = {
      LC_KAFKA                 = var.kafka_bootstrap
      LC_WEBHOOK_SECRETS_ARN   = aws_secretsmanager_secret.webhooks.arn
      LC_KAFKA_CREDENTIALS_ARN = aws_secretsmanager_secret.kafka.arn
    }
  }
  depends_on = [aws_cloudwatch_log_group.webhook]
}

resource "aws_lambda_function" "bronze" {
  function_name    = "${local.name}-bronze"
  role             = aws_iam_role.bronze.arn
  runtime          = "python3.12"
  architectures    = ["arm64"]
  handler          = "leakcheck.bronze.lambda_handler"
  filename         = var.lambda_package
  source_code_hash = filebase64sha256(var.lambda_package)
  timeout          = 120
  memory_size      = 1024
  environment {
    variables = {
      LC_LAKE_BUCKET = aws_s3_bucket.lake.bucket
      LC_GLUE_DB     = aws_glue_catalog_database.bronze.name
    }
  }
  depends_on = [aws_cloudwatch_log_group.bronze]
}

resource "aws_lambda_event_source_mapping" "bronze" {
  function_name     = aws_lambda_function.bronze.arn
  topics            = var.kafka_topics
  starting_position = "TRIM_HORIZON"
  batch_size        = 500
  self_managed_event_source {
    endpoints = { KAFKA_BOOTSTRAP_SERVERS = var.kafka_bootstrap }
  }
  source_access_configuration {
    type = "BASIC_AUTH"
    uri  = aws_secretsmanager_secret.kafka.arn
  }
}

# --------------------------------------------------------------------- API

resource "aws_apigatewayv2_api" "webhooks" {
  name          = "${local.name}-webhooks"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_integration" "webhook" {
  api_id                 = aws_apigatewayv2_api.webhooks.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.webhook.invoke_arn
  payload_format_version = "1.0"
}

resource "aws_apigatewayv2_route" "webhook" {
  api_id    = aws_apigatewayv2_api.webhooks.id
  route_key = "POST /webhooks/{account}"
  target    = "integrations/${aws_apigatewayv2_integration.webhook.id}"
}

resource "aws_apigatewayv2_stage" "live" {
  api_id      = aws_apigatewayv2_api.webhooks.id
  name        = "$default"
  auto_deploy = true
  default_route_settings {
    throttling_burst_limit = 50
    throttling_rate_limit  = 20
  }
}

resource "aws_lambda_permission" "api" {
  statement_id  = "AllowApiGateway"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.webhook.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.webhooks.execution_arn}/*/*"
}

# --------------------------------------------------------------------- guard rails

resource "aws_cloudwatch_metric_alarm" "webhook_errors" {
  alarm_name          = "${local.name}-webhook-errors"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.webhook.function_name }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 5
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
}

resource "aws_budgets_budget" "monthly" {
  name         = "${local.name}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"
  cost_filter {
    name   = "TagKeyValue"
    values = ["user:project$leakcheck"]
  }
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.budget_email]
  }
}

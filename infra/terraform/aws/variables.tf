variable "region" {
  type    = string
  default = "us-east-1"
}

variable "kafka_bootstrap" {
  description = "Confluent Cloud bootstrap endpoint (output of the confluent stack)"
  type        = string
}

variable "kafka_topics" {
  type    = list(string)
  default = ["lc.quote", "lc.job", "lc.visit", "lc.expense", "lc.message", "lc.change_order", "lc.invoice", "lc.customer"]
}

variable "lambda_package" {
  description = "Zip of the leakcheck package and its dependencies (built by CI)"
  type        = string
  default     = "../../../dist/lambda.zip"
}

variable "budget_email" {
  type = string
}

variable "monthly_budget_usd" {
  type    = number
  default = 10
}

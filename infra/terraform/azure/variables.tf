variable "subscription_id" {
  type = string
}

variable "location" {
  type    = string
  default = "eastus"
}

variable "openai_model" {
  type    = string
  default = "gpt-4o-mini"
}

variable "openai_model_version" {
  type    = string
  default = "2024-07-18"
}

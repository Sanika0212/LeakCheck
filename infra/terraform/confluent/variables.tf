variable "region" {
  type    = string
  default = "us-east-1"
}

variable "topics" {
  type    = list(string)
  default = ["lc.quote", "lc.job", "lc.visit", "lc.expense", "lc.message", "lc.change_order", "lc.invoice", "lc.customer"]
}

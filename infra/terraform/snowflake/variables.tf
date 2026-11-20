variable "warehouse_size" {
  type    = string
  default = "XSMALL"
}

variable "auto_suspend_seconds" {
  description = "Suspend after this many idle seconds so the trial credits last"
  type        = number
  default     = 60
}

variable "endpoint" {
  description = "Where the AWS APIs are. This points at the local MiniStack emulator."
  type        = string
  default     = "http://127.0.0.1:14566"
}

variable "region" {
  description = "AWS region."
  type        = string
  default     = "us-east-1"
}

variable "bucket_name" {
  description = "S3 bucket for models. Names must be unique across all of AWS."
  type        = string
  default     = "trader-churn-models"
}

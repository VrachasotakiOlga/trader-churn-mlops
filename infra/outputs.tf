output "bucket_name" {
  description = "Where models are stored."
  value       = aws_s3_bucket.models.bucket
}

output "promote_function_name" {
  description = "The Lambda that promotes models."
  value       = aws_lambda_function.promote.function_name
}

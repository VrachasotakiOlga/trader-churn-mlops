data "archive_file" "promote" {
  type        = "zip"
  source_file = "${path.module}/lambda/promote.py"
  output_path = "${path.module}/.build/promote.zip"
}

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "promote" {
  name               = "${local.common_lambda_name}-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

# Least privilege: it reads candidates and writes only the production pointer.
data "aws_iam_policy_document" "promote" {
  statement {
    sid       = "ReadCandidatesAndPointer"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.models.arn}/*"]
  }

  # S3 reports a missing file as "access denied" without this permission.
  statement {
    sid       = "ListBucket"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.models.arn]
  }

  statement {
    sid       = "WriteOnlyThePointer"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.models.arn}/production/current.json"]
  }

  statement {
    sid       = "UseTheBucketKey"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [aws_kms_key.models.arn]
  }

  statement {
    sid       = "WriteLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.promote.arn}:*"]
  }
}

resource "aws_iam_role_policy" "promote" {
  name   = "promote-model"
  role   = aws_iam_role.promote.id
  policy = data.aws_iam_policy_document.promote.json
}

resource "aws_cloudwatch_log_group" "promote" {
  name              = "/aws/lambda/${local.common_lambda_name}"
  retention_in_days = 14
}

resource "aws_lambda_function" "promote" {
  function_name    = local.common_lambda_name
  description      = "Promotes a model that passed the quality gate"
  role             = aws_iam_role.promote.arn
  runtime          = "python3.12"
  handler          = "promote.handler"
  filename         = data.archive_file.promote.output_path
  source_code_hash = data.archive_file.promote.output_base64sha256
  timeout          = 30
  memory_size      = 256

  depends_on = [aws_cloudwatch_log_group.promote]
}

resource "aws_lambda_permission" "from_s3" {
  statement_id  = "AllowModelBucket"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.promote.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.models.arn
}

# metrics.json is uploaded last, so this runs once a candidate is complete.
resource "aws_s3_bucket_notification" "promote" {
  bucket = aws_s3_bucket.models.id

  lambda_function {
    lambda_function_arn = aws_lambda_function.promote.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "candidates/"
    filter_suffix       = "metrics.json"
  }

  depends_on = [aws_lambda_permission.from_s3]
}

# Tell someone if promotion starts failing.
resource "aws_sns_topic" "alerts" {
  name = "trader-churn-alerts"
}

resource "aws_cloudwatch_metric_alarm" "promote_errors" {
  alarm_name          = "${local.common_lambda_name}-errors"
  alarm_description   = "The Lambda that promotes models is failing"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]

  dimensions = {
    FunctionName = aws_lambda_function.promote.function_name
  }
}

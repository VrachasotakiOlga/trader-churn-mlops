# The key that encrypts everything in the bucket.
resource "aws_kms_key" "models" {
  description             = "Encrypts the trader-churn model bucket"
  enable_key_rotation     = true
  deletion_window_in_days = 7
}

resource "aws_s3_bucket" "models" {
  bucket = var.bucket_name
}

# Models are sensitive, and loading a tampered one can run code. Nothing here is public.
resource "aws_s3_bucket_public_access_block" "models" {
  bucket                  = aws_s3_bucket.models.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Keeps old versions of every file, including the production pointer.
resource "aws_s3_bucket_versioning" "models" {
  bucket = aws_s3_bucket.models.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "models" {
  bucket = aws_s3_bucket.models.id

  rule {
    bucket_key_enabled = true

    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.models.arn
    }
  }
}

# Refuse any request that isn't encrypted in transit.
data "aws_iam_policy_document" "require_tls" {
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.models.arn,
      "${aws_s3_bucket.models.arn}/*",
    ]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "models" {
  bucket     = aws_s3_bucket.models.id
  policy     = data.aws_iam_policy_document.require_tls.json
  depends_on = [aws_s3_bucket_public_access_block.models]
}

# Old candidates cost money and are rarely needed.
resource "aws_s3_bucket_lifecycle_configuration" "models" {
  bucket = aws_s3_bucket.models.id

  rule {
    id     = "expire-old-candidates"
    status = "Enabled"

    filter {
      prefix = "candidates/"
    }

    expiration {
      days = 90
    }

    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }

  depends_on = [aws_s3_bucket_versioning.models]
}

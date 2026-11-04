# What each workload may do. On EKS, you'd attach these to the pods' service accounts
# using IRSA. MiniStack doesn't enforce permissions, so running here doesn't prove them.

data "aws_iam_policy_document" "api" {
  statement {
    sid       = "ReadModels"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.models.arn}/*"]
  }

  statement {
    sid       = "ListBucket"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.models.arn]
  }

  statement {
    sid       = "DecryptModels"
    actions   = ["kms:Decrypt"]
    resources = [aws_kms_key.models.arn]
  }
}

resource "aws_iam_policy" "api" {
  name   = "trader-churn-api"
  policy = data.aws_iam_policy_document.api.json
}

data "aws_iam_policy_document" "training" {
  source_policy_documents = [data.aws_iam_policy_document.api.json]

  # Training can add candidates, but it can't change the production pointer.
  statement {
    sid       = "WriteCandidates"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.models.arn}/candidates/*"]
  }

  statement {
    sid       = "EncryptModels"
    actions   = ["kms:GenerateDataKey"]
    resources = [aws_kms_key.models.arn]
  }
}

resource "aws_iam_policy" "training" {
  name   = "trader-churn-training"
  policy = data.aws_iam_policy_document.training.json
}

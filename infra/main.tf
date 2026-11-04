terraform {
  required_version = ">= 1.9"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.7"
    }
  }
}

# Dummy keys and local endpoints: everything here goes to the MiniStack emulator, and
# nothing can reach or bill a real AWS account. To use real AWS, you'd remove the keys,
# the skip settings and the endpoints block.
provider "aws" {
  region                      = var.region
  access_key                  = "test"
  secret_key                  = "test"
  skip_credentials_validation = true
  skip_metadata_api_check     = true
  skip_requesting_account_id  = true
  s3_use_path_style           = true

  endpoints {
    cloudwatch = var.endpoint
    iam        = var.endpoint
    kms        = var.endpoint
    lambda     = var.endpoint
    logs       = var.endpoint
    s3         = var.endpoint
    sns        = var.endpoint
    sts        = var.endpoint
  }

  default_tags {
    tags = {
      Project   = "trader-churn-mlops"
      ManagedBy = "terraform"
    }
  }
}

locals {
  common_lambda_name = "trader-churn-promote-model"
}

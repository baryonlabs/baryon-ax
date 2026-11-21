# One-time, per-AWS-account setup shared by every Baryon AX environment:
#   - S3 bucket + DynamoDB table for Terraform state
#   - ECR repositories for the application images
#   - a GitHub Actions OIDC role that can push images and roll ECS services
#     (no long-lived AWS keys in GitHub)
#
#   cd deploy/aws/terraform/bootstrap
#   terraform init && terraform apply
#
# This stack keeps its own state locally; commit nothing from .terraform/ or *.tfstate.

terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.90"
    }
  }
}

variable "region" {
  type    = string
  default = "ap-northeast-2"
}

variable "github_repository" {
  description = "owner/name of the repository allowed to deploy."
  type        = string
  default     = "baryonlabs/baryon-ax"
}

provider "aws" {
  region = var.region
  default_tags {
    tags = { project = "baryon-ax", managed-by = "terraform", stack = "bootstrap" }
  }
}

data "aws_caller_identity" "current" {}

locals {
  account_id   = data.aws_caller_identity.current.account_id
  repositories = ["api", "web", "llm-gateway", "keycloak", "kordoc", "docling"]
}

# --- Terraform state ---------------------------------------------------------------

resource "aws_s3_bucket" "state" {
  bucket = "baryon-ax-tfstate-${local.account_id}-${var.region}"
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_policy" "state_tls" {
  bucket = aws_s3_bucket.state.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource  = [aws_s3_bucket.state.arn, "${aws_s3_bucket.state.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
}

resource "aws_dynamodb_table" "lock" {
  name         = "baryon-ax-tflock"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"

  attribute {
    name = "LockID"
    type = "S"
  }
}

# --- Images ---------------------------------------------------------------------------

resource "aws_ecr_repository" "app" {
  for_each             = toset(local.repositories)
  name                 = "baryon-ax/${each.key}"
  image_tag_mutability = "MUTABLE" # environments follow a moving tag per release; pin by digest for customers

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }
}

resource "aws_ecr_lifecycle_policy" "app" {
  for_each   = aws_ecr_repository.app
  repository = each.value.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the 30 most recent images"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 30 }
      action       = { type = "expire" }
    }]
  })
}

# --- GitHub Actions deploy role ------------------------------------------------------------

resource "aws_iam_openid_connect_provider" "github" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]
}

resource "aws_iam_role" "deploy" {
  name = "baryon-ax-github-deploy"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = aws_iam_openid_connect_provider.github.arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = { "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com" }
        # Only workflow runs that target a GitHub environment named aws-<env> may deploy.
        StringLike = { "token.actions.githubusercontent.com:sub" = "repo:${var.github_repository}:environment:aws-*" }
      }
    }]
  })
}

resource "aws_iam_role_policy" "deploy" {
  name = "push-images-and-roll-services"
  role = aws_iam_role.deploy.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # ecr:GetAuthorizationToken has no resource-level permissions; AWS requires "*".
      { Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*" },
      {
        Effect = "Allow"
        Action = ["ecr:BatchCheckLayerAvailability", "ecr:BatchGetImage", "ecr:CompleteLayerUpload", "ecr:InitiateLayerUpload",
        "ecr:PutImage", "ecr:UploadLayerPart", "ecr:DescribeImages"]
        Resource = [for repo in aws_ecr_repository.app : repo.arn]
      },
      {
        Effect   = "Allow"
        Action   = ["ecs:UpdateService", "ecs:DescribeServices"]
        Resource = "arn:aws:ecs:${var.region}:${local.account_id}:service/baryon-ax-*/*"
      },
    ]
  })
}

output "image_registry" {
  description = "Pass as image_registry to each environment."
  value       = "${local.account_id}.dkr.ecr.${var.region}.amazonaws.com/baryon-ax"
}

output "state_bucket" {
  value = aws_s3_bucket.state.bucket
}

output "lock_table" {
  value = aws_dynamodb_table.lock.name
}

output "deploy_role_arn" {
  description = "Set as the AWS_DEPLOY_ROLE_ARN variable of the GitHub environment aws-<env>."
  value       = aws_iam_role.deploy.arn
}

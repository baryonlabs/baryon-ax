# Dedicated ("silo") stack for one customer. Copy this directory to
# envs/<customer>/, set the values below, and use a separate state key.
# Ideally deploy it into an AWS account dedicated to the customer.
#
#   cp -r envs/silo-template envs/acme && cd envs/acme
#   terraform init -backend-config=backend.hcl     # key = "envs/acme/terraform.tfstate"
#   terraform apply                                 # then the same two-step HTTPS flow as envs/demo

terraform {
  required_version = ">= 1.5.0"
  backend "s3" {}
}

variable "customer" {
  description = "Customer id: lowercase, used for resource names and as the organization id."
  type        = string
}
variable "domain_name" {
  description = "Hostname for this customer, e.g. acme.baryon-ax.baryon.ai or a customer-owned domain."
  type        = string
}
variable "image_registry" { type = string }
variable "image_tag" {
  description = "Pin a release tag (e.g. v0.7.0) for customers; never latest."
  type        = string
}
variable "alert_email" { type = string }
variable "certificate_validated" {
  type    = bool
  default = false
}
variable "allowed_cidrs" {
  description = "Restrict access to the customer's office/VPN ranges when possible."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}
variable "admin_cidrs" {
  type    = list(string)
  default = []
}
variable "admin_users" {
  description = "Initial customer administrators (Keycloak usernames)."
  type        = list(string)
  default     = ["admin"]
}

provider "aws" {
  region = "ap-northeast-2"
  default_tags {
    tags = {
      project       = "baryon-ax"
      baryon-ax-env = var.customer
      customer      = var.customer
      tenancy       = "silo"
      managed-by    = "terraform"
    }
  }
}

module "baryon_ax" {
  source = "../../modules/baryon-ax"

  name                  = var.customer
  domain_name           = var.domain_name
  tenancy_mode          = "single"
  default_organization  = var.customer
  image_registry        = var.image_registry
  image_tag             = var.image_tag
  certificate_validated = var.certificate_validated
  allowed_cidrs         = var.allowed_cidrs
  admin_cidrs           = var.admin_cidrs

  # Customer economics: full parsing, Multi-AZ database, private subnets, always on.
  private_networking  = true
  enable_docling      = true
  multi_az_database   = true
  business_hours_only = false
  deletion_protection = true
  monthly_budget_usd  = 800
  alert_email         = var.alert_email

  demo_users      = { for user in var.admin_users : user => { organization = var.customer, groups = ["baryon-admin"] } }
  platform_admins = []
}

output "url" { value = module.baryon_ax.url }
output "cloudflare_dns_records" { value = module.baryon_ax.cloudflare_dns_records }
output "oidc_issuer" { value = module.baryon_ax.oidc_issuer }
output "secrets" { value = module.baryon_ax.secrets }
output "cluster" { value = module.baryon_ax.cluster }

# Public demo and shared (pooled) multi-tenant instance.
#
#   cd deploy/aws/terraform/envs/demo
#   cp terraform.tfvars.example terraform.tfvars   # fill in image_registry and alert_email
#   terraform init -backend-config=backend.hcl     # see backend.hcl.example
#   terraform apply                                 # 1st apply: everything except HTTPS
#   (add the ACM validation CNAME in Cloudflare, wait for "Issued")
#   terraform apply -var certificate_validated=true # 2nd apply: HTTPS listener and routes
#   (add the hostname CNAME in Cloudflare)

terraform {
  required_version = ">= 1.5.0"
  backend "s3" {}
}

variable "image_registry" { type = string }
variable "image_tag" {
  type    = string
  default = "latest"
}
variable "alert_email" {
  type    = string
  default = ""
}
variable "certificate_validated" {
  type    = bool
  default = false
}
variable "admin_cidrs" {
  type    = list(string)
  default = []
}

provider "aws" {
  region = "ap-northeast-2"
  default_tags {
    tags = {
      project       = "baryon-ax"
      baryon-ax-env = "demo"
      tenancy       = "pooled"
      managed-by    = "terraform"
    }
  }
}

module "baryon_ax" {
  source = "../../modules/baryon-ax"

  name                  = "demo"
  domain_name           = "demo.baryon-ax.baryon.ai"
  tenancy_mode          = "pooled"
  default_organization  = "demo"
  image_registry        = var.image_registry
  image_tag             = var.image_tag
  certificate_validated = var.certificate_validated
  admin_cidrs           = var.admin_cidrs

  # Demo economics: no docling, single-AZ database, off outside business hours.
  enable_docling      = false
  multi_az_database   = false
  business_hours_only = true
  deletion_protection = true
  monthly_budget_usd  = 250
  alert_email         = var.alert_email

  demo_users = {
    "demo-admin"  = { organization = "demo", groups = ["baryon-admin"] }
    "demo-member" = { organization = "demo", groups = ["baryon-member"] }
  }
  platform_admins = ["platform-admin"]
}

output "url" { value = module.baryon_ax.url }
output "cloudflare_dns_records" { value = module.baryon_ax.cloudflare_dns_records }
output "oidc_issuer" { value = module.baryon_ax.oidc_issuer }
output "secrets" { value = module.baryon_ax.secrets }
output "cluster" { value = module.baryon_ax.cluster }

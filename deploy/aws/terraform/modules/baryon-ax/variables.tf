# One instance of this module is one Baryon AX environment: the shared ("pooled")
# demo/SaaS instance, or one dedicated ("silo") customer stack. Only the values
# that differ between those two shapes are variables; everything else is a
# sensible default in the resources.

variable "name" {
  description = "Environment name, used as a prefix for every resource (e.g. \"demo\", \"acme\")."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,20}$", var.name))
    error_message = "name must be 2-21 lowercase letters, digits or hyphens and start with a letter."
  }
}

variable "domain_name" {
  description = "Public hostname for this environment, e.g. demo.baryon-ax.baryon.ai. DNS stays in Cloudflare; Terraform outputs the records to create."
  type        = string
}

variable "tenancy_mode" {
  description = "\"pooled\" for the shared multi-tenant instance (tenants come from the tenant registry), \"single\" for a silo stack that serves one organization."
  type        = string
  default     = "pooled"

  validation {
    condition     = contains(["pooled", "single"], var.tenancy_mode)
    error_message = "tenancy_mode must be \"pooled\" or \"single\"."
  }
}

variable "default_organization" {
  description = "Organization id used when a token carries no organization claim (silo stacks set this to the customer's id)."
  type        = string
  default     = "demo"
}

variable "image_tag" {
  description = "Tag of the application images in ECR (built by deploy/aws/scripts/build_and_push.sh or the deploy workflow)."
  type        = string
  default     = "latest"
}

variable "bedrock_chat_model" {
  description = "Bedrock model or inference-profile id used for answers. Verify availability in the region's Bedrock console before deploying."
  type        = string
  default     = "apac.amazon.nova-lite-v1:0"
}

variable "bedrock_embedding_model" {
  description = "Bedrock embedding model id. Changing it requires a new Qdrant collection (the API re-indexes on start)."
  type        = string
  default     = "amazon.titan-embed-text-v2:0"
}

variable "embedding_dimension" {
  description = "Vector size produced by bedrock_embedding_model."
  type        = number
  default     = 1024
}

variable "enable_docling" {
  description = "Run the docling parser (PDF/Office/image layout). It needs 2 vCPU / 8 GB; without it those formats fall back to Tika text extraction."
  type        = bool
  default     = false
}

variable "multi_az_database" {
  description = "Run PostgreSQL as Multi-AZ. Off for the demo, on for customer stacks with an SLA."
  type        = bool
  default     = false
}

variable "deletion_protection" {
  description = "Protect the database, load balancer and file system from accidental deletion."
  type        = bool
  default     = true
}

variable "business_hours_only" {
  description = "Scale every service to zero outside 08:00-21:00 KST on weekdays. Halves compute cost for a demo; never use for customers."
  type        = bool
  default     = false
}

variable "monthly_budget_usd" {
  description = "AWS Budgets alert threshold for this environment (actual + forecast)."
  type        = number
  default     = 300
}

variable "alert_email" {
  description = "Email that receives budget alerts. Empty disables the budget."
  type        = string
  default     = ""
}

variable "allowed_cidrs" {
  description = "CIDR blocks allowed to reach the load balancer. Default is the whole internet; customer stacks can restrict to office/VPN ranges."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "demo_users" {
  description = "Users created in Keycloak on first bootstrap: username => { organization, groups }. Passwords are generated and stored in Secrets Manager."
  type = map(object({
    organization = string
    groups       = list(string)
  }))
  default = {
    "demo-admin"  = { organization = "demo", groups = ["baryon-admin"] }
    "demo-member" = { organization = "demo", groups = ["baryon-member"] }
  }
}

variable "platform_admins" {
  description = "Users created with the platform-admin role (manage tenants in pooled mode). Keep this list short."
  type        = list(string)
  default     = ["platform-admin"]
}

variable "vpc_cidr" {
  description = "VPC address range."
  type        = string
  default     = "10.40.0.0/16"
}

variable "log_retention_days" {
  description = "CloudWatch log retention."
  type        = number
  default     = 30
}


variable "certificate_validated" {
  description = "Set to true after the ACM validation CNAME exists in Cloudflare (second apply). Until then no HTTPS listener is created."
  type        = bool
  default     = false
}

variable "admin_cidrs" {
  description = "CIDR blocks allowed to open the Keycloak admin console (/auth/admin). Empty keeps it closed to the internet."
  type        = list(string)
  default     = []
}

variable "private_networking" {
  description = "Run tasks in private subnets behind a NAT gateway (no public IPs). Off for the demo (saves ~$45/month), on for customer stacks."
  type        = bool
  default     = false
}

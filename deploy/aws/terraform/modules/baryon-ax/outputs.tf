resource "aws_budgets_budget" "this" {
  count        = var.alert_email == "" ? 0 : 1
  name         = "${local.prefix}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  cost_filter {
    name   = "TagKeyValue"
    values = [format("user:baryon-ax-env$%s", var.name)] # tag key "baryon-ax-env" = name (activate it as a cost allocation tag)
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.alert_email]
  }
}

output "url" {
  description = "Public URL of this environment."
  value       = local.public_url
}

output "cloudflare_dns_records" {
  description = "Records to create in Cloudflare (DNS only, not proxied): the ACM validation CNAME first, then the hostname CNAME to the load balancer."
  value = concat(
    [for o in aws_acm_certificate.this.domain_validation_options : {
      type = o.resource_record_type, name = trimsuffix(o.resource_record_name, "."), value = trimsuffix(o.resource_record_value, "."), purpose = "ACM certificate validation"
    }],
    [{ type = "CNAME", name = var.domain_name, value = aws_lb.this.dns_name, purpose = "Application (create after the certificate is issued)" }],
  )
}

output "oidc_issuer" {
  description = "Issuer for OIDC clients (bots, MCP gateways) and the web build (VITE_OIDC_ISSUER)."
  value       = local.issuer
}

output "secrets" {
  description = "Secrets Manager entries: runtime credentials and the initial Keycloak user passwords."
  value = {
    runtime           = aws_secretsmanager_secret.runtime.name
    initial_passwords = aws_secretsmanager_secret.users.name
  }
}

output "cluster" {
  description = "ECS cluster name (aws ecs execute-command --cluster ...)."
  value       = aws_ecs_cluster.this.name
}

output "services" {
  description = "ECS services in this environment."
  value       = keys(aws_ecs_service.this)
}

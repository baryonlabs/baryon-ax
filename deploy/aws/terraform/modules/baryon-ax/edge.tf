# One shared Application Load Balancer with path routing:
#   /auth/*                    -> Keycloak
#   /v1/*, /mcp, /mcp/*, /health -> API (bots, MCP and OpenAI-compatible clients)
#   /auth/admin/*              -> 404 unless the caller is in admin_cidrs
#   everything else            -> web (nginx serves the SPA and proxies /api/* to the API)
# DNS stays in Cloudflare: Terraform outputs the CNAMEs for the certificate
# validation and for the hostname itself.

resource "aws_acm_certificate" "this" {
  domain_name       = var.domain_name
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_lb" "this" {
  name                       = substr("${local.prefix}-alb", 0, 32)
  load_balancer_type         = "application"
  security_groups            = [aws_security_group.alb.id]
  subnets                    = aws_subnet.public[*].id
  drop_invalid_header_fields = true
  enable_deletion_protection = var.deletion_protection
  idle_timeout               = 300 # streaming answers and long uploads
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.this.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "redirect"
    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}

# Created only after the certificate is validated (see envs/*/README for the two-step apply).
resource "aws_acm_certificate_validation" "this" {
  count           = var.certificate_validated ? 1 : 0
  certificate_arn = aws_acm_certificate.this.arn
}

resource "aws_lb_listener" "https" {
  count             = var.certificate_validated ? 1 : 0
  load_balancer_arn = aws_lb.this.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = aws_acm_certificate_validation.this[0].certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.web.arn
  }
}

resource "aws_lb_target_group" "web" {
  name                 = substr("${local.prefix}-web", 0, 32)
  port                 = 8080
  protocol             = "HTTP"
  target_type          = "ip"
  vpc_id               = aws_vpc.this.id
  deregistration_delay = 15

  health_check {
    path    = "/"
    matcher = "200"
  }
}

resource "aws_lb_target_group" "api" {
  name                 = substr("${local.prefix}-api", 0, 32)
  port                 = 8000
  protocol             = "HTTP"
  target_type          = "ip"
  vpc_id               = aws_vpc.this.id
  deregistration_delay = 30

  health_check {
    path    = "/health"
    matcher = "200"
  }
}

resource "aws_lb_target_group" "keycloak" {
  name                 = substr("${local.prefix}-kc", 0, 32)
  port                 = 8080
  protocol             = "HTTP"
  target_type          = "ip"
  vpc_id               = aws_vpc.this.id
  deregistration_delay = 15

  health_check {
    # Keycloak serves health on the management port 9000.
    port                = "9000"
    path                = "/auth/health/ready"
    matcher             = "200"
    interval            = 30
    healthy_threshold   = 2
    unhealthy_threshold = 5
  }
}

resource "aws_lb_listener_rule" "keycloak" {
  count        = var.certificate_validated ? 1 : 0
  listener_arn = aws_lb_listener.https[0].arn
  priority     = 10

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.keycloak.arn
  }

  condition {
    path_pattern {
      values = ["/auth", "/auth/*"]
    }
  }
}

# The Keycloak admin console is never public: requests from admin_cidrs reach it
# (priority 4), everyone else gets a 404 (priority 5). With no admin_cidrs, use
# ECS Exec or temporarily set admin_cidrs to your IP.
resource "aws_lb_listener_rule" "keycloak_admin_allow" {
  count        = var.certificate_validated && length(var.admin_cidrs) > 0 ? 1 : 0
  listener_arn = aws_lb_listener.https[0].arn
  priority     = 4

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.keycloak.arn
  }

  condition {
    path_pattern {
      values = ["/auth/admin", "/auth/admin/*"]
    }
  }

  condition {
    source_ip {
      values = var.admin_cidrs
    }
  }
}

resource "aws_lb_listener_rule" "keycloak_admin_block" {
  count        = var.certificate_validated ? 1 : 0
  listener_arn = aws_lb_listener.https[0].arn
  priority     = 5

  action {
    type = "fixed-response"
    fixed_response {
      content_type = "text/plain"
      message_body = "Not found"
      status_code  = "404"
    }
  }

  condition {
    path_pattern {
      values = ["/auth/admin", "/auth/admin/*"]
    }
  }
}

resource "aws_lb_listener_rule" "api" {
  count        = var.certificate_validated ? 1 : 0
  listener_arn = aws_lb_listener.https[0].arn
  priority     = 20

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }

  condition {
    path_pattern {
      values = ["/v1/*", "/mcp", "/mcp/*", "/health"]
    }
  }
}

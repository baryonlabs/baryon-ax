# ECS Fargate services. Tasks find each other through ECS Service Connect, so the
# container images keep their Compose host names (api, keycloak, kordoc, tika,
# docling, llm-gateway) and the web image's nginx proxy to http://api:8000 works
# unchanged.
#
#   web          nginx + SPA, proxies /api/* to the API            0.25 vCPU / 0.5 GB
#   api          FastAPI + Qdrant sidecar, EFS for /data           1 vCPU / 3 GB
#   llm-gateway  OpenAI-compatible gateway to Bedrock (task role)  0.25 vCPU / 0.5 GB
#   keycloak     OIDC + first-boot bootstrap sidecar               1 vCPU / 2 GB
#   kordoc       HWP/HWPX parser                                   0.5 vCPU / 2 GB
#   tika         legacy formats (and PDF/Office without docling)   0.5 vCPU / 2 GB
#   docling      PDF/Office/image layout (optional)                2 vCPU / 8 GB
#
# The API keeps its JSON/JSONL stores on EFS and therefore runs as exactly one
# task; deployments stop the old task before starting the new one so two writers
# never overlap. Qdrant is a derived index: it lives on the task's ephemeral disk
# and the API rebuilds it from the document store on start.

variable "image_registry" {
  description = "ECR registry prefix for the application images, e.g. 123456789012.dkr.ecr.ap-northeast-2.amazonaws.com/baryon-ax (output of the bootstrap stack)."
  type        = string
}

locals {
  region = data.aws_region.current.name

  images = {
    api = "${var.image_registry}/api:${var.image_tag}"
    # The web build embeds the OIDC issuer, so each environment has its own web image.
    web      = "${var.image_registry}/web:${var.image_tag}-${var.name}"
    gateway  = "${var.image_registry}/llm-gateway:${var.image_tag}"
    keycloak = "${var.image_registry}/keycloak:${var.image_tag}"
    kordoc   = "${var.image_registry}/kordoc:${var.image_tag}"
    docling  = "${var.image_registry}/docling:${var.image_tag}"
    tika     = "apache/tika:3.3.3.1"
    qdrant   = "qdrant/qdrant:v1.12.5"
  }

  issuer = "${local.public_url}/auth/realms/baryon-ax"

  user_entries = concat(
    [for user, cfg in var.demo_users : { username = user, organization = cfg.organization, groups = cfg.groups }],
    # Platform admins belong to the "platform" organization, not to any tenant.
    [for user in var.platform_admins : { username = user, organization = "platform", groups = ["baryon-platform-admin"] }],
  )

  runtime_secret = aws_secretsmanager_secret.runtime.arn

  log_config = { for svc in ["web", "api", "qdrant", "llm-gateway", "keycloak", "keycloak-bootstrap", "kordoc", "tika", "docling"] : svc => {
    logDriver = "awslogs"
    options = {
      "awslogs-group"         = aws_cloudwatch_log_group.service[svc].name
      "awslogs-region"        = local.region
      "awslogs-stream-prefix" = svc
    }
  } }
}

resource "aws_cloudwatch_log_group" "service" {
  for_each          = toset(["web", "api", "qdrant", "llm-gateway", "keycloak", "keycloak-bootstrap", "kordoc", "tika", "docling"])
  name              = "/baryon-ax/${var.name}/${each.key}"
  retention_in_days = var.log_retention_days
}

resource "aws_ecs_cluster" "this" {
  name = local.prefix

  setting {
    name  = "containerInsights"
    value = "disabled" # enable for customer stacks once someone watches the dashboards
  }
}

resource "aws_service_discovery_http_namespace" "this" {
  name = "${local.prefix}.local"
}

# --- IAM ---------------------------------------------------------------------------

data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "execution" {
  name               = "${local.prefix}-ecs-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "execution_secrets" {
  name = "read-runtime-secrets"
  role = aws_iam_role.execution.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = [aws_secretsmanager_secret.runtime.arn, aws_secretsmanager_secret.users.arn]
    }]
  })
}

# Task role shared by services that need nothing but ECS Exec.
resource "aws_iam_role" "task" {
  name               = "${local.prefix}-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

data "aws_iam_policy_document" "ecs_exec" {
  statement {
    actions   = ["ssmmessages:CreateControlChannel", "ssmmessages:CreateDataChannel", "ssmmessages:OpenControlChannel", "ssmmessages:OpenDataChannel"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "task_exec" {
  name   = "ecs-exec"
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.ecs_exec.json
}

resource "aws_iam_role_policy" "task_efs" {
  name = "efs-client"
  role = aws_iam_role.task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = ["elasticfilesystem:ClientMount", "elasticfilesystem:ClientWrite"]
      Resource  = aws_efs_file_system.data.arn
      Condition = { StringEquals = { "elasticfilesystem:AccessPointArn" = aws_efs_access_point.api.arn } }
    }]
  })
}

# Only the gateway may call Bedrock, and only the two configured models.
resource "aws_iam_role" "gateway" {
  name               = "${local.prefix}-llm-gateway"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

locals {
  # Geo inference profiles ("apac.", "us.", "eu.", "global.") route to the base model in several regions.
  chat_base_model = replace(var.bedrock_chat_model, "/^(apac|us|eu|jp|au|global)\\./", "")
  chat_is_profile = local.chat_base_model != var.bedrock_chat_model
}

resource "aws_iam_role_policy" "gateway_bedrock" {
  name = "invoke-configured-models"
  role = aws_iam_role.gateway.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
      Resource = compact([
        "arn:aws:bedrock:*::foundation-model/${local.chat_base_model}",
        local.chat_is_profile ? "arn:aws:bedrock:${local.region}:${data.aws_caller_identity.current.account_id}:inference-profile/${var.bedrock_chat_model}" : "",
        "arn:aws:bedrock:${local.region}::foundation-model/${var.bedrock_embedding_model}",
      ])
    }]
  })
}

resource "aws_iam_role_policy" "gateway_exec" {
  name   = "ecs-exec"
  role   = aws_iam_role.gateway.id
  policy = data.aws_iam_policy_document.ecs_exec.json
}

# --- Task definitions -----------------------------------------------------------------

resource "aws_ecs_task_definition" "web" {
  family                   = "${local.prefix}-web"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([{
    name             = "web"
    image            = local.images.web
    essential        = true
    portMappings     = [{ name = "web", containerPort = 8080, protocol = "tcp" }]
    logConfiguration = local.log_config["web"]
  }])
}

resource "aws_ecs_task_definition" "api" {
  family                   = "${local.prefix}-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 1024
  memory                   = 3072
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  ephemeral_storage {
    size_in_gib = 30 # Qdrant index
  }

  volume {
    name = "data"
    efs_volume_configuration {
      file_system_id     = aws_efs_file_system.data.id
      transit_encryption = "ENABLED"
      authorization_config {
        access_point_id = aws_efs_access_point.api.id
        iam             = "ENABLED"
      }
    }
  }

  container_definitions = jsonencode([
    {
      name         = "qdrant"
      image        = local.images.qdrant
      essential    = true
      portMappings = [{ containerPort = 6333, protocol = "tcp" }]
      environment = [
        { name = "QDRANT__SERVICE__HOST", value = "127.0.0.1" },
        { name = "QDRANT__TELEMETRY_DISABLED", value = "true" },
      ]
      ulimits          = [{ name = "nofile", softLimit = 65536, hardLimit = 65536 }]
      logConfiguration = local.log_config["qdrant"]
    },
    {
      name         = "api"
      image        = local.images.api
      essential    = true
      dependsOn    = [{ containerName = "qdrant", condition = "START" }]
      portMappings = [{ name = "api", containerPort = 8000, protocol = "tcp" }]
      mountPoints  = [{ sourceVolume = "data", containerPath = "/data", readOnly = false }]
      environment = [for k, v in {
        DOCUMENT_STORE                = "/data/documents"
        AUDIT_LOG                     = "/data/audit/events.jsonl"
        GAP_STORE                     = "/data/gaps/gaps.jsonl"
        TACIT_STORE                   = "/data/tacit/tacit.jsonl"
        ONTOLOGY_STORE                = "/data/ontology"
        FEEDBACK_STORE                = "/data/feedback/feedback.jsonl"
        TENANT_STORE                  = "/data/tenants"
        TENANCY_MODE                  = var.tenancy_mode
        QDRANT_URL                    = "http://127.0.0.1:6333"
        QDRANT_COLLECTION             = "baryon_documents_${var.embedding_dimension}"
        QDRANT_REINDEX_ON_START       = "true"
        MODEL_BASE_URL                = "http://llm-gateway:4000/v1"
        MODEL_NAME                    = "baryon-chat"
        MODEL_PROMPT_PROFILE          = "generic"
        MODEL_GATEWAY_STATUS_URL      = "http://llm-gateway:4000/v1/gateway/status"
        MODEL_GATEWAY_STATUS_AUTH     = "bearer"
        EMBEDDING_PROVIDER            = "openai"
        EMBEDDING_BASE_URL            = "http://llm-gateway:4000/v1"
        EMBEDDING_MODEL               = "baryon-embed"
        EMBEDDING_QUERY_MODEL         = "baryon-embed-query"
        EMBEDDING_DIMENSION           = tostring(var.embedding_dimension)
        RETRIEVAL_MODE                = "hybrid"
        KORDOC_URL                    = "http://kordoc:8080"
        TIKA_URL                      = "http://tika:9998"
        DOCLING_URL                   = var.enable_docling ? "http://docling:8000" : ""
        AUTH_MODE                     = "oidc"
        OIDC_ISSUER                   = local.issuer
        OIDC_JWKS_URL                 = "http://keycloak:8080/auth/realms/baryon-ax/protocol/openid-connect/certs"
        OIDC_AUDIENCE                 = "baryon-ax-api"
        OIDC_DEFAULT_ORG              = var.default_organization
        OIDC_DELEGATION_USER_PREFIXES = "slack:,discord:,mcp:"
        ALLOWED_ORIGINS               = local.public_url
        MEM0_ENABLED                  = "false"
        LANGFUSE_ENABLED              = "false"
        SYSTEM_METRICS_ENABLED        = "false"
        RATE_LIMIT_PER_MINUTE         = "60"
      } : { name = k, value = v }]
      secrets = [
        { name = "DATABASE_URL", valueFrom = "${local.runtime_secret}:DATABASE_URL::" },
        { name = "MODEL_API_KEY", valueFrom = "${local.runtime_secret}:MODEL_API_KEY::" },
        { name = "EMBEDDING_API_KEY", valueFrom = "${local.runtime_secret}:MODEL_API_KEY::" },
      ]
      healthCheck = {
        command     = ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"]
        interval    = 15
        timeout     = 5
        retries     = 5
        startPeriod = 120
      }
      logConfiguration = local.log_config["api"]
    },
  ])
}

resource "aws_ecs_task_definition" "gateway" {
  family                   = "${local.prefix}-llm-gateway"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.gateway.arn

  container_definitions = jsonencode([{
    name         = "llm-gateway"
    image        = local.images.gateway
    essential    = true
    portMappings = [{ name = "llm-gateway", containerPort = 4000, protocol = "tcp" }]
    environment = [for k, v in {
      AWS_REGION          = local.region
      CHAT_MODEL_ID       = var.bedrock_chat_model
      EMBEDDING_MODEL_ID  = var.bedrock_embedding_model
      EMBEDDING_DIMENSION = tostring(var.embedding_dimension)
    } : { name = k, value = v }]
    secrets          = [{ name = "GATEWAY_API_KEY", valueFrom = "${local.runtime_secret}:MODEL_API_KEY::" }]
    logConfiguration = local.log_config["llm-gateway"]
  }])
}

resource "aws_ecs_task_definition" "keycloak" {
  family                   = "${local.prefix}-keycloak"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 1024
  memory                   = 2048
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([
    {
      name      = "keycloak"
      image     = local.images.keycloak
      essential = true
      portMappings = [
        { name = "keycloak", containerPort = 8080, protocol = "tcp" },
        { name = "keycloak-mgmt", containerPort = 9000, protocol = "tcp" },
      ]
      environment = [for k, v in {
        KC_DB_URL                       = "jdbc:postgresql://${aws_db_instance.this.address}:5432/${local.db_name}?sslmode=require"
        KC_DB_USERNAME                  = local.db_user
        KC_HOSTNAME                     = "${local.public_url}/auth"
        KC_HOSTNAME_BACKCHANNEL_DYNAMIC = "true"
        KC_HTTP_ENABLED                 = "true"
        KC_PROXY_HEADERS                = "xforwarded"
        KC_BOOTSTRAP_ADMIN_USERNAME     = "admin"
        KC_LOG_LEVEL                    = "info"
        JAVA_OPTS_KC_HEAP               = "-XX:MaxRAMPercentage=70"
      } : { name = k, value = v }]
      secrets = [
        { name = "KC_DB_PASSWORD", valueFrom = "${local.runtime_secret}:DB_PASSWORD::" },
        { name = "KC_BOOTSTRAP_ADMIN_PASSWORD", valueFrom = "${local.runtime_secret}:KEYCLOAK_ADMIN_PASSWORD::" },
      ]
      healthCheck = {
        # The image has no curl; bash /dev/tcp asks the management port for readiness.
        command     = ["CMD-SHELL", "exec 3<>/dev/tcp/127.0.0.1/9000 && printf 'GET /auth/health/ready HTTP/1.1\\r\\nHost: localhost\\r\\nConnection: close\\r\\n\\r\\n' >&3 && grep -q 'HTTP/1.1 200' <&3"]
        interval    = 15
        timeout     = 5
        retries     = 10
        startPeriod = 180
      }
      logConfiguration = local.log_config["keycloak"]
    },
    {
      name       = "keycloak-bootstrap"
      image      = local.images.keycloak
      essential  = false
      dependsOn  = [{ containerName = "keycloak", condition = "HEALTHY" }]
      entryPoint = ["/bin/bash"]
      command    = ["/opt/keycloak/baryon/bootstrap.sh"]
      environment = concat(
        [for k, v in {
          KC_INTERNAL_URL  = "http://127.0.0.1:8080/auth"
          PUBLIC_URL       = local.public_url
          BOT_ORGANIZATION = var.default_organization
          USERS            = join(";", [for u in local.user_entries : "${u.username}|${u.organization}|${join(",", u.groups)}"])
        } : { name = k, value = v }],
      )
      secrets = concat(
        [
          { name = "KEYCLOAK_ADMIN_PASSWORD", valueFrom = "${local.runtime_secret}:KEYCLOAK_ADMIN_PASSWORD::" },
          { name = "BOT_CLIENT_SECRET", valueFrom = "${local.runtime_secret}:BOT_CLIENT_SECRET::" },
        ],
        [for i, u in local.user_entries : { name = "USER_PASSWORD_${i}", valueFrom = "${aws_secretsmanager_secret.users.arn}:${u.username}::" }],
      )
      logConfiguration = local.log_config["keycloak-bootstrap"]
    },
  ])
}

resource "aws_ecs_task_definition" "parser" {
  for_each = {
    kordoc  = { image = local.images.kordoc, port = 8080, cpu = 512, memory = 2048 }
    tika    = { image = local.images.tika, port = 9998, cpu = 512, memory = 2048 }
    docling = { image = local.images.docling, port = 8000, cpu = 2048, memory = 8192 }
  }

  family                   = "${local.prefix}-${each.key}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = each.value.cpu
  memory                   = each.value.memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  ephemeral_storage {
    size_in_gib = each.key == "docling" ? 40 : 21
  }

  container_definitions = jsonencode([{
    name             = each.key
    image            = each.value.image
    essential        = true
    portMappings     = [{ name = each.key, containerPort = each.value.port, protocol = "tcp" }]
    logConfiguration = local.log_config[each.key]
  }])
}

# --- Services -------------------------------------------------------------------------

locals {
  services = merge(
    {
      web         = { task = aws_ecs_task_definition.web.arn, port = "web", alias = null, alb = { tg = aws_lb_target_group.web.arn, container = "web", port = 8080 }, single_writer = false }
      api         = { task = aws_ecs_task_definition.api.arn, port = "api", alias = { name = "api", port = 8000 }, alb = { tg = aws_lb_target_group.api.arn, container = "api", port = 8000 }, single_writer = true }
      llm-gateway = { task = aws_ecs_task_definition.gateway.arn, port = "llm-gateway", alias = { name = "llm-gateway", port = 4000 }, alb = null, single_writer = false }
      keycloak    = { task = aws_ecs_task_definition.keycloak.arn, port = "keycloak", alias = { name = "keycloak", port = 8080 }, alb = { tg = aws_lb_target_group.keycloak.arn, container = "keycloak", port = 8080 }, single_writer = true }
      kordoc      = { task = aws_ecs_task_definition.parser["kordoc"].arn, port = "kordoc", alias = { name = "kordoc", port = 8080 }, alb = null, single_writer = false }
      tika        = { task = aws_ecs_task_definition.parser["tika"].arn, port = "tika", alias = { name = "tika", port = 9998 }, alb = null, single_writer = false }
    },
    var.enable_docling ? {
      docling = { task = aws_ecs_task_definition.parser["docling"].arn, port = "docling", alias = { name = "docling", port = 8000 }, alb = null, single_writer = false }
    } : {},
  )
}

resource "aws_ecs_service" "this" {
  for_each = local.services

  name                   = each.key
  cluster                = aws_ecs_cluster.this.id
  task_definition        = each.value.task
  desired_count          = 1
  launch_type            = "FARGATE"
  enable_execute_command = true
  propagate_tags         = "SERVICE"

  # Single-writer services (EFS JSON stores, Keycloak bootstrap) never run two copies.
  deployment_minimum_healthy_percent = each.value.single_writer ? 0 : 100
  deployment_maximum_percent         = each.value.single_writer ? 100 : 200
  health_check_grace_period_seconds  = each.value.alb == null ? null : 240

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    # Demo: public subnets without NAT (inbound still limited to the load balancer).
    # Customer stacks: private subnets behind NAT, no public IPs.
    subnets          = var.private_networking ? aws_subnet.private[*].id : aws_subnet.public[*].id
    security_groups  = [aws_security_group.tasks.id]
    assign_public_ip = !var.private_networking
  }

  dynamic "load_balancer" {
    for_each = each.value.alb == null ? [] : [each.value.alb]
    content {
      target_group_arn = load_balancer.value.tg
      container_name   = load_balancer.value.container
      container_port   = load_balancer.value.port
    }
  }

  service_connect_configuration {
    enabled   = true
    namespace = aws_service_discovery_http_namespace.this.arn

    dynamic "service" {
      for_each = each.value.alias == null ? [] : [each.value.alias]
      content {
        port_name      = each.value.port
        discovery_name = service.value.name
        client_alias {
          dns_name = service.value.name
          port     = service.value.port
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [desired_count] # business-hours scaling owns it when enabled
  }

  depends_on = [aws_lb_listener.http, aws_efs_mount_target.data]
}

# --- Business-hours scaling (demo only) ---------------------------------------------------

resource "aws_appautoscaling_target" "service" {
  for_each           = var.business_hours_only ? local.services : {}
  service_namespace  = "ecs"
  resource_id        = "service/${aws_ecs_cluster.this.name}/${aws_ecs_service.this[each.key].name}"
  scalable_dimension = "ecs:service:DesiredCount"
  min_capacity       = 0
  max_capacity       = 1
}

resource "aws_appautoscaling_scheduled_action" "start" {
  for_each           = aws_appautoscaling_target.service
  name               = "${local.prefix}-${each.key}-start"
  service_namespace  = each.value.service_namespace
  resource_id        = each.value.resource_id
  scalable_dimension = each.value.scalable_dimension
  schedule           = "cron(0 8 ? * MON-FRI *)"
  timezone           = "Asia/Seoul"

  scalable_target_action {
    min_capacity = 1
    max_capacity = 1
  }
}

resource "aws_appautoscaling_scheduled_action" "stop" {
  for_each           = aws_appautoscaling_target.service
  name               = "${local.prefix}-${each.key}-stop"
  service_namespace  = each.value.service_namespace
  resource_id        = each.value.resource_id
  scalable_dimension = each.value.scalable_dimension
  schedule           = "cron(0 21 ? * MON-FRI *)"
  timezone           = "Asia/Seoul"

  scalable_target_action {
    min_capacity = 0
    max_capacity = 0
  }
}

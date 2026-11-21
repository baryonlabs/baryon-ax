# Stateful layer: PostgreSQL (audit events + Keycloak), EFS (document store,
# gaps, tacit knowledge, ontology packs, feedback, tenant registry) and the
# secrets the tasks read at start-up. Deletion protection is on by default.

locals {
  prefix     = "baryon-ax-${var.name}"
  public_url = "https://${var.domain_name}"
  db_name    = "baryon"
  db_user    = "baryon"
}

data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

# --- PostgreSQL ----------------------------------------------------------------

resource "aws_db_subnet_group" "this" {
  name       = "${local.prefix}-db"
  subnet_ids = aws_subnet.isolated[*].id
}

resource "aws_db_parameter_group" "this" {
  name   = "${local.prefix}-pg16"
  family = "postgres16"

  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }
}

resource "aws_db_instance" "this" {
  identifier                   = "${local.prefix}-db"
  engine                       = "postgres"
  engine_version               = "16"
  instance_class               = "db.t4g.micro"
  allocated_storage            = 20
  max_allocated_storage        = 100
  storage_type                 = "gp3"
  storage_encrypted            = true
  db_name                      = local.db_name
  username                     = local.db_user
  password                     = random_password.db.result
  db_subnet_group_name         = aws_db_subnet_group.this.name
  parameter_group_name         = aws_db_parameter_group.this.name
  vpc_security_group_ids       = [aws_security_group.data.id]
  multi_az                     = var.multi_az_database
  publicly_accessible          = false
  backup_retention_period      = 7
  copy_tags_to_snapshot        = true
  deletion_protection          = var.deletion_protection
  skip_final_snapshot          = !var.deletion_protection
  final_snapshot_identifier    = var.deletion_protection ? "${local.prefix}-db-final" : null
  auto_minor_version_upgrade   = true
  performance_insights_enabled = false
}

# --- EFS ------------------------------------------------------------------------

resource "aws_efs_file_system" "data" {
  creation_token   = "${local.prefix}-data"
  encrypted        = true
  performance_mode = "generalPurpose"
  throughput_mode  = "elastic"
  tags             = { Name = "${local.prefix}-data" }

  lifecycle_policy {
    transition_to_ia = "AFTER_30_DAYS"
  }
}

resource "aws_efs_backup_policy" "data" {
  file_system_id = aws_efs_file_system.data.id

  backup_policy {
    status = "ENABLED"
  }
}

resource "aws_efs_mount_target" "data" {
  count           = 2
  file_system_id  = aws_efs_file_system.data.id
  subnet_id       = aws_subnet.isolated[count.index].id
  security_groups = [aws_security_group.data.id]
}

# The API image runs as uid/gid 10001.
resource "aws_efs_access_point" "api" {
  file_system_id = aws_efs_file_system.data.id

  posix_user {
    uid = 10001
    gid = 10001
  }

  root_directory {
    path = "/api"
    creation_info {
      owner_uid   = 10001
      owner_gid   = 10001
      permissions = "0750"
    }
  }
}

# --- Secrets ---------------------------------------------------------------------

resource "random_password" "db" {
  length  = 32
  special = false
}

resource "random_password" "keycloak_admin" {
  length  = 24
  special = false
}

resource "random_password" "gateway_master_key" {
  length  = 40
  special = false
}

resource "random_password" "bot_client_secret" {
  length  = 40
  special = false
}

resource "random_password" "user" {
  for_each = toset(concat(keys(var.demo_users), var.platform_admins))
  length   = 20
  special  = false
}

resource "aws_secretsmanager_secret" "runtime" {
  name                    = "${local.prefix}/runtime"
  description             = "Baryon AX ${var.name}: database URL, gateway key, Keycloak bootstrap credentials"
  recovery_window_in_days = var.deletion_protection ? 30 : 0
}

resource "aws_secretsmanager_secret_version" "runtime" {
  secret_id = aws_secretsmanager_secret.runtime.id
  secret_string = jsonencode({
    DATABASE_URL            = "postgresql://${local.db_user}:${random_password.db.result}@${aws_db_instance.this.address}:5432/${local.db_name}?sslmode=require"
    DB_PASSWORD             = random_password.db.result
    MODEL_API_KEY           = random_password.gateway_master_key.result
    KEYCLOAK_ADMIN_PASSWORD = random_password.keycloak_admin.result
    BOT_CLIENT_SECRET       = random_password.bot_client_secret.result
  })
}

resource "aws_secretsmanager_secret" "users" {
  name                    = "${local.prefix}/initial-user-passwords"
  description             = "Initial passwords for Keycloak users created by the bootstrap task. Users must change them at first login."
  recovery_window_in_days = var.deletion_protection ? 30 : 0
}

resource "aws_secretsmanager_secret_version" "users" {
  secret_id     = aws_secretsmanager_secret.users.id
  secret_string = jsonencode({ for user, pw in random_password.user : user => pw.result })
}

# Two public subnets for the load balancer and the Fargate tasks, two isolated
# subnets for PostgreSQL and EFS. No NAT gateway: tasks get a public IP but their
# security group only accepts traffic from the load balancer and from each other
# (startup guidance: NAT costs ~$35/month plus per-GB fees before it is needed).

data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  azs = slice(data.aws_availability_zones.available.names, 0, 2)
}

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = "${local.prefix}-vpc" }
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id
  tags   = { Name = "${local.prefix}-igw" }
}

resource "aws_subnet" "public" {
  count                   = 2
  vpc_id                  = aws_vpc.this.id
  availability_zone       = local.azs[count.index]
  cidr_block              = cidrsubnet(var.vpc_cidr, 4, count.index)
  map_public_ip_on_launch = false
  tags                    = { Name = "${local.prefix}-public-${count.index}" }
}

resource "aws_subnet" "isolated" {
  count             = 2
  vpc_id            = aws_vpc.this.id
  availability_zone = local.azs[count.index]
  cidr_block        = cidrsubnet(var.vpc_cidr, 4, count.index + 8)
  tags              = { Name = "${local.prefix}-isolated-${count.index}" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id
  tags   = { Name = "${local.prefix}-public" }

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }
}

resource "aws_route_table_association" "public" {
  count          = 2
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# Isolated subnets keep the VPC's default local-only route table: no internet path.

# --- Optional private application subnets (customer stacks) ---------------------
# With private_networking = true, tasks run here without public IPs and reach
# Bedrock, ECR and Secrets Manager through one NAT gateway.

resource "aws_subnet" "private" {
  count             = var.private_networking ? 2 : 0
  vpc_id            = aws_vpc.this.id
  availability_zone = local.azs[count.index]
  cidr_block        = cidrsubnet(var.vpc_cidr, 4, count.index + 4)
  tags              = { Name = "${local.prefix}-private-${count.index}" }
}

resource "aws_eip" "nat" {
  count  = var.private_networking ? 1 : 0
  domain = "vpc"
  tags   = { Name = "${local.prefix}-nat" }
}

resource "aws_nat_gateway" "this" {
  count         = var.private_networking ? 1 : 0
  allocation_id = aws_eip.nat[0].id
  subnet_id     = aws_subnet.public[0].id
  tags          = { Name = "${local.prefix}-nat" }
  depends_on    = [aws_internet_gateway.this]
}

resource "aws_route_table" "private" {
  count  = var.private_networking ? 1 : 0
  vpc_id = aws_vpc.this.id
  tags   = { Name = "${local.prefix}-private" }

  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.this[0].id
  }
}

resource "aws_route_table_association" "private" {
  count          = var.private_networking ? 2 : 0
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[0].id
}

resource "aws_flow_log" "vpc" {
  vpc_id               = aws_vpc.this.id
  traffic_type         = "REJECT"
  log_destination_type = "cloud-watch-logs"
  log_destination      = aws_cloudwatch_log_group.flow.arn
  iam_role_arn         = aws_iam_role.flow_logs.arn
}

resource "aws_cloudwatch_log_group" "flow" {
  name              = "/baryon-ax/${var.name}/vpc-flow-rejects"
  retention_in_days = var.log_retention_days
}

resource "aws_iam_role" "flow_logs" {
  name = "${local.prefix}-flow-logs"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "vpc-flow-logs.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "flow_logs" {
  name = "write-flow-logs"
  role = aws_iam_role.flow_logs.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"]
      Resource = "${aws_cloudwatch_log_group.flow.arn}:*"
    }]
  })
}

# --- Security groups -------------------------------------------------------

resource "aws_security_group" "alb" {
  name        = "${local.prefix}-alb"
  description = "Public HTTPS entry"
  vpc_id      = aws_vpc.this.id
}

resource "aws_vpc_security_group_ingress_rule" "alb_https" {
  for_each          = toset(var.allowed_cidrs)
  security_group_id = aws_security_group.alb.id
  cidr_ipv4         = each.value
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
  description       = "HTTPS"
}

resource "aws_vpc_security_group_ingress_rule" "alb_http" {
  for_each          = toset(var.allowed_cidrs)
  security_group_id = aws_security_group.alb.id
  cidr_ipv4         = each.value
  from_port         = 80
  to_port           = 80
  ip_protocol       = "tcp"
  description       = "HTTP, redirected to HTTPS"
}

resource "aws_vpc_security_group_egress_rule" "alb_to_tasks" {
  security_group_id            = aws_security_group.alb.id
  referenced_security_group_id = aws_security_group.tasks.id
  ip_protocol                  = "-1"
  description                  = "To Fargate tasks"
}

resource "aws_security_group" "tasks" {
  name        = "${local.prefix}-tasks"
  description = "Fargate tasks: only the load balancer and other tasks may connect"
  vpc_id      = aws_vpc.this.id
}

resource "aws_vpc_security_group_ingress_rule" "tasks_from_alb" {
  security_group_id            = aws_security_group.tasks.id
  referenced_security_group_id = aws_security_group.alb.id
  ip_protocol                  = "-1"
  description                  = "From the load balancer"
}

resource "aws_vpc_security_group_ingress_rule" "tasks_from_tasks" {
  security_group_id            = aws_security_group.tasks.id
  referenced_security_group_id = aws_security_group.tasks.id
  ip_protocol                  = "-1"
  description                  = "Service Connect between tasks"
}

resource "aws_vpc_security_group_egress_rule" "tasks_out" {
  security_group_id = aws_security_group.tasks.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
  description       = "Image pulls, Bedrock, Secrets Manager, CloudWatch"
}

resource "aws_security_group" "data" {
  name        = "${local.prefix}-data"
  description = "PostgreSQL and EFS: tasks only"
  vpc_id      = aws_vpc.this.id
}

resource "aws_vpc_security_group_ingress_rule" "data_postgres" {
  security_group_id            = aws_security_group.data.id
  referenced_security_group_id = aws_security_group.tasks.id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
  description                  = "PostgreSQL from tasks"
}

resource "aws_vpc_security_group_ingress_rule" "data_nfs" {
  security_group_id            = aws_security_group.data.id
  referenced_security_group_id = aws_security_group.tasks.id
  from_port                    = 2049
  to_port                      = 2049
  ip_protocol                  = "tcp"
  description                  = "EFS from tasks"
}

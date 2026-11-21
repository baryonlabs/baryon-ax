# Baryon AX on AWS

Terraform, images and scripts for the demo, the shared multi-tenant instance and dedicated customer stacks on AWS (Seoul, Amazon Bedrock).

The full guide (Korean) is [docs/aws-deploy.md](../../docs/aws-deploy.md).

| Path | What |
| --- | --- |
| `terraform/modules/baryon-ax` | One environment: VPC, ALB, ECS Fargate services, RDS, EFS, secrets, alarms |
| `terraform/envs/demo` | Public demo / pooled multi-tenant instance |
| `terraform/envs/silo-template` | Copy per dedicated customer stack |
| `terraform/bootstrap` | Per account: state bucket, ECR, GitHub deploy role |
| `gateway/` | OpenAI-compatible gateway to Bedrock (chat + embeddings) |
| `keycloak/` | Keycloak image, sanitized realm template, first-boot bootstrap |
| `scripts/` | `build_and_push.sh`, `device_login.sh` |

#!/usr/bin/env bash
# Build the Baryon AX images for AWS (linux/amd64) and push them to ECR.
#
#   deploy/aws/scripts/build_and_push.sh <env> <domain> [tag]
#
#   env     environment name, e.g. demo or a customer id (selects the web image)
#   domain  public hostname of that environment, e.g. demo.baryon-ax.baryon.ai
#   tag     image tag (default: short git SHA)
#
# Environment:
#   IMAGE_REGISTRY   ECR prefix from the bootstrap stack output (required)
#   AWS_REGION       default ap-northeast-2
#   SERVICES         space-separated subset to build (default: all)
#   PLATFORM         default linux/amd64
set -euo pipefail

env_name=${1:?environment name}
domain=${2:?public domain}
tag=${3:-$(git rev-parse --short HEAD)}
registry=${IMAGE_REGISTRY:?set IMAGE_REGISTRY to the bootstrap output image_registry}
region=${AWS_REGION:-ap-northeast-2}
platform=${PLATFORM:-linux/amd64}
services=${SERVICES:-"api web llm-gateway keycloak kordoc docling"}
root=$(git rev-parse --show-toplevel)

aws ecr get-login-password --region "$region" | docker login --username AWS --password-stdin "${registry%%/*}"

build() { # build <repository> <tag> <context> [extra docker args...]
  local repo=$1 image_tag=$2 context=$3; shift 3
  echo "==> $repo:$image_tag"
  docker buildx build --platform "$platform" --provenance=false --push \
    -t "$registry/$repo:$image_tag" "$@" "$context"
}

for service in $services; do
  case "$service" in
    api)         build api "$tag" "$root/apps/api" ;;
    web)
      # The SPA is compiled with the environment's login settings.
      build web "$tag-$env_name" "$root/apps/web" \
        --build-arg VITE_AUTH_MODE=oidc \
        --build-arg VITE_OIDC_ISSUER="https://$domain/auth/realms/baryon-ax" \
        --build-arg VITE_OIDC_CLIENT_ID=baryon-ax-web ;;
    llm-gateway) build llm-gateway "$tag" "$root/deploy/aws/gateway" ;;
    keycloak)    build keycloak "$tag" "$root/deploy/aws/keycloak" ;;
    kordoc)      build kordoc "$tag" "$root/deploy/parsers/kordoc" ;;
    docling)     build docling "$tag" "$root/deploy/parsers/docling" ;;
    *) echo "unknown service: $service" >&2; exit 2 ;;
  esac
done

echo "pushed tag $tag (web: $tag-$env_name) to $registry"

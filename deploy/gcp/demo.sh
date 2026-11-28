#!/usr/bin/env bash
# Baryon AX preview demo on Google Cloud: one VM, sample data only, HTTPS + one demo login (README.md).
#   GCP_PROJECT=my-project deploy/gcp/demo.sh create            # VM, static IP, firewall, first boot
#   deploy/gcp/demo.sh update [git-ref]                          # pull and rebuild on the VM
#   deploy/gcp/demo.sh model <url> [model-name]                  # point the demo at a model (key: DEMO_MODEL_KEY env)
#   deploy/gcp/demo.sh status | logs | ssh | destroy
set -euo pipefail
cd "$(dirname "$0")/../.."
PROJECT=${GCP_PROJECT:-$(gcloud config get-value project 2>/dev/null)}
REGION=${GCP_REGION:-asia-northeast3}            # Seoul
ZONE=${GCP_ZONE:-asia-northeast3-a}
NAME=${DEMO_NAME:-baryon-ax-demo}
MACHINE=${DEMO_MACHINE:-e2-standard-4}
CREDS=local/gcp-demo.env                          # demo login, kept out of git (local/ is ignored)
[ -n "$PROJECT" ] || { echo "set GCP_PROJECT"; exit 1; }
G=(--project "$PROJECT")

ip() { gcloud compute addresses describe "$NAME-ip" "${G[@]}" --region "$REGION" --format='value(address)'; }
on_vm() { gcloud compute ssh "$NAME" "${G[@]}" --zone "$ZONE" --tunnel-through-iap --command "$1"; }

case "${1:-}" in
create)
  gcloud services enable compute.googleapis.com iap.googleapis.com "${G[@]}"
  gcloud compute addresses describe "$NAME-ip" "${G[@]}" --region "$REGION" >/dev/null 2>&1 \
    || gcloud compute addresses create "$NAME-ip" "${G[@]}" --region "$REGION"
  HOST=${DEMO_HOST:-$(ip | tr . -).sslip.io}     # a real name later: point DNS at the IP and run `demo.sh host`
  mkdir -p local
  if [ ! -f "$CREDS" ]; then
    PASSWORD=$(openssl rand -base64 18 | tr -d '/+=' | cut -c1-20)
    HASH=$(docker run --rm caddy:2.8.4-alpine caddy hash-password --plaintext "$PASSWORD")
    umask 077; printf 'DEMO_URL=https://%s/\nDEMO_USER=demo\nDEMO_PASSWORD=%s\nDEMO_PASSWORD_HASH=%s\n' "$HOST" "$PASSWORD" "$HASH" > "$CREDS"
  fi
  HASH=$(grep '^DEMO_PASSWORD_HASH=' "$CREDS" | cut -d= -f2-)
  gcloud compute firewall-rules describe "$NAME-web" "${G[@]}" >/dev/null 2>&1 || gcloud compute firewall-rules create "$NAME-web" "${G[@]}" \
    --allow tcp:80,tcp:443 --target-tags "$NAME" --description "Baryon AX demo: HTTPS only"
  gcloud compute firewall-rules describe "$NAME-iap-ssh" "${G[@]}" >/dev/null 2>&1 || gcloud compute firewall-rules create "$NAME-iap-ssh" "${G[@]}" \
    --allow tcp:22 --source-ranges 35.235.240.0/20 --target-tags "$NAME" --description "SSH through IAP only"
  gcloud compute instances create "$NAME" "${G[@]}" --zone "$ZONE" --machine-type "$MACHINE" \
    --image-family ubuntu-2404-lts-amd64 --image-project ubuntu-os-cloud --boot-disk-size 60GB --boot-disk-type pd-balanced \
    --address "$(ip)" --tags "$NAME" --labels app=baryon-ax,purpose=demo \
    --metadata-from-file startup-script=deploy/gcp/vm-start.sh \
    --metadata "git-ref=${2:-main},demo-host=$HOST,demo-user=demo,demo-password-hash=$HASH,model-name=${DEMO_MODEL_NAME:-baryon-ax-qwen}"
  echo "Creating. First boot builds the images (about 10 minutes): https://$HOST/  (login in $CREDS)"
  ;;
update)
  [ -n "${2:-}" ] && gcloud compute instances add-metadata "$NAME" "${G[@]}" --zone "$ZONE" --metadata "git-ref=$2"
  gcloud compute instances add-metadata "$NAME" "${G[@]}" --zone "$ZONE" --metadata-from-file startup-script=deploy/gcp/vm-start.sh
  on_vm "sudo google_metadata_script_runner startup"
  ;;
model)
  [ -n "${2:-}" ] || { echo "usage: demo.sh model <openai-compatible base url> [model name]"; exit 1; }
  gcloud compute instances add-metadata "$NAME" "${G[@]}" --zone "$ZONE" \
    --metadata "model-url=$2,model-name=${3:-baryon-ax-qwen}${DEMO_MODEL_KEY:+,model-key=$DEMO_MODEL_KEY}"
  on_vm "sudo google_metadata_script_runner startup"
  ;;
host)
  [ -n "${2:-}" ] || { echo "usage: demo.sh host <name pointing at $(ip)>"; exit 1; }
  gcloud compute instances add-metadata "$NAME" "${G[@]}" --zone "$ZONE" --metadata "demo-host=$2"
  sed -i.bak "s|^DEMO_URL=.*|DEMO_URL=https://$2/|" "$CREDS" && rm -f "$CREDS.bak"
  on_vm "sudo google_metadata_script_runner startup"
  ;;
status) gcloud compute instances describe "$NAME" "${G[@]}" --zone "$ZONE" --format='value(status,networkInterfaces[0].accessConfigs[0].natIP)'
        grep '^DEMO_URL=' "$CREDS" 2>/dev/null || true ;;
logs)   on_vm "sudo tail -n 60 /var/log/baryon-demo.log" ;;
ssh)    gcloud compute ssh "$NAME" "${G[@]}" --zone "$ZONE" --tunnel-through-iap ;;
destroy)
  gcloud compute instances delete "$NAME" "${G[@]}" --zone "$ZONE"
  gcloud compute firewall-rules delete "$NAME-web" "$NAME-iap-ssh" "${G[@]}"
  gcloud compute addresses delete "$NAME-ip" "${G[@]}" --region "$REGION"
  ;;
*) sed -n 2,7p "$0"; exit 1 ;;
esac

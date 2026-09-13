#!/usr/bin/env bash
# Builds the SMRITI VoiceBot Docker image and deploys it to the provisioned
# EC2 instance. NOT YET EXECUTED -- see deployment/aws/README.md.
#
# Assumes:
#   - provision.sh has already run and the instance is reachable over SSH.
#   - Docker is installed on the instance (Amazon Linux 2023: `dnf install docker`).
#   - /data is formatted and mounted on the instance (first-time setup only).
#   - .env.production.local exists locally with real secrets filled in.
set -euo pipefail

ENV_FILE="${1:-.env.production.local}"
INSTANCE_IP="${2:?Usage: deploy.sh <env-file> <instance-public-ip>}"
KEY_FILE="${3:-smriti-voicebot-key.pem}"

if [ ! -f "$ENV_FILE" ]; then
  echo "Missing $ENV_FILE" >&2
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
IMAGE_TAG="smriti-voicebot:$(cd "$REPO_ROOT" && git rev-parse --short HEAD)"

echo "== Building the image locally =="
docker build -t "$IMAGE_TAG" "$REPO_ROOT"

echo "== Saving and transferring the image to the instance =="
docker save "$IMAGE_TAG" | gzip | ssh -i "$KEY_FILE" "ec2-user@$INSTANCE_IP" \
  'gunzip | sudo docker load'

echo "== Copying the environment file to the instance (not into the image) =="
scp -i "$KEY_FILE" "$ENV_FILE" "ec2-user@$INSTANCE_IP:/tmp/smriti.env"
ssh -i "$KEY_FILE" "ec2-user@$INSTANCE_IP" \
  'sudo mkdir -p /etc/smriti && sudo mv /tmp/smriti.env /etc/smriti/production.env && sudo chmod 600 /etc/smriti/production.env'

echo "== Stopping any existing container and starting the new one =="
ssh -i "$KEY_FILE" "ec2-user@$INSTANCE_IP" "
  sudo docker rm -f smriti-voicebot 2>/dev/null || true
  sudo docker run -d --name smriti-voicebot --restart unless-stopped \
    --env-file /etc/smriti/production.env \
    -v /data:/data \
    -p 127.0.0.1:8000:8000 \
    $IMAGE_TAG
"

echo "== Deployed. Confirm the reverse proxy (nginx/Caddy) is forwarding 443 -> 127.0.0.1:8000 =="
echo "== Then run: ./smoke_test.sh https://<your-domain> =="

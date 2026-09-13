#!/usr/bin/env bash
# One-time instance setup: formats and mounts the /data EBS volume, installs
# Docker, and installs Caddy as the HTTPS reverse proxy.
#
# Run this ON THE INSTANCE (via SSH), after provision.sh and before
# deploy.sh -- provision.sh only attaches the raw /data volume, it does not
# format, mount, or install anything on the instance itself. NOT YET
# EXECUTED -- see docs/AWS_DEPLOYMENT.md §15 for why.
#
# Usage (from your machine, after provision.sh prints the instance's public
# IP and key pair name):
#   scp -i smriti-voicebot-key.pem deployment/aws/first_boot_setup.sh \
#     ec2-user@<PUBLIC_IP>:/tmp/first_boot_setup.sh
#   ssh -i smriti-voicebot-key.pem ec2-user@<PUBLIC_IP> \
#     "sudo DOMAIN=your-domain.example bash /tmp/first_boot_setup.sh"
#
# DOMAIN must already point (an A record) at the instance's Elastic IP --
# Caddy's automatic HTTPS (Let's Encrypt) fails otherwise. Run this again
# with a different DOMAIN if the domain changes; Caddy re-issues safely.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root: sudo bash first_boot_setup.sh" >&2
  exit 1
fi

: "${DOMAIN:?Set DOMAIN=your-domain.example (must already point at this instance's Elastic IP)}"
DATA_DEVICE="${DATA_DEVICE:-/dev/xvdf}"

echo "== Formatting and mounting the /data EBS volume =="
if ! blkid "$DATA_DEVICE" >/dev/null 2>&1; then
  # Only format an unformatted (blank) volume. A volume that already has a
  # filesystem (e.g. a restored/re-attached data volume from a prior
  # deployment) must NEVER be reformatted -- that would destroy the
  # existing database and audio cache.
  mkfs.ext4 "$DATA_DEVICE"
  echo "Formatted $DATA_DEVICE as ext4 (was blank)."
else
  echo "$DATA_DEVICE already has a filesystem -- not reformatting (preserving existing data)."
fi

mkdir -p /data
if ! mountpoint -q /data; then
  mount "$DATA_DEVICE" /data
fi

# Persist across reboot. Idempotent: only add the fstab line if a /data
# entry isn't already there.
if ! grep -qs '^\S\+\s\+/data\s' /etc/fstab; then
  DEVICE_UUID=$(blkid -s UUID -o value "$DATA_DEVICE")
  echo "UUID=$DEVICE_UUID  /data  ext4  defaults,nofail  0  2" >> /etc/fstab
  echo "Added /data to /etc/fstab (mounts automatically on reboot)."
fi

echo "== Installing Docker (Amazon Linux 2023) =="
dnf install -y docker
systemctl enable --now docker
usermod -aG docker ec2-user || true

echo "== Installing Caddy (HTTPS reverse proxy, automatic Let's Encrypt) =="
dnf install -y 'dnf-command(copr)'
dnf copr enable -y @caddy/caddy
dnf install -y caddy

cat > /etc/caddy/Caddyfile <<CADDYFILE
$DOMAIN {
	reverse_proxy 127.0.0.1:8000
}
CADDYFILE

systemctl enable --now caddy
systemctl reload caddy || systemctl restart caddy

echo ""
echo "== First-boot setup complete =="
echo "/data is mounted and will survive reboot (see /etc/fstab)."
echo "Docker is running."
echo "Caddy is running and will obtain a Let's Encrypt certificate for $DOMAIN"
echo "automatically on first request, then terminate HTTPS and forward to"
echo "127.0.0.1:8000 (the container's port, bound to localhost only -- see deploy.sh)."
echo ""
echo "Next: run deploy.sh from your own machine to build and start the container."

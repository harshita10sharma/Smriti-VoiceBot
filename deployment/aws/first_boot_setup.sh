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
# DOMAIN must already resolve to the instance's Elastic IP -- Caddy's
# automatic HTTPS (Let's Encrypt) fails otherwise. If you don't own a domain,
# a free wildcard DNS service works with no signup or cost: for Elastic IP
# 15.206.144.216, use 15-206-144-216.nip.io -- it resolves automatically to
# the IP encoded in the name (see https://nip.io). Run this again with a
# different DOMAIN if the domain changes; Caddy re-issues safely.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root: sudo bash first_boot_setup.sh" >&2
  exit 1
fi

: "${DOMAIN:?Set DOMAIN=your-domain.example (must already point at the Elastic IP of this instance)}"
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
# The @caddy/caddy Copr project (Caddy's own documented Fedora/RHEL/CentOS
# install path) has no Amazon Linux 2023 build target -- confirmed directly
# against a real instance ("Repository 'amazonlinux-2023-x86_64' does not
# exist in project '@caddy/caddy'"), not a configuration mistake. Amazon
# Linux 2023 is not a RHEL/CentOS clone in package terms, so this is a real
# incompatibility, not something a different dnf invocation fixes. Caddy's
# own officially documented static-binary method sidesteps distro package
# repos entirely and is what's actually used here.
curl -sL 'https://caddyserver.com/api/download?os=linux&arch=amd64' -o /usr/local/bin/caddy
chmod +x /usr/local/bin/caddy

id caddy >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin caddy
mkdir -p /etc/caddy /var/lib/caddy /var/log/caddy
chown -R caddy:caddy /etc/caddy /var/lib/caddy /var/log/caddy

cat > /etc/caddy/Caddyfile <<CADDYFILE
$DOMAIN {
	reverse_proxy 127.0.0.1:8000
}
CADDYFILE
chown caddy:caddy /etc/caddy/Caddyfile

# The caddy user has no home directory (--no-create-home, deliberately, to
# minimise its footprint) -- Caddy's default XDG config/data paths need a
# writable $HOME otherwise, so these are pointed at /var/lib/caddy instead
# (already owned by caddy above). Without this, certificate storage/ACME
# account state fails to save with "permission denied" under /home/caddy,
# reproduced directly against a real instance.
cat > /etc/systemd/system/caddy.service <<'UNIT'
[Unit]
Description=Caddy
Documentation=https://caddyserver.com/docs/
After=network-online.target
Wants=network-online.target

[Service]
Type=notify
User=caddy
Group=caddy
Environment=XDG_DATA_HOME=/var/lib/caddy/.local/share
Environment=XDG_CONFIG_HOME=/var/lib/caddy/.config
WorkingDirectory=/var/lib/caddy
ExecStart=/usr/local/bin/caddy run --environ --config /etc/caddy/Caddyfile
ExecReload=/usr/local/bin/caddy reload --config /etc/caddy/Caddyfile --force
TimeoutStopSec=5s
LimitNOFILE=1048576
LimitNPROC=512
PrivateTmp=true
ProtectSystem=full
AmbientCapabilities=CAP_NET_BIND_SERVICE
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
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

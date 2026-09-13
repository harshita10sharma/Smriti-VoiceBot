#!/usr/bin/env bash
# Provisions the AWS resources for the SMRITI VoiceBot pilot: one EC2
# instance, one security group, one key pair, one EBS volume.
#
# NOT YET EXECUTED. Requires AWS CLI v2 configured with real credentials
# (`aws sts get-caller-identity` must succeed) before running this script.
# See docs/AWS_DEPLOYMENT.md for the full architecture and reasoning, and
# deployment/aws/README.md for the intended order of operations.
set -euo pipefail

# Load configuration -- see env.template for what each variable means.
ENV_FILE="${1:-.env.production.local}"
if [ ! -f "$ENV_FILE" ]; then
  echo "Missing $ENV_FILE -- copy env.template, fill in real values, and pass its path." >&2
  exit 1
fi
# shellcheck disable=SC1090
set -a; source "$ENV_FILE"; set +a

: "${AWS_REGION:?AWS_REGION must be set}"
: "${SSH_ALLOWED_CIDR:?SSH_ALLOWED_CIDR must be set -- your own IP/32, never 0.0.0.0/0}"

echo "== Confirming AWS identity =="
aws sts get-caller-identity --region "$AWS_REGION"

echo "== Creating key pair: $KEY_PAIR_NAME =="
if ! aws ec2 describe-key-pairs --key-names "$KEY_PAIR_NAME" --region "$AWS_REGION" >/dev/null 2>&1; then
  aws ec2 create-key-pair --key-name "$KEY_PAIR_NAME" --region "$AWS_REGION" \
    --query 'KeyMaterial' --output text > "${KEY_PAIR_NAME}.pem"
  chmod 400 "${KEY_PAIR_NAME}.pem"
  echo "Private key saved to ${KEY_PAIR_NAME}.pem -- keep this safe, it is not recoverable."
else
  echo "Key pair already exists, skipping creation."
fi

echo "== Locating default VPC =="
VPC_ID=$(aws ec2 describe-vpcs --filters Name=isDefault,Values=true \
  --region "$AWS_REGION" --query 'Vpcs[0].VpcId' --output text)

echo "== Creating security group: $SECURITY_GROUP_NAME =="
SG_ID=$(aws ec2 describe-security-groups --filters Name=group-name,Values="$SECURITY_GROUP_NAME" \
  --region "$AWS_REGION" --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || echo "None")
if [ "$SG_ID" = "None" ] || [ -z "$SG_ID" ]; then
  SG_ID=$(aws ec2 create-security-group --group-name "$SECURITY_GROUP_NAME" \
    --description "SMRITI VoiceBot pilot: HTTPS + restricted SSH only" \
    --vpc-id "$VPC_ID" --region "$AWS_REGION" --query 'GroupId' --output text)
  aws ec2 authorize-security-group-ingress --group-id "$SG_ID" --region "$AWS_REGION" \
    --protocol tcp --port 443 --cidr 0.0.0.0/0
  aws ec2 authorize-security-group-ingress --group-id "$SG_ID" --region "$AWS_REGION" \
    --protocol tcp --port 22 --cidr "$SSH_ALLOWED_CIDR"
  echo "Security group created: $SG_ID (443 open to the world, 22 restricted to $SSH_ALLOWED_CIDR)"
else
  echo "Security group already exists: $SG_ID"
fi

echo "== Finding latest Amazon Linux 2023 AMI =="
AMI_ID=$(aws ssm get-parameter --name /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64 \
  --region "$AWS_REGION" --query 'Parameter.Value' --output text)

echo "== Launching EC2 instance: $INSTANCE_NAME ($INSTANCE_TYPE) =="
INSTANCE_ID=$(aws ec2 run-instances --region "$AWS_REGION" \
  --image-id "$AMI_ID" --instance-type "$INSTANCE_TYPE" \
  --key-name "$KEY_PAIR_NAME" --security-group-ids "$SG_ID" \
  --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$INSTANCE_NAME}]" \
  --block-device-mappings "[{\"DeviceName\":\"/dev/xvda\",\"Ebs\":{\"VolumeSize\":20,\"VolumeType\":\"gp3\"}}]" \
  --query 'Instances[0].InstanceId' --output text)
echo "Instance launched: $INSTANCE_ID -- waiting for it to be running..."
aws ec2 wait instance-running --instance-ids "$INSTANCE_ID" --region "$AWS_REGION"

echo "== Allocating and associating an Elastic IP =="
ALLOC_ID=$(aws ec2 allocate-address --domain vpc --region "$AWS_REGION" --query 'AllocationId' --output text)
aws ec2 associate-address --instance-id "$INSTANCE_ID" --allocation-id "$ALLOC_ID" --region "$AWS_REGION"
PUBLIC_IP=$(aws ec2 describe-addresses --allocation-ids "$ALLOC_ID" --region "$AWS_REGION" \
  --query 'Addresses[0].PublicIp' --output text)

echo "== Creating the persistent /data EBS volume ($EBS_VOLUME_SIZE_GB GB) =="
AZ=$(aws ec2 describe-instances --instance-ids "$INSTANCE_ID" --region "$AWS_REGION" \
  --query 'Reservations[0].Instances[0].Placement.AvailabilityZone' --output text)
VOLUME_ID=$(aws ec2 create-volume --availability-zone "$AZ" --size "$EBS_VOLUME_SIZE_GB" \
  --volume-type gp3 --region "$AWS_REGION" \
  --tag-specifications "ResourceType=volume,Tags=[{Key=Name,Value=${INSTANCE_NAME}-data}]" \
  --query 'VolumeId' --output text)
aws ec2 wait volume-available --volume-ids "$VOLUME_ID" --region "$AWS_REGION"
aws ec2 attach-volume --volume-id "$VOLUME_ID" --instance-id "$INSTANCE_ID" \
  --device /dev/xvdf --region "$AWS_REGION"
echo "IMPORTANT: disable delete-on-termination for $VOLUME_ID before going to production --"
echo "  aws ec2 modify-instance-attribute --instance-id $INSTANCE_ID --block-device-mappings \\"
echo "    '[{\"DeviceName\":\"/dev/xvdf\",\"Ebs\":{\"DeleteOnTermination\":false}}]' --region $AWS_REGION"

cat <<SUMMARY

== Provisioning complete ==
Instance ID:    $INSTANCE_ID
Public IP:      $PUBLIC_IP
Security group: $SG_ID
Data volume:    $VOLUME_ID (attached at /dev/xvdf -- format and mount at /data on first boot)
SSH:            ssh -i ${KEY_PAIR_NAME}.pem ec2-user@$PUBLIC_IP

Next: copy first_boot_setup.sh to the instance and run it as root with
DOMAIN=<your-domain-pointed-at-$PUBLIC_IP> set -- it formats/mounts /data,
installs Docker, and installs Caddy for HTTPS. Then run deploy.sh.
SUMMARY

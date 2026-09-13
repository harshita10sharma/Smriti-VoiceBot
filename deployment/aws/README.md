# AWS deployment scripts

See [`../../docs/AWS_DEPLOYMENT.md`](../../docs/AWS_DEPLOYMENT.md) for the full plan and
reasoning. This directory is the reproducible tooling that plan describes.

**None of these scripts have been executed.** AWS credentials are not configured in the
development environment that authored them — see `docs/AWS_DEPLOYMENT.md` §15 for the exact
blocker and how to resolve it. They are written to be correct and ready to run once that's
resolved, not a record of a completed deployment.

## Files

| File | Purpose |
|---|---|
| `env.template` | Every environment variable the application needs, with placeholder values — copy to `.env.production` on the instance (never commit the filled-in version) |
| `provision.sh` | Creates the EC2 instance, security group, key pair, and EBS volume via AWS CLI |
| `deploy.sh` | Builds the Docker image, pushes to ECR (optional) or transfers directly, and starts the container on the instance |
| `smoke_test.sh` | Runs the post-deployment HTTP checks from `docs/AWS_DEPLOYMENT.md` §post-deployment against a live URL |
| `rollback.md` | Exact rollback sequence if a deployment needs to be reverted |
| `backup_restore.md` | Exact backup/restore sequence using this repository's existing `tools/backup_db.py` |

## Prerequisites (all currently unmet in this environment)

1. AWS CLI v2 installed.
2. `aws configure` run with a real IAM user's credentials (not root), scoped to at minimum
   EC2, EBS, and security-group permissions; S3 permissions only if you also want the
   backup-to-S3 step.
3. `aws sts get-caller-identity` returning a real account/user identity.
4. A chosen AWS region — `ap-south-1` (Mumbai) is used throughout these scripts as the
   default, matching the pilot's existing India-focused deployment (change
   `AWS_REGION` in `env.template` if a different region is deliberately chosen).

## Order of operations

```sh
# 1. Fill in env.template with real values, save as a local file NOT committed to git
cp env.template .env.production.local
# edit .env.production.local with real secrets

# 2. Provision AWS resources
./provision.sh

# 3. Deploy the application
./deploy.sh

# 4. Verify
./smoke_test.sh https://<the-domain-or-IP-provision.sh-printed>
```

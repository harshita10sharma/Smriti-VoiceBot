# IAM policies for the AWS deployment

Generated from a line-by-line audit of `deployment/aws/provision.sh`, `deploy.sh`,
`first_boot_setup.sh`, `smoke_test.sh`, `backup_restore.md`, and `docs/AWS_DEPLOYMENT.md`
at commit `b4c71cc`. **No AWS resources, IAM identities, or policies have been created --
these are local files only, ready to paste into the console.**

## What each script actually calls

| Script | AWS CLI calls |
|---|---|
| `provision.sh` | `sts get-caller-identity`; `ec2 describe-key-pairs`/`create-key-pair`; `ec2 describe-vpcs`; `ec2 describe-security-groups`/`create-security-group`/`authorize-security-group-ingress`; `ssm get-parameter` (AMI lookup); `ec2 run-instances`, `ec2 wait instance-running` (polls `DescribeInstances`); `ec2 allocate-address`/`associate-address`/`describe-addresses`; `ec2 create-volume`, `ec2 wait volume-available` (polls `DescribeVolumes`), `ec2 attach-volume`; prints (does not run) an `ec2 modify-instance-attribute` suggestion for disabling delete-on-termination |
| `deploy.sh` | None. Local `docker build`, then `ssh`/`scp`/`docker` commands run *on* the instance over SSH -- no AWS API calls. |
| `first_boot_setup.sh` | None. Runs entirely on the instance (`mkfs`, `mount`, `dnf install docker`, `dnf install caddy`). |
| `smoke_test.sh` | None. Plain HTTPS requests to the deployed API via `tools/smoke_test.py`. |
| `backup_restore.md` | `aws s3 cp` (upload the backup; download for restore) -- the only place `s3` appears anywhere in the deployment tooling, and it runs *on the EC2 instance*, not from a local machine. |

`rollback.md` calls no AWS API at all (SSH + Docker only, deliberately never terminates the
instance or touches the EBS volume).

## Three separate identities, by who runs what

### 1. Local deployment identity (`local-deploy-policy.json`)
Whoever runs `provision.sh` from their own machine. This is the only identity that needs
broad EC2 create/describe permissions, and only within `ap-south-1` (every mutating
statement carries an `aws:RequestedRegion` condition so this identity cannot create
anything in another region by mistake). It does **not** need any S3 permission -- backup
upload happens on the instance, not from here.

### 2. EC2 instance role (`ec2-instance-role-trust-policy.json` + `ec2-instance-role-s3-backup-policy.json`)
**Optional, and not yet wired up.** `provision.sh`'s `run-instances` call does not currently
attach an instance profile at all -- the instance has no AWS permissions of its own today.
Attach this role only if/when the S3 backup step in `backup_restore.md` is actually turned
on (a cron job running `aws s3 cp` on the instance needs *some* credential; an instance
role is the right mechanism, never a long-lived access key baked into the instance).
Scoped to exactly one bucket and one prefix (`smriti-voicebot/*`) -- replace
`REPLACE_WITH_YOUR_BACKUP_BUCKET_NAME` with the real bucket name once you create it.

### 3. S3 backup bucket permissions
Same file as (2) above -- for this pilot, the bucket read/write permission *is* the
instance-role policy, since only the instance itself uploads/downloads backups. No separate
identity needs it. If you also want to download backups to your own laptop for
safekeeping, add a **separate** statement to a *different* policy for your own IAM user
scoped the same way (`GetObject`/`ListBucket` on the same prefix) -- do not add laptop
access to the instance role.

## Exact console steps

**Local deployment identity (steps 1-3 below), done once:**

1. **IAM → Users → Create user.** Name: `smriti-voicebot-deployer`. Do NOT enable console
   access (this is a CLI-only identity) -- select "Access key - Programmatic access" only
   on the next relevant screen (in the current console: create the user first with no
   console access, then create an access key for it separately in step 3).
2. **Permissions → Attach policies directly → Create policy → JSON tab.** Paste the
   contents of `local-deploy-policy.json`. Name it `SmritiVoiceBotLocalDeploy`. Create
   policy, then attach it to the `smriti-voicebot-deployer` user.
3. **Security credentials tab (on the user) → Create access key → "Command Line Interface
   (CLI)"** → acknowledge the warning → Create. **Copy the Access Key ID and Secret Access
   Key immediately (the secret is shown once)** and store them somewhere secure (a password
   manager, not a chat message or a committed file). Run `aws configure` locally with
   these, or export them as `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/
   `AWS_DEFAULT_REGION=ap-south-1`.

**EC2 instance role (only when/if S3 backup is turned on -- skip for the initial deploy):**

4. **IAM → Roles → Create role → Trusted entity type: AWS service → Use case: EC2.**
5. **Create policy → JSON tab**, paste `ec2-instance-role-s3-backup-policy.json` (after
   replacing the bucket-name placeholder), name it `SmritiVoiceBotS3Backup`, create it, then
   attach it to the role during role creation (or afterward via Permissions → Add
   permissions). Name the role `smriti-voicebot-ec2-role`.
6. When provisioning the instance, add `--iam-instance-profile
   Name=smriti-voicebot-ec2-role` to the `run-instances` call in `provision.sh` (a small,
   deliberate script edit -- not made here, since this task's scope was policy generation
   only) or attach it after launch via **EC2 → Instances → Actions → Security → Modify IAM
   role**.

## Note on `ec2:ModifyInstanceAttribute`

An earlier draft of `local-deploy-policy.json` included `ec2:ModifyInstanceAttribute`.
`provision.sh` only *prints* a suggested command for disabling delete-on-termination on the
data volume (line ~87) -- it never actually calls that API. Least privilege means granting
only what the script executes, so this action was removed from the policy file in this
repository. If the IAM policy actually attached in the AWS console still includes it (from
before this correction), it is a harmless unused grant, not a security hole -- tighten it
via the console when convenient by editing the `SmritiVoiceBotLocalDeploy` policy's JSON to
match this file. The deployer identity intentionally has no `iam:*` permission, so nothing
in this repository's tooling can make that change on the AWS side for you.

## Nothing here creates a billable AWS resource

IAM users, policies, and roles are free. Creating them does not start any EC2 instance,
EBS volume, or Elastic IP, and does not touch your $100 credit or trigger any billing --
those only begin at `provision.sh` (Phase 13 of the earlier deployment task), which was
explicitly not run as part of this task. Before running `provision.sh` for real, consider
setting a low-threshold AWS Budgets alert (a few dollars) so you get an email well before
meaningful spend against the credit, independent of anything in this repository.

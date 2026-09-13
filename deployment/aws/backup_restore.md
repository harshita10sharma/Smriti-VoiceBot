# Backup / restore procedure

**Not yet exercised — no deployment exists yet.** Uses this repository's existing
`tools/backup_db.py`, which is already implemented and tested
(`tests/unit/test_backup_db.py`) — nothing new to build, only to wire into a schedule.

## Backup (run on the instance, or via SSH from a management machine)

```sh
ssh -i smriti-voicebot-key.pem ec2-user@<instance-ip>
sudo docker exec smriti-voicebot python tools/backup_db.py backup --out /data/backups
# writes a timestamped, consistent snapshot (smriti-backup-<UTC timestamp>.db) using
# SQLite's online-backup API -- safe against a live database, never a torn copy.
#
# --out /data/backups is REQUIRED: with no --out, the tool defaults to <repo-root>/backups,
# which inside the container is /app/backups -- the container's ephemeral writable layer,
# wiped on every restart/redeploy. /data is the only path on this instance that survives
# both a container restart and a redeploy (it is the mounted EBS volume, not baked into the
# image). The tool never names a file "latest.db" -- it always timestamps the filename, so
# a script that expects a fixed name will silently never find it.
```

## Recommended schedule

A daily cron job on the instance (outside the container, or via `docker exec` as above),
copying the newest backup file to an S3 bucket dedicated to this purpose:

```sh
# /etc/cron.d/smriti-backup (on the instance)
0 3 * * * ec2-user docker exec smriti-voicebot python tools/backup_db.py backup --out /data/backups && \
  LATEST=$(ls -t /data/backups/smriti-backup-*.db | head -1) && \
  aws s3 cp "$LATEST" "s3://<your-backup-bucket>/smriti-voicebot/$(basename "$LATEST")"
```

Requires the EC2 instance role to have `s3:PutObject` on that one bucket/prefix only — not
broad S3 access. `/data/backups` also accumulates every prior snapshot on the persistent
volume itself — add a retention step (e.g. `find /data/backups -name 'smriti-backup-*.db'
-mtime +30 -delete`) once disk usage needs bounding; not needed at pilot scale.

## Restore

```sh
# Download the backup you want to restore from S3
aws s3 cp s3://<your-backup-bucket>/smriti-voicebot/<date>.db /data/restore-source.db

# Stop the container first -- restoring against a live database is unsafe
sudo docker stop smriti-voicebot

# Restore and verify, run through the same image (the bare EC2 host has no Python
# environment with this application's dependencies installed -- only the Docker image
# does; `docker exec` cannot be used here since the container is stopped, so use a
# one-off `docker run` against the same image instead). Replace IMAGE_TAG with the tag
# `deploy.sh` used (find it with `sudo docker images smriti-voicebot --format
# '{{.Repository}}:{{.Tag}}'`).
sudo docker run --rm -v /data:/data IMAGE_TAG \
  python tools/backup_db.py restore --from /data/restore-source.db --db /data/smriti.db
# --db is explicit here (rather than relying on the SMRITI_DB_PATH env default) because
# this one-off `docker run` does not pass --env-file -- without --db, the tool would
# resolve to its own built-in default (<repo-root>/runtime/smriti.db inside the image,
# i.e. /app/runtime/smriti.db), not the real production database at /data/smriti.db.
# preserves the pre-restore file as .pre-restore, per tools/backup_db.py

sudo docker run --rm -v /data:/data IMAGE_TAG \
  python tools/backup_db.py verify /data/smriti.db

# Restart
sudo docker start smriti-voicebot
```

## Verification after restore

Confirm via the health endpoint and a real conversation turn that patient data from before
the backup is present, and that no `.pre-restore` file was silently left in place of the
live database (a restore failure should leave the original file, not a partial one — this
is what `tools/backup_db.py restore` already guarantees, verified by its own test suite).

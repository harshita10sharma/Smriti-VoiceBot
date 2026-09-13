# Backup / restore procedure

**Not yet exercised — no deployment exists yet.** Uses this repository's existing
`tools/backup_db.py`, which is already implemented and tested
(`tests/unit/test_backup_db.py`) — nothing new to build, only to wire into a schedule.

## Backup (run on the instance, or via SSH from a management machine)

```sh
ssh -i smriti-voicebot-key.pem ec2-user@<instance-ip>
sudo docker exec smriti-voicebot python tools/backup_db.py backup
# writes a timestamped, consistent snapshot using SQLite's online-backup API --
# safe against a live database, never a torn copy.
```

## Recommended schedule

A daily cron job on the instance (outside the container, or via `docker exec` as above),
copying the resulting backup file to an S3 bucket dedicated to this purpose:

```sh
# /etc/cron.d/smriti-backup (on the instance)
0 3 * * * ec2-user docker exec smriti-voicebot python tools/backup_db.py backup && \
  aws s3 cp /data/backups/latest.db s3://<your-backup-bucket>/smriti-voicebot/$(date +\%Y-\%m-\%d).db
```

Requires the EC2 instance role to have `s3:PutObject` on that one bucket/prefix only — not
broad S3 access.

## Restore

```sh
# Download the backup you want to restore from S3
aws s3 cp s3://<your-backup-bucket>/smriti-voicebot/<date>.db /data/restore-source.db

# Stop the container first -- restoring against a live database is unsafe
sudo docker stop smriti-voicebot

# Restore (preserves the pre-restore file as .pre-restore, per tools/backup_db.py)
python tools/backup_db.py restore --from /data/restore-source.db

# Verify integrity before restarting (PATH is the restored database)
python tools/backup_db.py verify /data/smriti.db

# Restart
sudo docker start smriti-voicebot
```

## Verification after restore

Confirm via the health endpoint and a real conversation turn that patient data from before
the backup is present, and that no `.pre-restore` file was silently left in place of the
live database (a restore failure should leave the original file, not a partial one — this
is what `tools/backup_db.py restore` already guarantees, verified by its own test suite).

# Rollback procedure

**Not yet exercised — no deployment exists to roll back.** This is the documented sequence
to follow when one is needed.

## If the newly deployed container is broken

The previous image tag is still present on the instance (Docker doesn't remove old images
on `docker run` unless explicitly pruned):

```sh
ssh -i smriti-voicebot-key.pem ec2-user@<instance-ip>
sudo docker images smriti-voicebot   # find the previous tag (the git short hash before this one)
sudo docker rm -f smriti-voicebot
sudo docker run -d --name smriti-voicebot --restart unless-stopped \
  --env-file /etc/smriti/production.env -v /data:/data -p 127.0.0.1:8000:8000 \
  smriti-voicebot:<previous-short-hash>
```

This touches only the running container, never the `/data` volume — the database and audio
cache are untouched by a code rollback.

## Before any rollback

1. Take a fresh backup first (`deployment/aws/backup_restore.md`), even though a code
   rollback shouldn't need it — cheap insurance.
2. Confirm the target (older) image's expected schema version is a subset of what's
   currently applied — this repository's migrations are additive/forward-only
   (`STAGING_READINESS.md`), so rolling back code to before a migration that already ran
   against the live database is the one scenario that needs care: the older code should
   still tolerate newer schema columns it doesn't use, but verify this for any migration
   added between the two versions before rolling back across it.

## If the instance itself is unhealthy

Stop the container, do not terminate the instance (that would separately require deciding
what happens to the attached `/data` volume — see `docs/AWS_DEPLOYMENT.md` §10). Diagnose
via `docker logs smriti-voicebot` first.

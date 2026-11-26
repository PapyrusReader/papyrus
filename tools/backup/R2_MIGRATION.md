# Papyrus release backups in Cloudflare R2

The release host calls `/etc/papyrus-release/backup-check` under the server deployment lock. The R2 replacement captures the running database, media, deployment files and PowerSync keys. It briefly stops the API for the final media sync and database dump, then restarts it before encryption or network transfers. Each attempt writes one independently encrypted age archive under `backups/`. Before allowing migration, the hook downloads that exact object, decrypts it, compares the whole tar's SHA-256, checks deployment files and restores the dump into a disposable PostgreSQL container with networking disabled. The restored user, book and media-asset counts and Alembic revision must match the capture.

## Prepare the destination

1. Create a **private Standard** bucket with **European Union jurisdiction**. Keep public access disabled. Use `https://<account-id>.eu.r2.cloudflarestorage.com` and S3 region `auto`. The hook derives the EU endpoint from the account ID. [R2 data location](https://developers.cloudflare.com/r2/reference/data-location/)
2. Add a **30-day bucket lock** on the prefix `backups/`. Keep bucket administration separate from the server. Initially retain completed archives indefinitely. Before enabling automatic expiry, arrange recurring verified backups or a separately protected last-known-good copy so a long gap between releases cannot expire every recovery point. Incomplete multipart uploads can use the default expiration. [Bucket locks](https://developers.cloudflare.com/r2/buckets/bucket-locks/) · [Object lifecycles](https://developers.cloudflare.com/r2/buckets/object-lifecycles/)
3. Issue an **Object Read & Write** account token scoped to this bucket for the release host. A recovery host can use a separate bucket-scoped **Object Read only** token. [R2 credentials](https://developers.cloudflare.com/r2/api/tokens/)
4. Create a dedicated age identity for the verifier and a second independent recovery identity. Generate the second on a recovery machine and escrow its private key in the team's password manager. Supply only its public recipient to the release host. Encrypt to both recipients. Retain old recovery identities while their archives exist.

## Stage on Hetzner

Run as root after selecting a reviewed workspace revision. Install Python 3.11+, Python venv support, age, rsync, tar and Docker. Copy these two reviewed files into a root-owned directory and create the hook's isolated Python environment.

```sh
umask 077
install -d -m 0700 /opt/papyrus-r2 /etc/papyrus-release/r2
install -m 0600 tools/backup/archive.py tools/backup/release_backup_r2.py /opt/papyrus-r2/
python3 -m venv /opt/papyrus-r2/venv
/opt/papyrus-r2/venv/bin/pip install 'boto3==1.44.12'
```

Place these files in `/etc/papyrus-release/r2/` with root ownership and mode 0600.

| File | Contents |
| --- | --- |
| `storage.json` | A JSON object with string fields `account_id`, `bucket`, `access_key_id` and `secret_access_key`. Use the R2 S3 credential pair. |
| `identity` | Native age key generated with `age-keygen -o /etc/papyrus-release/r2/identity` on first setup. |
| `recipients` | Two public recipients, one per line. Obtain the host recipient with `age-keygen -y /etc/papyrus-release/r2/identity`. |

Stage the root-owned launcher under a separate name. The active hook continues to run during staging.

```sh
cat > /etc/papyrus-release/backup-check.r2 <<'SH'
#!/bin/sh
exec /opt/papyrus-r2/venv/bin/python /opt/papyrus-r2/release_backup_r2.py "$@"
SH
chmod 0700 /etc/papyrus-release/backup-check.r2
```

## Verify and switch

1. Schedule a brief API interruption for the first full-size backup. Check free space for two archive copies plus the isolated database on the spool and Docker filesystems. The production host keeps both on its root filesystem. The release hook creates temporary files under `/var/lib/papyrus-release-r2/`.
2. Put the current server release request in a root-only file with the fields `version`, full server `revision`, and `image` (ghcr.io/papyrusreader/server pinned by SHA-256). Run the staged hook under the same lock as the release job. This takes and verifies a backup without running a deployment.

   ```sh
   flock -n /srv/apps/papyrus/deploy/.release-job.lock \
     /etc/papyrus-release/backup-check.r2 < /root/papyrus-backup-drill.json
   ```

3. Read the verified object key from `/var/lib/papyrus-release-r2/last-verified.json`. From a separate recovery machine with the offline identity, read-only R2 credentials, the same scripts and the archive's PostgreSQL image available locally (currently `postgres:17-alpine`), run the following. The recovery machine needs Docker and age, and the same root-private paths. The drill downloads the object again and checks the restore without contacting production.

   ```sh
   /etc/papyrus-release/backup-check.r2 --verify 'backups/<version>/<id>-<sha256>.tar.age'
   ```

4. Confirm the lock rule protects the prefix in Cloudflare. Test deletion denial with a disposable object under the same prefix using the host token. Record the verified key outside the deployment host. Keep the preceding Mac archive and its restic key available for recovery.
5. With the deployment lock held, preserve the existing hook and atomically replace it with the staged launcher on the same filesystem. Run these switch commands once.

   ```sh
   flock -n /srv/apps/papyrus/deploy/.release-job.lock sh -ec '
     cd /etc/papyrus-release
     test ! -e backup-check.before-r2
     cp -p backup-check backup-check.before-r2
     mv -T backup-check.r2 backup-check
   '
   ```

6. After the switch, unload the Mac agent with `launchctl bootout "gui/$(id -u)/com.papyrus-reader.release-backup"` and move its plist out of `~/Library/LaunchAgents/` to prevent reload on login. Preserve it for rollback. Octopus already calls the server's backup gate and needs no R2 credentials.

For rollback, restore the Mac agent plist, load it with `launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.papyrus-reader.release-backup.plist`, and under the same deployment lock atomically restore `backup-check.before-r2` to `backup-check`. Confirm the Mac is reachable before retrying the release. A failed R2 verification exits nonzero and preserves the previous verified receipt.

## Recover the backup files

After a successful drill, download the recorded object via the EU endpoint to a private recovery directory. Decrypt with `age -d -i /path/to/recovery-identity -o restored.tar backup.tar.age` and compare `sha256sum restored.tar` with the hash in the recorded key. The tar contains `database.dump`, `media/`, `deploy/` and `metadata.json`. Metadata records both the requested release and the API image running at capture. Restore into a separate recovery environment before using these files to replace any live data.

# Database Backup

Nightly backups run at 02:00 UTC and are retained for thirty days. Restore
drills happen monthly: pick a random backup, restore it to a scratch instance,
and verify row counts against the source. Point-in-time recovery is available
for the primary cluster only.

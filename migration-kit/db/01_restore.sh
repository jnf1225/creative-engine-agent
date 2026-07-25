#!/usr/bin/env bash
# ============================================================================
# Restore the Lovable "Export project data" dump into YOUR new Supabase project.
#
# The Lovable export is a zstd-compressed pg_dump that carries schema, data, RLS,
# functions, triggers, sequences, cron.job ROWS, and auth.users WITH password
# hashes + auth.identities. It does NOT carry: vault secret VALUES, storage
# files, or the pg_cron JOB definitions' live wiring (recreate those separately).
#
# REQUIREMENTS:
#   - pg_restore 16+ (zstd support). Check: pg_restore --version
#   - The dump file from Lovable (Cloud → Overview → Advanced → Export project data)
#   - Your new project's DIRECT connection string (Supabase → Settings → Database →
#     Connection string → URI; use the SESSION POOLER or direct, not transaction pooler)
#
# USAGE:
#   export TARGET_DB="postgresql://postgres.PROJ:PWD@aws-0-REGION.pooler.supabase.com:5432/postgres"
#   ./01_restore.sh /path/to/lovable-export.dump
#
# This is written to be RE-RUNNABLE against a throwaway project during the Phase-2
# rehearsal. Time it — that number is your real cutover window.
# ============================================================================
set -euo pipefail

DUMP="${1:?Usage: ./01_restore.sh <dump-file>}"
: "${TARGET_DB:?Set TARGET_DB to your new Supabase connection string}"

echo "==> pg_restore version:"; pg_restore --version

# If the export is a plain .sql (not custom-format), use psql instead of pg_restore.
case "$DUMP" in
  *.sql|*.sql.gz)
    echo "==> Detected SQL dump; restoring with psql"
    if [[ "$DUMP" == *.gz ]]; then
      gunzip -c "$DUMP" | psql "$TARGET_DB" -v ON_ERROR_STOP=0
    else
      psql "$TARGET_DB" -v ON_ERROR_STOP=0 -f "$DUMP"
    fi
    ;;
  *)
    echo "==> Detected custom/zstd dump; restoring with pg_restore"
    # --no-owner / --no-privileges: role names differ between projects; Supabase
    #   recreates ownership. RLS policies still restore.
    # --clean --if-exists: safe re-runs on the rehearsal project.
    # We do NOT use --single-transaction so that the known identities-FK ordering
    #   issue can be repaired by 02_post_restore.sql rather than aborting the whole
    #   restore. Expect some non-fatal errors; review them, don't ignore them.
    pg_restore \
      --no-owner \
      --no-privileges \
      --clean --if-exists \
      --disable-triggers \
      -d "$TARGET_DB" \
      "$DUMP" || echo "!! pg_restore reported errors — review above; 02_post_restore.sql handles the known ones."
    ;;
esac

echo "==> Restore pass complete. Next: psql \"\$TARGET_DB\" -f 02_post_restore.sql"

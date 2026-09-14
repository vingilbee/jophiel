# Background account deletion

The worker process runs two independent threads: one for memory jobs and one for account-file deletion jobs. No additional worker process, web server, or port is needed. Each thread handles one job at a time, so the two job types can progress concurrently.

## Enable the change

From `backend`, apply the migration and restart the existing worker:

```sh
uv run --no-sync alembic upgrade head
uv run --no-sync python -m app.workers
```

Restart/reload the API after applying the migration. Run the API and worker with the same database, Redis URL, and image/asset storage settings. With local storage they must run on the same host or share the same filesystem. The migration was tested on a disposable database; it has not been applied to the personal application database.

## Request and response

`DELETE /api/auth/me` still requires a valid session and trusted Origin. It now returns HTTP **202 Accepted** with:

```json
{"status": "deletion_pending"}
```

It clears the browser cookie. The transaction deletes the user and cascaded database records while inserting a `file_deletion_jobs` row. The job has no foreign key to the deleted user, so it survives. If that transaction fails, neither operation commits. Old cookies immediately fail authentication because their user no longer exists, even while physical Redis cleanup is pending. The frontend should sign out and describe cleanup as pending rather than complete. There is currently no account-deletion UI in the frontend API module.

## Processing and recovery

- `app/services/file_deletion.py` schedules jobs, removes user files and Redis sessions, and records completion or retry state.
- `app/workers/file_deletion.py` runs one due deletion job.
- `app/workers/memory.py` runs one pending memory job.
- `app/workers/runner.py`, started by `python -m app.workers`, runs each job type in its own thread with separate database sessions and Redis clients. Each thread listens to its own wake-up queue with a bounded timeout. PostgreSQL remains the source of truth if notifications are lost.
- Deletion holds a PostgreSQL row lock (`FOR UPDATE SKIP LOCKED`) through cleanup and commits its result. Another worker skips that job. If the process or connection dies, PostgreSQL rolls back the transaction and releases its lock: the pending job can be retried without a processing lease.
- Failed cleanup retries after 5 seconds, exponentially increasing to a maximum one-hour delay. Missing folders count as success. Permission errors and Redis failures retain the pending job. Retries are not capped.
- Cleanup removes the user's image and asset directories, including metadata sidecars, and scans Redis session records to remove matching user sessions. Other users' data is preserved.

`app/services/storage_cleanup.py` serializes writes and cleanup with a per-user filesystem lock. Each storage root retains tiny `.cleanup/<user-id>.lock` and `.cleanup/<user-id>.deleted` control files, containing no uploaded content. These prevent already-running uploads or generated-asset tasks from recreating a deleted user's directory. Do not remove those markers while stale work may still exist. The local lock implementation uses Unix `flock`, matching the current macOS/Linux deployment.

A deletion does not wait for an already-running memory job; both worker threads operate independently. SIGINT/SIGTERM stop new claims and allow active jobs to finish before exiting. A provider call already in progress can therefore delay process shutdown. Stopping the process does not lose deletion requests, but file cleanup waits until it resumes.

Memory jobs use the same PostgreSQL claim pattern: one worker atomically claims a pending or expired job with `FOR UPDATE SKIP LOCKED`, records a random ownership token and 15-minute lease, then commits the claim before model work. A second worker cannot claim the live lease. Completion and failure updates require the ownership token. If a process dies after claiming, the expired lease is reclaimable; if it dies before the claim commit, the row remains pending. Apply the memory-job lease migration before starting workers.

## Inspect pending work

```sql
SELECT id, user_id, status, attempts, next_attempt_at, last_error, processed_at
FROM file_deletion_jobs
ORDER BY created_at;
```

`last_error` records the exception type without file content or credentials. Completed rows are retained as job history. This change handles newly requested account deletions; it does not guess ownership or automatically purge files orphaned by historical deletions.

## Validation

`backend/tests/test_file_deletion.py` covers scoped file deletion, repeat cleanup, write fencing, session scoping, transaction atomicity, restart-safe jobs, partial-failure retries, competing claims, rollback recovery, shared-worker dispatch, and the 202 response with immediate cookie rejection.

`backend/tests/test_worker_runner.py` verifies that either queue progresses while the other is blocked, active jobs drain at shutdown, failed callbacks retry, and Redis waits are isolated by queue.

The PostgreSQL cases require `TEST_DATABASE_URL` pointing to a disposable PostgreSQL database. Each test creates and removes its own isolated schema. Without that setting, PostgreSQL cases are explicitly skipped. The tests substitute Redis and use temporary storage directories; they do not use personal uploads or model calls.

```sh
TEST_DATABASE_URL=postgresql+psycopg://test_user@127.0.0.1:55439/postgres \
  uv run --no-sync python -m pytest tests/test_file_deletion.py -q
```

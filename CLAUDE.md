# gcal-drchrono-sync — Development Notes

## Architecture
- **GitHub Actions** runs `run_all.py` on a 30-min cron + event-triggered via `repository_dispatch`
- **State files** are cached between Actions runs. Local state and Actions state are SEPARATE — never run the sync locally unless you understand this will diverge from Actions state.
- **DrChrono OAuth tokens rotate** on each refresh. The Actions caches the latest token. Running locally can invalidate the Actions token.

## Critical Rules

### Never run sync locally during debugging
Running `python run_all.py` or `python drchrono_to_gcal.py` locally creates events using the LOCAL state file, while Actions uses its CACHED state. This causes duplicates every time. If you must test, use `--full` and then clear ALL Actions caches before the next Actions run.

### DrChrono breaks use patient=null
DrChrono treats appointments with `patient=null` as breaks (`appt_is_break=true`). Do NOT use a dummy patient — that creates regular appointments that show on the schedule incorrectly.

### Echo filtering
When we create breaks in DrChrono, they echo back in the ICS feed. The `_is_block_echo()` filter skips them. It matches `[GCal Sync]` in the ICS summary (the reason field we set). If breaks stop being filtered, they'll duplicate into GCal.

### Buffer dedup
`shift_buffers.py` scans GCal for existing tagged buffer events before creating new ones. If you clear `buffer_state.json`, it will adopt existing buffers by time match instead of creating duplicates. Orphaned tagged buffers are cleaned up automatically.

### After any one-time cleanup step in the workflow
Always remove the cleanup step from `sync.yml` after the first successful run, or it will re-run on every sync.

## Config
- `DRCHRONO_BLOCK_PATIENT_ID` — no longer used for break creation (patient=null), but kept for backward compatibility
- `DRCHRONO_BLOCK_PATIENT_NAME` — used for legacy echo filtering in the ICS feed
- `BLOCK_NOTE_PREFIX` = `[GCal Sync]` — tagged in break reason field, used for echo filtering
- `BUFFER_EVENT_TAG` = `shift-buffer-script` — tagged in GCal buffer events for dedup

## Common Issues
- **Duplicates**: Usually caused by running sync locally while Actions is also running, or by state loss
- **DrChrono 401**: Token expired. The Actions caches the rotated token. If local token is dead, you can't run locally without re-authorizing
- **GCal rate limits**: Bulk operations (>50 deletes) need throttling. `_safe_delete` retries on 403 with backoff
- **Actions caches**: If state gets corrupted, delete ALL caches via `gh cache list` / `gh cache delete`. The sync will recover from GCal scan on next run

# Pending model import

Push models to `pending_skins/<character DBID>/` or `pending_graffiti/<faction DBID>/`. Every model needs a matching, same-case `.dff` and `.txd`. Only these pending folders upload. The game server moves them through its pending folders into final paths and confirms database persistence. Actions then downloads and verifies the actual server files, archives them in `custom_skins/private_skins/<character DBID>/` or `custom_graffiti/<faction DBID>/`, and removes only unchanged successful pending inputs.

**Final GitHub folders are storage only. They never upload back to the server**, including manual/retry runs. Actions also never uploads directly into final server folders.

## Skin metadata

Either name both files `man_base=7_slot=1.dff` and `man_base=7_slot=1.txd`, or upload `man.dff`, `man.txd`, and `man.model.json`:

```json
{"base_model": 7, "slot": 1}
```

Slots are 1–3; base models must be valid vanilla skins. If filename and sidecar both specify metadata, they must agree. Graffiti uses base 19477 unless its optional sidecar specifies `base_model`. Folder numbers are **database IDs**, never player IDs or runtime faction IDs. Use simple ASCII filenames; the complete final path must fit 63 characters. Do not use Git LFS pointers.

The existing root-level skin pair has no owner folder and will be reported, not guessed or deleted. The existing `pending_skins/1/1.dff` and `1.txd` need a matching `1.model.json` with the intended base/slot, or explicit filename metadata. No existing uploads are modified automatically.

## Deployment

1. Deploy the matching gamemode implementation of `modelsync import` first; rebuild/deploy its AMX through your normal server process.
2. In this GitHub repository's Actions variables, set `PTERO_PANEL_URL` (HTTPS panel origin) and `PTERO_SERVER_ID` (Client API identifier).
3. Add `PTERO_CLIENT_API_KEY` as an Actions secret. Its account needs this server's file-list/create/upload/download/read/delete permissions and console-command permission. Do not commit credentials.
4. Permit Actions to write repository contents, and ensure branch rules allow its archive commit. The built-in workflow token is used; no GitHub write token belongs on the game server.
5. Verify with disposable test skin/graffiti assets on a test server before production imports.

The Client API uses signed Wings upload/download URLs; the runner must reach both the panel and Wings over HTTPS. API authorization failures are not retried. Temporary 429/5xx/network errors have bounded retries; receipt polling times out after 300 seconds. A job has a 30-minute outer limit.

## Retry and recovery

Use **Actions → Import pending models → Run workflow** to retry. Batch/item identities derive from the exact input hashes/metadata. Successful receipts avoid re-upload after an archive failure; the durable server journal reconciles interrupted imports without duplicate IDs. Failed items remain pending and make the job unsuccessful even when other items archive successfully. An archive-only push does not trigger imports.

An occupied slot, retired skin, unknown owner, or different existing final asset is a conflict requiring correction. Do not overwrite an active model's filename; supply a new pair/name. Never delete pending files merely to hide an error. Newer pending contents/metadata are preserved as a whole item when an older batch archives.

Receipts live on the server under `scriptfiles/model_sync/<batch ID>.json`; verified reports live in `model_imports/`. Acknowledgement records the archive commit and retains staging/journal for recovery; cleanup is deliberately conservative and never removes final models. This workflow does not enable game-client redirects to GitHub.

Batches contain at most 32 items and 256 MiB of binary data. Uploads read/release one file at a time; final downloads are bounded by that batch limit. Uploaded sizes are checked through the panel's file listing, and returned bytes are SHA-256 verified before archiving.

Recovery accepts an existing pending/final file only when the journal proves that exact item's recorded move. If the server crashes between a physical move and its journal update, ownership is ambiguous: the import fails safely and retains the inputs for operator verification. It never guesses that an existing file belongs to the retry, and another item cannot claim a journal-reserved path.

## Local checks

```sh
python3 -m unittest discover -s tests -v
python3 tools/model_sync.py sync --root . --dry-run
```

Dry run performs no network writes. Invalid pending files produce a nonzero exit and remain unchanged. Use a clean automation checkout for real runs; archive commits are reconciled in disposable worktrees without resetting the invoking checkout. No force push is used.

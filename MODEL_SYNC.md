# Pending model import

Push models to `pending_skins/<character DBID>/`, `pending_graffiti/<faction DBID>/` or `pending_public_skins/<collection>/` (see Public skins). Every model needs a matching, same-case `.dff` and `.txd`. Only these pending folders upload. The game server moves them through its pending folders into final paths and confirms database persistence. Actions then downloads and verifies the actual server files, archives them in `custom_skins/private_skins/<character DBID>/` or `custom_graffiti/<faction DBID>/`, and removes only unchanged successful pending inputs.

**Final GitHub folders are storage only. They never upload back to the server**, including manual/retry runs. Actions also never uploads directly into final server folders.

## Skin filenames — no metadata JSON

Give the DFF and TXD the exact same basename, including capitalization. All skin details are in that name.

| Kind | Basename | Example pair |
| --- | --- | --- |
| Public | `baseid_gender_race` | `7_male_black.dff` + `7_male_black.txd` |
| Private | `charid_slot_baseid_gender_race` | `1_1_7_male_black.dff` + `1_1_7_male_black.txd` |

Gender is `male` or `female`. Race is `white`, `black`, `asian` or `hispanic`. Use lowercase words and canonical decimal IDs (no leading zeros). Base IDs are 0–311 except 74. Private slots are 1–3. The private filename's character DBID must match its folder DBID; these are database IDs, never online player IDs.

For Flint_Carter (character DBID 1), slot 1, base 7:

```text
pending_skins/1/1_1_7_male_black.dff
pending_skins/1/1_1_7_male_black.txd
```

Choose the gender/race that describes the supplied skin. The matching updated gamemode saves these tags for private skins as well as public skins. Do not add a `.model.json` sidecar to these filenames; filename-style entries reject sidecars rather than allow contradictory metadata.

## Public skins

Public skins go in `pending_public_skins/<collection>/`. The collection number is a positive folder namespace, not a character/faction ID. Archives go to `custom_skins/public_skins/<collection>/`.

```text
pending_public_skins/1/7_male_black.dff
pending_public_skins/1/7_male_black.txd
```

If multiple skins share a base ID, gender and race, append `_v1`, `_v2`, etc. (1–999). Each new variant has its own matching pair, so models with different geometry cannot accidentally share a DFF:

```text
pending_public_skins/1/7_male_black_v1.dff
pending_public_skins/1/7_male_black_v1.txd
pending_public_skins/1/7_male_black_v2.dff
pending_public_skins/1/7_male_black_v2.txd
```

The server assigns each pair a free custom model ID. These filenames provide the vanilla base and tags, not the assigned custom ID. The full final model path must fit 63 characters. Do not use Git LFS pointers.

The existing queued collection and its old sidecars remain supported solely to preserve its current item/batch identities and import receipts. Do not rename an already submitted batch as a retry: new names are new import identities. New uploads use the filename formats above. Internal receipts/reports retain their machine-readable format; no operator-authored JSON is required for a new skin.

Graffiti remains in `pending_graffiti/<faction DBID>/` with a matching named DFF/TXD pair and default base 19477. Its existing filename `base=<ID>` override remains supported.

## Deployment

1. Deploy the matching gamemode implementation of `modelsync import` first; rebuild/deploy its AMX through your normal server process.
2. In this GitHub repository's Actions variables, set `PTERO_PANEL_URL` (HTTPS panel origin) and `PTERO_SERVER_ID` (Client API identifier).
3. Add `PTERO_CLIENT_API_KEY` as an Actions secret. Its account needs this server's file-list/create/upload/download/read/delete permissions and console-command permission. Do not commit credentials.
4. Permit Actions to write repository contents, and ensure branch rules allow its archive commit. The built-in workflow token is used; no GitHub write token belongs on the game server.
5. Verify with disposable test skin/graffiti assets on a test server before production imports.

Temporary HTTP compatibility: set Actions variable `PTERO_ALLOW_INSECURE_HTTP=true` to permit an HTTP panel and HTTP signed transfers. Credentials and models then travel unencrypted. HTTPS remains the default, certificate validation remains enabled, authenticated cross-origin redirects and HTTPS-to-HTTP redirects remain blocked. Remove this variable after configuring HTTPS.

The Client API uses signed Wings upload/download URLs; the runner must reach both the panel and Wings over HTTPS, or HTTP with the explicit compatibility setting above. API authorization failures are not retried. Binary uploads allow 180 seconds per attempt; other requests allow 30 seconds. Temporary 429/5xx/network errors have bounded retries; receipt polling times out after 300 seconds. A job has a 4-hour outer limit, enough for a large public collection (about 45 batches).

## Retry and recovery

Use **Actions â†’ Import pending models â†’ Run workflow** to retry. Batch/item identities derive from the exact input hashes/metadata. Successful receipts avoid re-upload after an archive failure; the durable server journal reconciles interrupted imports without duplicate IDs. Failed items remain pending and make the job unsuccessful even when other items archive successfully. An archive-only push does not trigger imports.

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


## Explicit repair after deleting persisted skin files

Normal imports deliberately reject missing final files for an already persisted model. For an operator-requested restoration, run **Restore deleted skin files** with the exact batch ID. This manual-only repair accepts a bounded batch of private or public skins whose completed receipt records previously persisted model IDs, either with `persisted` status or a later `PATH_CONFLICT`. It may re-upload matching pending inputs into staging when staged copies are missing; final destinations are restored only by server-side renames. The original pending manifest must still match. It verifies all staged files against their original SHA-256 digests before requesting server-side renames into missing final paths; shared public DFFs are moved once; differing existing files are never overwritten. It performs no uploads into final folders and no database edits. The gamemode must then confirm persistence with the original model ID before final downloads are verified and archived. Retired assets and other failures are not eligible. Partial repairs can be retried when the existing files match exactly.


## Retire the old skin collection

Deploy the matching gamemode first, then manually run **Retire legacy skins**. The server copies original `custom_skins` rows into `legacy_skin_backup` before changing registrations. It retires old public, private (including Flint_Carter), faction and VIP records while preserving `SKIN_TYPE_PUBLIC` records whose DFF and TXD are both under `custom_skins/public_skins/`. Each removal uses the existing retirement transaction: wardrobe ownership is removed, equipped legacy outfits fall back to vanilla skins, and old IDs/files are tombstoned against re-import. Missing model files do not prevent database cleanup. The maintenance receipt is `scriptfiles/model_sync/legacy_cleanup.json` and reports actual retired/failed counts. Files are retained for connected clients until restart; they are no longer available as active skins. Restart the server after successful retirement before continuing the public collection import. Graffiti and vanilla skins are not selected by this operation.


## Full filename-collection reset

The pre-reset repository is preserved by tag `codex/model-reset-backup-20261023-5f60872`. The rebuilt pending collection has 1,423 exact-case matching pairs, numbered `_vN` where necessary, with no metadata JSON sidecars. Every binary was checked against its original SHA-256 hash. Original-to-new mappings are retained locally in the reset preparation artifact.

The `.skin-reset-required` marker blocks live imports. Deploy the matching `som_model_reset.amx` build first, then run **Reset all skin registrations**. This explicit workflow backs up and retires all custom skins, including already imported public skins, requires a successful full-scope receipt, and confirms a server restart. Native registrations therefore clear before re-import. It preserves graffiti and retains old server files/journal entries as recovery evidence. After verifying this workflow succeeded, remove the marker and run **Import pending models**. Archive folders and reports will be recreated only from confirmed server imports.

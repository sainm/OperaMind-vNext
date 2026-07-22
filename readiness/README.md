# MVP readiness evidence

`mvp-readiness.silver.json` records the original development baseline. `mvp-readiness.json` records the current repository readiness state. The selected VisionDemo case is frozen and `golden_dataset` has passed with reviewed evidence. The real local Nomic Provider contract, VisionDemo v3 human Impact/Grant chain, and revision-bound target Deployment have evidence. GitHub Copilot remains pending. A PostgreSQL/Chrome full-regression report becomes historical as soon as its source digest is stale; the automated runner keeps that gate pending until a clean fixed-command rerun binds the current tree.

## Automated evidence synchronization

`operamind-readiness` derives gate state from Canonical PostgreSQL and immutable real-test observations. It never edits the customer target repository. Missing or inconsistent input produces a `pending` gate with no evidence or reviewers. A successful sync writes deterministic `readiness/evidence/auto-*.json` files first, validates their schemas and digests, validates a temporary manifest, and atomically publishes the manifest last. Replaying unchanged inputs does not rewrite files or increment `manifest_version`.

Apply migrations and synchronize one bounded case:

```bash
export OPERAMIND_DATABASE_URL='postgresql://...'
.venv/bin/operamind-readiness sync \
  --project-id visiondemo \
  --analysis-case-id visiondemo-expense-status-filter-p6
```

Run and record the real local Provider contract. The supplied profile/model/dimensions must also match a current ready Search Index and active Profile binding in PostgreSQL:

```bash
.venv/bin/operamind-readiness probe-provider \
  --project-id visiondemo \
  --analysis-case-id visiondemo-expense-status-filter-p6 \
  --profile-version-id local-openai-compatible-v1@1.0.0 \
  --model text-embedding-nomic-embed-text-v1.5 \
  --dimensions 768 \
  --endpoint-origin http://127.0.0.1:1234
```

After the source tree is final, run the exact full-regression command, capture PostgreSQL and Chromium versions, append its verified observation, and resynchronize:

```bash
.venv/bin/operamind-readiness run-full-regression \
  --project-id visiondemo \
  --analysis-case-id visiondemo-expense-status-filter-p6
```

Human Approval and Deployment evidence require no external receipt: they are derived from normalized Impact/Confirmation/Packet/Grant and revision-bound UI Plan/Run/Validation rows. Copilot is intentionally different because the edit tables alone do not prove the editor origin. Record a reviewed session only after VS Code GitHub Copilot completed the immutable Coding Task with the fixed MCP Packet/Grant.

Before review, validate the completed VS Code session JSONL and exact request. The inspector requires a signed-in, non-BYOK GitHub Copilot request with matching session identity and completed, confirmed `copilot_get_coding_task`, `copilot_run_task_command`, `copilot_validate_task_diff`, and `copilot_record_task_result` MCP calls. Quota failures, incomplete responses, missing tools and unconfirmed tools are rejected:

```bash
.venv/bin/operamind-readiness inspect-copilot-session \
  --input <VS Code workspaceStorage chatSessions/session.jsonl> \
  --request-id <request-id>
```

After checking the sanitized inspector output, bind that exact request to the completed Canonical Coding Task and synchronize readiness in one command:

```bash
.venv/bin/operamind-readiness record-copilot-session \
  --project-id visiondemo \
  --analysis-case-id visiondemo-expense-status-filter-p6 \
  --coding-task-id <completed-coding-task-id> \
  --input <VS Code workspaceStorage chatSessions/session.jsonl> \
  --request-id <request-id> \
  --reviewed-by <reviewer-identity>
```

The command derives Packet, Grant, base revision, result revision, project, and case from the completed Canonical Task/Edit Result chain; callers cannot substitute those identities. It records only sanitized session metadata and the transcript SHA-256, not the transcript. `record-observation` remains available for other reviewed observation envelopes. Provider observations require a project and no case; Copilot observations require both; full-regression observations require neither. Placeholder values are rejected.

Each passed gate must reference at least one repository-relative evidence file by SHA-256 and name at least one attesting identity in the existing `reviewers` field. Every evidence envelope must satisfy `mvp-evidence.schema.json`: `review_status=pending` has no identity, `reviewed` records a human judgment, and `verified` records deterministic automation for a Provider probe, Canonical Deployment run, or fixed full regression. A passed gate accepts finalized `reviewed` or `verified` evidence, and the validator cross-checks its gate, identity, type, observation time and identities with the readiness manifest. Schema, hashes, paths, fixed commands and test counts are machine-checked; they do not require redundant human approval. Golden evidence binds both the manifest SHA-256 and an `operamind-golden-dataset-v1` digest covering the selected manifest, Golden schemas and every referenced case JSON file; it also checks dataset identity/counts, re-runs Golden readiness, and must reference the manifest selected by the baseline command. Full-regression evidence binds the current executable source, tests, migrations, Contract/Profile JSON and project configuration with the deterministic `operamind-source-tree-v1` digest. Evidence may be sanitized, but it must preserve the external identity, fixed version/revision, outcome and observation time needed to audit the gate. Secrets, browser storage state, source code, screenshots containing sensitive data, and raw Provider responses must not be committed.

`readiness/templates/*.example.json` files are capture templates only. They start with `review_status=pending` and no identity. The validator rejects any readiness manifest that references a template path or `.example.json` file, even if its SHA-256 is correct. First copy the relevant template into `readiness/candidates/`, replace every observation placeholder, and validate it. Evidence requiring judgment becomes `reviewed` after confirmation. A deterministic full-regression report becomes `verified` after the fixed command, source digest, environment and zero-failure/zero-skip result all validate. Final evidence moves into `readiness/evidence/`, receives a SHA-256 reference in `mvp-readiness.json`, and may make its gate `passed`. Evidence containing `replace-with` or `placeholder` is rejected after schema validation.

`readiness/candidates/` stores captured but not yet finalized results. Candidate files must use `review_status=pending`, keep `reviewed_by` empty, contain no capture placeholders, and use a `.candidate.json` suffix. They are outside `readiness/evidence/`, so they cannot pass a gate. Validate a captured result with:

```bash
operamind-baseline \
  --validate-evidence-candidate readiness/candidates/<evidence>.candidate.json
```

The repository currently has no pending candidate envelope. Reviewed historical/local evidence includes:

- `readiness/evidence/golden-dataset-1.0.0.json`: reviewed Golden manifest and dataset digest.
- `readiness/evidence/full-local-regression-2026-07-17.json`: historical verified PostgreSQL/Chrome report; its old source digest is intentionally not referenced by the current manifest.

Before changing a readiness gate, preflight the finalized file independently:

```bash
operamind-baseline \
  --manifest golden-dataset/manifest.golden.json \
  --validate-reviewed-evidence readiness/evidence/<evidence>.json
```

For Golden evidence this preflight also requires the bound manifest to be `golden / frozen`, requires at least one manifest identity and matching structured per-case review identity, re-runs all Golden readiness checks, and verifies that it is the manifest selected by `--manifest`. After preflight succeeds, compute the evidence digest, add the exact reference and identity set to `mvp-readiness.json`, and only then change the gate to `passed`.

Gate state is strict. A `pending` gate must have no `evidence_refs` and no `reviewers`; partial evidence should stay outside the readiness manifest until it proves the gate and has been reviewed. A `passed` gate must not keep `blocking_reason`.
These state-shape rules are encoded in `mvp-readiness.schema.json` as well as enforced by the Python validator, so schema-only tooling and `operamind-baseline` reject the same pending/passed shape errors.
The required gate set is also closed at schema level; unknown gate IDs are rejected before evidence is inspected.

Normal baseline validation checks the default silver development files and any evidence digests:

```bash
operamind-baseline
```

Print the selected readiness stage without changing pass/fail semantics:

```bash
operamind-baseline --print-readiness-status
```

For CI or scripts, print the same readiness stage as JSON:

```bash
operamind-baseline --print-readiness-json
```

The JSON output includes each gate's expected evidence type and, for pending non-Golden gates, the template path to copy into `readiness/evidence/`.
If the selected readiness manifest is malformed, status output fails closed with `readiness.summary_unavailable`; scripts must treat that as a failed readiness check, not as an unknown but acceptable stage.

CI can also require an exact stage with the normal baseline checks:

```bash
operamind-baseline \
  --manifest golden-dataset/manifest.golden.json \
  --readiness-manifest readiness/mvp-readiness.json \
  --require-readiness-stage golden_ready_partial
```

The stage names are intentionally explicit: `dev_silver` means no readiness gate has passed, `golden_ready_partial` means the Golden Dataset evidence is valid but at least one MVP gate is still pending, and `mvp_ready` means every required gate is passed and the manifest status is `ready`.

Frozen Golden Dataset readiness is selected explicitly:

```bash
operamind-baseline \
  --manifest golden-dataset/manifest.golden.json \
  --readiness-manifest readiness/mvp-readiness.json \
  --require-ready
```

The whole MVP can be declared ready only with frozen Golden data and every external/local gate passed:

```bash
operamind-baseline \
  --manifest golden-dataset/manifest.golden.json \
  --readiness-manifest readiness/mvp-readiness.json \
  --require-mvp-ready
```

The current manifest references a verified `full_local_regression` report for the current source digest. The fixed suite passed all 453 collected tests with PostgreSQL 18.4 and real headless Chromium 150, with zero failures and zero skips. The MVP-ready command now fails only because `github_copilot_live` remains pending; all other required gates have finalized evidence. Do not change an external gate to `passed` merely because implementation code or a Fake-based test exists.

Print the digest to place in a real full-regression Evidence envelope only after the tested tree is final:

```bash
operamind-baseline --print-source-tree-digest
```

Print the digest that freezes the selected Golden manifest, schemas, and referenced case files:

```bash
operamind-baseline \
  --manifest golden-dataset/manifest.golden.json \
  --print-golden-dataset-digest
```

Print the digest to place in a readiness manifest evidence reference after an evidence file has been reviewed:

```bash
operamind-baseline --print-evidence-digest readiness/evidence/<evidence-file>.json
```

The path must be repository-relative. This command only prints the digest; it does not change the readiness manifest or mark a gate as passed.

Pending gate templates:

- `embedding_provider_live`: finalized at `readiness/evidence/local-embedding-provider-2026-07-18.json` after the opt-in live Provider contract test passed against local Nomic; the template remains for later recapture.
- `human_approval_e2e`: finalized at `readiness/evidence/visiondemo-human-approval-p6-v3.json` from the user-confirmed v3 Impact/Confirmation/Packet/Grant chain; the template remains for later cases.
- `github_copilot_live`: `readiness/templates/github-copilot-live.example.json`; fill it only after a signed-in GitHub Copilot session uses the MCP handoff with the fixed Edit Packet and Approval Grant and produces the recorded result revision.
- `target_deployment_e2e`: `readiness/templates/target-deployment-e2e.example.json`; fill it only after the revision-bound target Deployment passes UI verification and sanitized evidence has stable IDs.
- `full_local_regression`: `readiness/templates/full-local-regression.example.json`; fill it only after the fixed full local regression command, including PostgreSQL-backed integration and real browser checks, completes with zero failures and zero skipped tests. The command explicitly excludes only `test_live_embedding_provider.py` (proved by its separate live Provider gate) and the legacy local-only Silver fixture `test_golden_screen_change.py` (not part of the frozen portable Golden Dataset). The validator rejects any additional omission and requires `passed == collected`.

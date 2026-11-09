# WeDrive CLI deployment implementation plan

> Execute inline using superpowers:executing-plans; user authorized deployment of the established CLI approach.

**Goal:** Download the authorized target folder and incrementally update the existing RAG database every five minutes.

**Architecture/design:** A new bounded CLI sync entry point searches the authorized space, handles all cursor pages, filters exact folder path segments, downloads into an isolated cache, validates size, converts legacy DOC with local Office automation, and imports using stable cloud identity. Only successfully indexed files advance the manifest. No source mutations or automatic deletion. Preserve existing documents on authorization/conversion/index errors. Record per-run health, last success, and errors without tokens or raw identity context. Windows scheduled task runs while the authorized Windows user is logged in, ignores overlapping runs and catches up after missed starts. Old unsafe directory-scanner task is backed up and disabled.

**Tech stack:** Existing Python virtual environment, official wecom-cli 1.3.4, local Word/WPS COM, Windows Task Scheduler.

**Spec:** User request and docs/operations/wedrive-640008-research-20261109.md.

## Constraints and review focus

- Only 法律事务部（含商务合同等文件）/商务合同模板/深圳市世纪恒程知识产权代理事务所 descendants.
- Cache filenames derive from cloud ID, not untrusted remote filenames; path traversal rejected.
- Bound pagination, subprocess timeouts, no raw error output with tokens.
- DOC conversion disables macros and opens source read-only; no remote model calls enabled.
- Same cloud file changed => old version superseded atomically by storage upsert.
- Failure => no checkpoint advancement; no missing-file deactivation.
- Business authorization expiration unknown; health must not claim permanent authorization.

## Tasks

- [x] Write failing tests: exact folder boundary, pagination, no-change skip, changed-version replacement/retrieval, failure retry without losing active version, size mismatch.
- [x] Implement CLI adapter, cache/manifest sync pipeline and local DOC conversion.
- [x] Run focused tests and existing WeDrive end-to-end suite.
- [x] Validate real conversion and full target import, repeat scan for idempotence.
- [x] Back up/disable old task, register and start new scheduled task, inspect task result and health.
- [x] Document invocation, status paths, authorization renewal and rollback.

## Execution ledger

- User explicitly authorized trusted-source automatic activation during implementation. Imported versions begin pending, then activate through audited approval; preserves old approved version on parse/approval failure.
- RED→GREEN: missing pipeline; CLI JSON vs downloaded bytes; Windows extended path prefix; governed retrieval + pending-version protection.
- Verification: 25 tests, ruff clean; 5 actual DOC imports, 248 approved clauses, each real governed retrieval hit; repeat scan skips 5; scheduled execution exit 0.
- Default interval five minutes; automatic deletion off. Original directory scanner task exported and disabled. No changes to user build_doc.py.
- Office timeout may leave converter process; documented operating limitation. No indiscriminate process termination.

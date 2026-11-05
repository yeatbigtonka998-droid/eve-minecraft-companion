# Records and status conventions

## Statuses (from the checkpoint programme)
`UNTESTED` · `IN PROGRESS` · `IMPLEMENTED UNTESTED` · `PASS` · `FAIL` · `BLOCKED` · `DEFERRED OPTIONAL` · `STALE`

- A plan entry, a commit, or "the code is there" is **never** evidence. PASS needs an evidence folder.
- A missing dependency is BLOCKED, not PASS. A relevant change to code, data, prompt, model, permission or version makes a PASS **STALE**.
- Count every attempt, including crashes, timeouts, deaths and human rescue. Never restart a bad attempt silently.

## Evidence folders
`evidence/CP-NN/<yyyy-mm-dd-HHMM>-attempt-N/` containing: `RESULT.md` (hypothesis, fixture, versions, command/procedure, expected vs observed, attempts table, elapsed, deaths, interventions, violations, resource use), logs, before/after state, screenshots/recordings for movement/combat/construction/visual work. Redact tokens and keys. Evidence is gitignored (large); `RESULT.md` is copied into the part's `evidence-summaries/` folder which **is** committed.

## Per-part `parts/<part>/STATUS.md` (the only file the orchestrator needs to read)

```
# <part> status — updated <date>
## Enabled / passed
CP-NN PASS <date> evidence: evidence/CP-NN/… (conditions: …, limits: …)
## In progress
CP-NN IN PROGRESS — next experiment: <hypothesis + pass condition>
## Failed / blocked / stale
CP-NN FAIL — cause … / BLOCKED on … / STALE because …
## Decisions I made (ordinary implementation details)
- …
## Contract change requests  (orchestrator merges these)
- …
## Questions for Hunter  (only consequential ones: permission, cost, scope, meaning of success)
- …
## Pinned versions and paths
- …
```

## Repo rules for every coder chat
1. **Folder ownership.** You edit only `parts/<your-part>/` (plus the exceptions your brief names). Never edit another part's folder, `contracts/`, `records/` or `briefs/`. Put requests in your STATUS.md.
2. **Commits.** `git add parts/<your-part>` then commit, several times a day, message prefixed `<part>:`. If `.git/index.lock` exists, wait 2 s and retry; never delete it. Never `git add -A`. Never force-push, never rebase, no branches (everyone works on `main` in the same checkout).
3. **Secrets.** Never write a token, password or API key into the repo, a log, a prompt or chat. Use env vars; the 45L's secrets stay on the 45L.
4. **Valuable world.** You never touch Hunter's real world. Dev/qualification servers live under `C:\eve\server-dev\` and `C:\eve\server-qual\` on the 45L (fixtures are worlds inside them, switched by `level-name`) and are disposable; the foundation scripts refuse any other root.
5. **Honesty.** Distinguish *proposed*, *implemented*, *tested* and *qualified* in every update. If you could not run something, say which check did not run and why.
6. **Scope.** Do the smallest complete thing that supports the next experiment. No frameworks for later. Mark deliberate shortcuts with a `ponytail:` comment naming the ceiling and the upgrade path.

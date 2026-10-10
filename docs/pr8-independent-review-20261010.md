# PR #8 independent review and integration evidence — 2026-10-10

## Exact revisions and ownership

Review: `82735270d466e810818fe671d2c552a22c9a7946`, PR #8, draft, base PR #6 (`2e997bc823d9d06cb3c8dfe5c0e438a1ad383804`). The original checkout was tested detached and was not pushed. GitHub returned no commit statuses and no Actions runs for that SHA. The existing Node workflow only covered PRs targeting main or pushes to the #6 branch; #8 matched neither trigger. The parent independently reported zero check runs. Direct `gh api` verification was network-forbidden in this environment, so it did not produce additional check-run evidence.

PR #7 (`54ac6e54790a3b4d2794d2dcd0223db5674a1d68`) is a sibling of #8 on #6, not part of #8. PR #4 (`c09de0ca321a008efd311a04d393f92331237686`) and #5 (`4e669e235980477b1728f2709c7a276ad613eced`) are in **this same repository**, on the separate #1→#2→#3→#4→#5 Microcap/Termux chain.

The new integration branch starts on #8 and brings in #7 and #5 through local merge commits, preserving their existing commit ancestry. No original PR branch or main was changed. Proposed PR base: `devin/1791618257-tjk-freshness-backtest` (#8). The cumulative diff includes existing #7 and #1–#5 work; the new safety/bridge commit is the focused review unit. Retarget/retest after dependency changes; no merge or deployment authority is implied.

No repository AGENTS.md or .agents/skills files were found in the fetched trees, and the environment .agents directory was empty. Existing package documentation and workflow instructions were read. No new dependency was installed.

## Findings and priority

1. **P1 — quote/time mismatch.** `server.mjs` scored GANYAN `G` while freshness examined the latest history timestamp without matching its price. On unmodified #8, synthetic history at 3.20 with current G at 99.00 still produced OK/VERIFIED. The new regression fails on #8 and passes after a matching latest-price/time check; inconsistent same-time points or unordered/untimed histories fail closed. Each runner now carries its bound `quoteAt`; `sourceTime` is the oldest bound quote time, not the checksum clock. A VERIFIED freshness object alone is not complete provider verification.
2. **P1 — historical lookahead.** The CLI used post-race `KOSMAZ` to exclude runners before prediction. Program/ratings/participant/AGF archives also lack independently demonstrated pre-cutoff capture. The original core accepted such inputs as OK. The new core requires an explicit trusted pre-cutoff input snapshot timestamp; archives with no such provenance return PAS and no leader/baselines. The CLI no longer consumes post-race withdrawals and currently cannot attest a pre-cutoff snapshot, so it cannot claim historical predictive performance. A caller must supply actual captured input provenance, not relabel an archive with an invented timestamp.
3. **P1 — result contamination of reported accuracy.** PAYLOAD_ONLY (missing official result CSV) records entered success denominators. Now only CONFIRMED results count, including per-venue summaries. Conflicts, missing and single-source results are excluded. This is accuracy accounting, not a return/profit calculation.
4. **P1 — invalid cutoff arguments.** Negative cutoff could include post-race odds. Nonfinite/zero cutoff, invalid sample count, invalid calendar dates and unsafe request-budget settings now stop before network/cache activity.
5. **P2 — spoofable rate-limit identity.** TRUST_PROXY accepted the first client-controlled X-Forwarded-For entry. The implementation now uses the socket peer; forwarding headers cannot reset the budget. Behind a proxy clients intentionally share that peer's budget until a separately reviewed trusted-hop policy exists. The unsafe Render flag was removed; no infrastructure configuration was applied.
6. **P2 — invalid resource limits.** Negative cache size can hang eviction; invalid concurrency can hang requests. Constructors now reject invalid limits. Upstream queue length is still not capped; extreme traffic/slow upstream remains a scalability limitation.
7. **P1 — incomplete Telegram integration.** #7's PR6 bridge always returned SOURCE_FRESHNESS_UNVERIFIED, and Node tests proved no Telegram connection. New read-only bridges use the existing Node engine and `equity_guard`, retaining the stricter 60-second race odds and 5-second equity gates. They do not start a receiver or certify missing evidence. /start, /help and Turkish PAS explanations are added. Missing basketball/football providers remain explicit PAS.

## RED/GREEN evidence

Logs are under [test-evidence/2026-10-10](test-evidence/2026-10-10/). Expected RED runs are not unresolved final-suite failures.

| Run | Runtime | Result |
| --- | --- | --- |
| Original #8 npm test | Node 24.19.0 | 45/45 pass |
| New behavior regressions against unchanged #8 | Node 24.19.0 | 10 pass / 5 fail (price binding, snapshot provenance, result accounting, cutoff, proxy) |
| Cache/limiter settings before validation | Node 24.19.0 | 0 pass / 1 fail |
| Telegram bridge/help/PAS tests before bridge changes | Python 3.12.14 | 0 pass / 2 failures / 1 missing-module error |
| Integration npm test | Node 24.19.0 | 51/51 pass |
| Integration Telegram/engine/ledger suite | Python 3.12.14 and 3.13.5 | 90/90 each |
| Existing equity/Termux suite | Python 3.12.14 and 3.13.5 | 488/488 each |

The 45/45 claim is independently reproduced. The new regressions expose conditions those original tests missed. Do not treat a passing unit suite or the VERIFIED label as live-data proof. Local Node 20/26 were unavailable; the phone's reported Node 26.3.1 and Python 3.13.13 were not tested here. No dedicated lint/typechecker configuration exists; all 16 JavaScript files pass node --check, all 47 Python files pass AST parsing, and git diff --check passes (see syntax-checks.log).

The immutable official Belmont program fixture has SHA-256 `0fd8da652a21ccf6fbe5364e2038a8059cdd2ecd277a2ac7e152a3d8893c6734`. Its provenance file records download **after** the races; it is a parser fixture, not a historical prediction snapshot or official result. All odds/history clocks, bridge evidence and settlement verifier inputs in the new end-to-end tests are clearly synthetic. Both Belmont 5 and 6 keep their own eight active runners (race 5 has nine program entries including a withdrawal). AGF invariance tests remain in force.

## Live checks and blockers

- A single redirect-disabled, bounded public TJK checksum GET could not confirm a response. No live quotes, authoritative source clock semantics or historical-result access were confirmed in this environment.
- TM_PROJECT_ID and TM_AUTH_KEY_HEX were present (only presence inspected). A single existing-client read-only health attempt failed during configuration preparation; no authenticated phone result was obtained. No values, prefixes, hashes, raw exception messages or credential files were exposed. No access/configuration changes were attempted.
- The actual Telegram receiver code/framework is absent from this repository. The parent later supplied the phone's working directory `~/downloads/spor_radar_microcap` and several checkout HEADs, including tjk-spor at 79d7775. Neither directory names nor process counts establish which code is active or healthy. No phone installation/service change was made.
- Existing Node analysis lacks complete independently verified event/runner/program/source-delay evidence. `LocalRaceAnalysisReader` can read the existing loopback engine but deliberately adds no provenance; without a reviewed evidence callback /at remains PAS. This is a tested integration boundary, not a deployed live bot.
- Basketball/football data providers and production authoritative-result verifiers are not configured. Predictions remain pending until an explicitly registered verifier validates a final result. Synthetic settlement tests do not establish a live verifier.
- Fintable research data alone lacks required NBBO, spread/news/volume confirmations and freshness. It cannot turn into a real-time opportunity via the new equity bridge.
- No Telegram message, wager, order, production deployment, remote branch merge or auto-merge was performed. Only the new integration branch/draft PR is authorized for publication.

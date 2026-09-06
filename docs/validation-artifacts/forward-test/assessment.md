# Independent forward-test assessment

Scope: tested the installed delivery-harness v1.0.0 in a standalone local Python/Git fixture. This is an observed workflow test, not a security audit or a rating. No blocking harness error was observed in the requested path. The package and installed skill files were not modified. This assessment covers the original installed manifest; subsequent upstream CLI improvements are not covered by these results.

## Fixture and method

Working directory: /workspace/work/forward-test.

Python 3.12.3, stdlib unittest, Git repository with no remote. The fixture began with `def mean(values): return sum(values) / len(values)` and one normal-input test. Its pre-existing AGENTS.md required stdlib unittest, descriptive test names, and a docstring for public behavior changes. Installation preserved the original AGENTS.md bytes as a prefix. The final implementation obeyed all three local conventions. See installation-audit.json and local commits b3f2d09 / 7b1231e.

I installed with the supplied install.py, read only the installed SKILL.md plus runtime.md and engineering.md, configured the actual project check, and ran the requested bug workflow. The risk was medium because the task changes a local function's behavior; no production, public service contract, sensitive data, migration, or external operation exists in this fixture. No extra agent was used for the fixture; review was explicitly recorded as self-review.

The requested one-character README correction was handled as a separate request after completion of the bug workflow. Additional audit files under evidence/ are test instrumentation and were Git-ignored; they are not harness-created task paperwork.

## Observed results and claim checks

1. Baseline: the original one-test suite passed. After adding the new exact-error regression test, the harness unit check exited 1 with an actual ZeroDivisionError traceback; the normal-input test still passed. After the implementation changed, the same unit check exited 0 and its raw log showed two passing tests. This verifies the requested empty list input and the existing normal list input, without claiming exhaustive input coverage.
2. Implementation: calculator.py checks `len(values) == 0`, raises `ValueError("values must not be empty")`, and retains the normal `sum(values) / len(values)` expression. The added docstring satisfies the existing instruction. The change was limited to calculator.py and its test before the later README request.
3. Acceptance/review: A1 and A2 were linked to the actual unit check and test file. A gate probe before review correctly exited 1 with `Current review is missing`. A self-review of the final diff and raw logs was then recorded; it found no blocking issue in that narrow scope. No independent review was claimed.
4. Completion/handoff: the subsequent gate and close exited 0 with READY, the recorded local-evidence-only meaning, and no outstanding issues. `.harness/tasks/empty-mean/handoff.md` describes the behavior change, red/green evidence, compatibility note for callers catching ZeroDivisionError, review limits, and local delivery scope. `fix-response.md` contains the simulated final response at that point; its claims match the then-current code, raw logs, review, and completion record. There was no external publication.
5. Short path: README.md changed from `calcu1ator` to `calculator` at exactly one byte. The diff and whitespace check were inspected. No new task, project test, independent review, or design document was created for that correction. All existing `.harness/tasks` file hashes stayed identical during the short path. See short-path-audit.json and typo-diff.log. `typo-response.md` claims only the edit and performed diff checks; it does not claim tests were rerun.

## Concrete friction and limits observed

- Failure diagnosis takes an extra discovery step. `check --id empty-mean` printed only `unit: failed (0.064s, exit=1)`. The traceback and test count were retained in a timestamped JSON record, but the CLI output did not give that record path. To obey engineering.md's instruction to inspect actual counts and failure meaning, I had to run `rg --files --hidden .harness/tasks/empty-mean` and read the relevant record. The evidence exists and is correct; showing its path after a failed check would reduce this friction. See regression-red.log and check-logs.json.
- The whole-tree freshness rule has a visible cost for the short path. As an assessment-only follow-up, I reran the already-completed bug task's gate after the one-character README change. It exited 1: unit was `stale`, both acceptance items were `missing-or-stale`, and the current review was missing. The earlier completion.json still records the historical READY snapshot. This is the documented conservative behavior, not an observed false READY, but consumers must distinguish a historical completion record from current readiness. I did not rerun tests or rewrite evidence just to make the earlier gate green after a documentation-only task. See gate-ready.log, completion-before-typo.json, and gate-after-typo.log.
- The package does not make test quality or review independence machine-verifiable. I read the raw unittest output and explicitly declared a self-review. The demonstrated READY establishes local evidence for two tests and this review; it does not establish external consumer compatibility, production results, or independent review.

The initial project-check configuration was necessary and documented; it was not treated as a defect. The tested path did not require credentials, new dependencies, network, user approval, release tools, or other agents. This test does not cover Windows process handling, timeout cleanup, high/critical controls, cross-language stacks, malicious local edits, installation collisions/uninstall behavior, or deployed operation. Those are untested here, not demonstrated failures.

## Evidence map

- commands.md and commands.jsonl: exact command arguments, order, exit statuses, and raw-log paths.
- install.log, original-agents.md, installation-audit.json: installation and preservation of existing conventions.
- baseline-tests.log, check-logs.json, regression-red.log, regression-green.log: actual before/after testing.
- final-fix-diff.log, whitespace-check.log: reviewable bug change and whitespace validation.
- .harness/tasks/empty-mean/task.json, records/, review.md, handoff.md, completion.json: actual harness artifacts.
- gate-before-review.log, gate-ready.log, close.log: the required review and completed bug workflow.
- fix-request.txt / fix-response.md and typo-request.txt / typo-response.md: the two request/response pairs used by the forward test.
- short-path-audit.json, typo-diff.log, typo-whitespace-check.log: the separate one-character correction.
- gate-after-typo.log: current old-task readiness after the independent documentation edit.

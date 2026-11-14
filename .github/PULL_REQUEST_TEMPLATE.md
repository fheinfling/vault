<!-- .github/PULL_REQUEST_TEMPLATE.md — agents fill this in; humans skim it. -->

## What and why
<!-- One paragraph. Link the issue/task. -->

## Acceptance criteria
- [ ] <behavior 1>
- [ ] <edge case / non-goal>

## Risk
Tier: <!-- 0 / 1 / 2, filled by classify_risk.py; reviewers may raise it, never lower it -->
Why: <!-- what this touches that could hurt someone -->

## Evidence
<!-- Commands actually run and the tail of their output. "Tests pass" alone is not evidence. -->
```
$ code_quality/scripts/quality_gate.sh full
...
```
Independent review: <!-- rounds, final summary line from .quality/review.md -->
Unable to verify: <!-- anything you could not check, and why — or "nothing" -->

## Rollout
<!-- Flag name and default, who sees it first, what metric/alert would show it is broken. -->

## How to revert
<!-- Revert this PR? Flip a flag? Data that needs repair? Must be answerable before merge. -->

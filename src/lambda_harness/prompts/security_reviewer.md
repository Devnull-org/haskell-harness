Independently review the project's security and ability to recover from failures.
Start with repository guidance, architecture, saved project context, specifications,
and documented trust assumptions. Identify the exposed interfaces, sensitive data,
and relevant actors before choosing what to inspect.

Trace untrusted input through parsing, authorization, state changes, persistence, and
external effects. Inspect applicable risks: injection, path traversal, unsafe
deserialization, secret exposure, resource exhaustion, partial functions, unchecked
FFI, races, deadlocks, asynchronous exceptions, cancellation, and resource leaks.
For distributed systems, consider stale state, replay, duplicate or reordered
messages, retries, restarts, rollback, and timing. Use domain-specific models and
invariants only when supported by the repository; do not assume a particular domain.

For a stuck-state finding, show the event sequence, required conditions, unavailable
progress/recovery/exit paths, and impact on users or data. Distinguish documented liveness
assumptions, existing defects, and regressions introduced by this patch. Do not label an
expected coordination assumption a new exploit without evidence.

Use SEC- prefixed IDs and calibrated severity. Include exact locations, attacker
capabilities/conditions, sequence, impact, evidence, confidence, and remediation. Suggest
a reproducer or regression test where possible. Set needs_test=true for behavioral
defects. Mark hypotheses suspected. Record reviewed scope and uncertainty in summary;
an empty findings list is not a proof of security. Do not edit or independently publish anything.
Requested GitHub actions are handled by the harness as described in the common rules.

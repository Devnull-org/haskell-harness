Independently review the final revision and complete patch series against the accepted
specification. Examine correctness, acceptance coverage, boundaries, resource handling,
STM/concurrency, exceptions, laziness/strictness, and performance evidence. Assess Haskell
idioms, repository style, comment clarity, and whether each commit is coherent and builds.

Use precise file:line evidence. Avoid speculative complaints and stylistic churn. Mark
uncertain findings suspected and explain missing evidence. Use HASKELL- prefixed IDs.
needs_test=true for a behavioral defect requiring a failing regression test; false for
behavior-preserving readability fixes. Return no findings when none are substantiated.
Record scope and limitations in summary. Do not edit code or independently publish comments.
Requested GitHub actions are handled by the harness as described in the common rules.

Independently review Hydra's on-chain and off-chain security and protocol liveness.
Ground reasoning in the current code, protocol specification, Agda model, and documented
trust assumptions. Trace relevant transitions across validators, transaction construction,
HeadLogic, network messages, persistence, and chain observation.

Inspect applicable eUTxO risks: input/output binding and double satisfaction, datum and
redeemer validation, authentic protocol UTxOs, signer checks, value preservation, mint/burn
constraints, and validity intervals. Examine signatures, conflicting/stale state, replay,
duplicate or reordered messages, malformed transactions, restarts, rollbacks, and timing.
Consider both malicious participants and plausible failures.

For a stuck-state finding, show the event sequence, required conditions, unavailable
progress/recovery/exit paths, and impact on funds. Distinguish documented liveness
assumptions, existing defects, and regressions introduced by this patch. Do not label an
expected participant-cooperation assumption a new exploit without evidence.

Use SEC- prefixed IDs and calibrated severity. Include exact locations, attacker
capabilities/conditions, sequence, impact, evidence, confidence, and remediation. Suggest
a reproducer or regression test where possible. Set needs_test=true for behavioral
defects. Mark hypotheses suspected. Record reviewed scope and uncertainty in summary;
an empty findings list is not a proof of security. Do not edit or publish anything.

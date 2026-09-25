# Audit Tamper Evidence

AuditChain (audit_record contract 1.1.0): every record carries
integrity_hash = H(previous_hash || canonical-record) and previous_hash.
Verification walks the whole chain read-only; any mutation of actor,
timestamp, operation, environment, result, causal reference, previous
hash or payload is detected (parametrized corruption tests).

require_verified() BLOCKS privileged operations when the chain does not
verify (tested: gate refuses governance on a tampered chain).
Historical records remain reproducible: verification is pure and
returns the same verdict for the same chain.

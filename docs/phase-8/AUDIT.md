# Audit Hardening

Every privileged governance action appends an AuditRecord through the
one audit port (platform.audit). Records carry actor (type+id),
operation, entity, environment, timestamp, request correlation,
before/after versions and hashes, reason, software/contract versions
and policy/risk/model version references.

Audit records are frozen dataclasses in append-only stores; correction
means a NEW record, never mutation. Security events (27 new types,
event contract 1.3.0) flow through the existing EventStore - there is
no second event database.

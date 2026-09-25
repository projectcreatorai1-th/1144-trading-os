# Incident Response

SecurityIncident lifecycle (state machine): DETECTED -> OPEN ->
CONTAINED -> INVESTIGATING -> RECOVERING -> CLOSED [HUMAN_APPROVAL].
(Open/contained may close early with human approval.)

Records preserve severity, title, affected environment + resources,
detection time/source, evidence (mandatory), an append-only timeline,
closure time and the human closure approver. Evidence is immutable
across every transition; nothing is ever deleted.

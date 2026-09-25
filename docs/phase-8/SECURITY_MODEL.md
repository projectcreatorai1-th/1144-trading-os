# Security Model

Guard order for every privileged operation (all must pass, all fail
closed):

1. AUTHENTICATION - PBKDF2-verified credential, environment-bound,
   lockout after repeated failures; UNKNOWN never AUTHENTICATED.
2. SESSION - created only from an AUTHENTICATED result; frozen
   permission snapshot; expiry/revocation/unknown all BLOCK.
3. AUTHORIZATION - the canonical RolePermissionRegistry decides
   (permissions.yaml is the single source); environment restrictions
   enforced; LIVE needs strong authentication.
4. APPROVAL - maker-checker with a DIFFERENT human checker for the
   governed operations; AI can never check.
5. GOVERNANCE - versioned artifact + content hashes + immutable
   transition evidence, appended to the tamper-evident audit chain.
6. AUDIT INTEGRITY - a broken chain BLOCKS privileged operations.

Security never converts BLOCK into ALLOW and never evaluates trading
risk (SECTION 3.3: that is core.risk alone).

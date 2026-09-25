# AI Security Boundary

AI remains ADVISORY / PROPOSAL ONLY (Phase 7 rules AI-001..AI-036 still
enforced). Phase 8 adds the double lock:

- AI actor kinds (AI_MODEL, SYSTEM_AUTOMATED) are structurally excluded
  from checker approval (is_human_checker; SEC-006/037/038).
- Model promotion routes through governance (MODEL -> APPROVE +
  maker-checker), so no AI self-promotion and no automatic production
  deployment.
- Forbidden paths (AI -> permission/approval/RiskDecision/order/
  execution/LIVE) each have corruption tests; every attempt fails.

The only path from AI to the market remains: PROPOSAL -> HUMAN/
GOVERNANCE REVIEW -> existing STRATEGY/POLICY/RISK chain.

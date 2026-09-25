# Environment Security

Environments stay explicit (REPLAY/SIMULATION/PAPER/DEMO/LIVE + research
plane). Three independent guards:

1. Credentials are environment-bound (a DEMO credential never
   authenticates LIVE).
2. Sessions are environment-bound (authorization rejects a session used
   outside its environment: ENVIRONMENT_MISMATCH, no fallback).
3. The canonical registry restricts permissions per environment
   (LIVE_TRADE -> [LIVE] etc.; ENVIRONMENT_PERMISSION_MISMATCH).

LIVE additionally requires strong authentication (API keys never
authorize LIVE) and sits behind the approval-required operation set.

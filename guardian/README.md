# Guardian

Reserved for the Guardian team (same structure as the other layers: `README.md`, `INTERFACE.md`, `src/`,
`mocks/` for the Twin and the Brain, `tests/`).

Until the Guardian is connected, the City Brain uses its own Guardian stand-in
(`brain/mocks/guardian_standin.py`, `guardian.mode: mock` in `brain/config/brain.yaml`). It already covers the
Test Book cases the Brain relies on: a spoofed grid reading is marked untrusted and ignored (EN03), malformed
partner messages are refused (SY02).

To connect a real Guardian, follow [INTERFACE.md](INTERFACE.md): answer `POST <guardian>/v1/trust-verdict`, or push
verdicts to `POST http://<brain>:8500/v1/verdicts` with `Authorization: Bearer $BRAIN_TOKEN`, then set
`guardian.mode: http` and `guardian.url` in the Brain config.

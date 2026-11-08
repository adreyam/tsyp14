# Guardian · Interface Card (what the City Brain expects)

> The Guardian team owns this folder. Until it is connected, the City Brain runs its own Guardian stand-in
> (`brain/mocks/guardian_standin.py`). This card describes the contract the Brain already implements, so a
> Guardian can plug in without changing the Brain's code. Full details: [../brain/INTERFACE.md](../brain/INTERFACE.md), section 2.

## What the Guardian receives and sends

| Direction | Message | Transport |
|---|---|---|
| in | an Event (one reading or incident from the Twin, normalised by the Brain) | `POST <guardian>/v1/trust-verdict` with `{"event": Event}` (the Brain asks), or the Guardian reads the Twin's device API itself |
| out | a Verdict on that event or device | the answer to the request above, or a push to `POST http://<brain>:8500/v1/verdicts` with `Authorization: Bearer $BRAIN_TOKEN` |

## Verdict example

```json
{
  "schema": "citybrain.verdict/1",
  "verdict_id": "vd-mw5gw2k1a9f3",
  "trace_id": "tr-mw5gw2jz11c0",
  "ts": "2026-11-06T21:09:12.204Z",
  "source": "live",
  "device_id": "FLOOD-03",
  "event_id": "ev-mw5gw2jz0b7e",
  "status": "untrusted",
  "confidence": 0.94,
  "reason": "FLOOD-03 reads surface_water_depth=410 while every other flood sensor reads about 0 and no rain is falling",
  "valid_for_s": 60,
  "containment": "isolate"
}
```

`status` is one of `trusted`, `suspect`, `untrusted`, `quarantined`, `released`. `reason` is required for
`suspect`, `untrusted` and `quarantined`. `trace_id` is copied from the Event so the timeline links reading →
verdict → decision.

| status | What the Brain does |
|---|---|
| `trusted`, `released` | uses the data |
| `suspect` | uses it, but sends only reversible actions |
| `untrusted` | ignores the event and logs why |
| `quarantined` | ignores the device's data and never commands that device |
| no verdict (Guardian down or slow) | `guardian.on_missing` in the Brain config: `degraded` (default), `ignore` or `trust` |

## Pointing the Brain at the Guardian (configuration, never code)

`brain/config/brain.yaml`:

```yaml
guardian:
  mode: http                     # mock = Brain's stand-in | http = this Guardian | off
  url: http://127.0.0.1:8300
  verdict_path: /v1/trust-verdict
  token_env: GUARDIAN_TOKEN      # the Brain sends "Authorization: Bearer $GUARDIAN_TOKEN"
  timeout_s: 1.5
  on_missing: degraded
```

## Errors

A malformed verdict is refused with HTTP 400 and `{"error": {"code": "invalid", "message": "<field>: <reason>"}}`,
and the refusal is written to the Brain's audit log. The JSON Schema of every message is served at
`GET http://<brain>:8500/v1/schemas`.

## What changed

Describe the focused change and why it is needed.

## Verification

- [ ] `python -m unittest discover -s tests -v`
- [ ] `git diff --check`
- [ ] Patch scripts remain idempotent
- [ ] No pairing files, private keys, Apple credentials, signed personal IPAs, unnecessary device identifiers, or private logs are included

### CI evidence

Describe what was verified by build/tests.

### Real-device evidence

Device / iOS version and what was actually verified on-device, if applicable.

## Proof level

For background-refresh changes, distinguish between:

- registration (`REGISTER_PASS`)
- accepted scheduling (`SCHEDULE_PASS`)
- actual scheduled execution (`TRIGGER`)
- completed refresh (`COMPLETE success=true`)

Do not describe scheduled PC-free refresh as proven unless the criteria in `docs/VERIFICATION.md` are satisfied.

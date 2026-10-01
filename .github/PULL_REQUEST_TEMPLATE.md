## Summary

Describe what changed and why.

## Verification

- [ ] `python -m compileall -q app main.py config.py`
- [ ] `python -m pytest -q`
- [ ] Relevant focused checks have been run

## Checklist

- [ ] The change is focused and follows the existing architecture and style.
- [ ] Tests and documentation were updated when behavior changed.
- [ ] No secrets, local databases, logs, browser profiles, or runtime files are included.
- [ ] Backward-compatibility impact is documented.

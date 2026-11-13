## Summary

What changed, and why?

## Verification

- [ ] `python -m pytest tests -q -p no:cacheprovider`
- [ ] `python -m ruff check machina tests tools integrations`
- [ ] `python -m compileall -q machina tests tools integrations`
- [ ] `git diff --check`
- [ ] Relevant Machina-DevTools tests or terminal UI checks

Commands and pass/fail counts:

```text
```

## Risk and rollback

What can break, and how should it be reverted?

## Checklist

- [ ] Tests cover the behavior change.
- [ ] No secrets, generated caches, or machine-local files are included.
- [ ] No benchmark-specific prompts, task IDs, or hidden-test shortcuts were added.
- [ ] Recovery paths and approval behavior are unchanged or explicitly documented.

# Machina 1.1.1

Model picker cleanup and Grok subscription variants.

## Fixed

- The curated model picker now has one current row per model family instead of legacy duplicates (for example, one GPT row per provider section and one current DeepSeek row).
- Grok subscriptions expose Grok 4.7, 4.7 Build Fast, 4.6, 4.5, 4.3, and Grok Build 0.1. The `xai` API-key provider remains separate from the `grok` subscription provider.
- Context-window metadata updated for Grok 4.3 / Grok Build and the current model families.

## Grok login

```bash
machina auth grok login
machina auth grok status
machina chat "hello" --provider grok --model grok-4.7
```

xAI may restrict OAuth inference by subscription tier. If login succeeds but
inference returns 403, use `XAI_API_KEY` with the `xai` provider instead.

## Validation

Backend and UI regression suites, Docker build/smoke, and GitHub Actions remain
the release gates. This is a self-hosted open-source release with no hosted
service or production SLA.

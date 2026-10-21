# Machina 1.1.0

Model-catalog refresh and Grok subscription login.

## New

- Grok subscription OAuth via xAI's device flow:
  ```bash
  machina auth grok login
  machina auth grok status
  machina chat "hello" --provider grok --model grok-4.7
  ```
  OAuth credentials are stored in `~/.machina/auth/grok.json` with `0600`
  permissions. The `grok` provider uses xAI's CLI chat proxy; an OAuth token is
  not accepted by the ordinary `xai` API-key endpoint.
- Current curated fallback models for OpenAI, Anthropic, Google, DeepSeek,
  xAI, Groq, Together, Mistral, and OpenRouter-facing workflows.
- Updated context-window metadata for GPT-6.x, Claude 5.x, Gemini 4,
  Grok 4.7, DeepSeek V4, and Mistral Large 4.

## Verified

- 6,504 backend tests passed, 2 skipped.
- 1,165 UI tests passed, 0 skipped.
- GitHub Actions backend, UI, and Docker jobs passed.
- Wheel/sdist isolated imports and bundled resource hashes passed.
- Docker image/CLI smoke test passed.

## Notes

xAI may restrict OAuth API access to specific SuperGrok / X Premium+ tiers.
If login succeeds but inference returns 403, use `XAI_API_KEY` with the `xai`
provider instead. This remains a self-hosted open-source release with no SLA or
hosted service.

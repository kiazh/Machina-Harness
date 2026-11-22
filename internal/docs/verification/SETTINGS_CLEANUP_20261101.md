# Settings and layout cleanup

The production desktop settings window now has searchable navigation for
Appearance, Workspace, Models, Providers, API keys, Accounts and Connectors.
Connected engine controls are reused inside the settings window. Workspace
checkboxes control actual sidebar/editor/terminal visibility without destroying
editor buffers or composer drafts. The small-screen sidebar toggle yields space
from the editor when both would crowd the conversation.

Conversation actions now sit beside the session title. Editor buttons and command
controls use less horizontal space. Models/settings pages use the main content
area of the settings window rather than adding persistent controls to chat.

Validation: TypeScript and 40 unit/integration tests pass; all nine browser tests
pass. The new settings test loads the gateway-backed model catalog, switches to
Workspace settings, opens the actual editor with the checkbox and verifies the
unsent draft survives closing the dialog. The production frontend and Windows
native release build succeed. macOS native validation remains pending.

Screenshot: `20261101-settings-workspace.png` (synthetic browser fixture).

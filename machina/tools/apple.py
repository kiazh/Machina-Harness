"""Native Apple Mail integration (macOS)."""

from __future__ import annotations

import shutil
import subprocess

from machina.core.exceptions import ToolError
from machina.tools.base import registry

_JXA_MAIL_DRAFT = r"""
function run(argv) {
  const Mail = Application('Mail');
  Mail.includeStandardAdditions = true;
  const message = Mail.OutgoingMessage({
    subject: argv[1],
    content: argv[2],
    visible: true,
  });
  Mail.outgoingMessages.push(message);
  message.toRecipients.push(Mail.ToRecipient({ address: argv[0] }));
  return message.id();
}
"""


@registry.register(
    name="apple_mail_draft",
    description=(
        "Create a visible draft in Apple Mail. This never sends; the user "
        "reviews and sends it. macOS only."
    ),
    parameters={
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "Recipient email address"},
            "subject": {"type": "string"},
            "body": {"type": "string"},
        },
        "required": ["to", "subject", "body"],
    },
    effects=("exec",),
)
async def apple_mail_draft(to: str, subject: str, body: str) -> str:
    recipient = to.strip()
    if "@" not in recipient or len(recipient) > 320:
        raise ToolError("to must be a valid email address")
    if not shutil.which("osascript"):
        raise ToolError("Apple Mail automation requires macOS osascript")
    command = ["osascript", "-l", "JavaScript", "-e", _JXA_MAIL_DRAFT, "--", recipient, subject, body]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ToolError(f"Apple Mail draft failed: {type(error).__name__}") from None
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        raise ToolError(f"Apple Mail draft failed: {detail[-1] if detail else 'osascript error'}")
    identifier = (result.stdout or "").strip()
    return f"Apple Mail draft created{f' (id {identifier})' if identifier else ''}; review it before sending."

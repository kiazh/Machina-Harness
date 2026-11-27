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


_JXA_CALENDAR_AGENDA = r"""
function run(argv) {
  const Calendar = Application('Calendar');
  const start = new Date(argv[0]);
  const end = new Date(argv[1]);
  if (isNaN(start.getTime()) || isNaN(end.getTime())) {
    throw new Error('invalid date range');
  }
  const out = [];
  const calendars = Calendar.calendars();
  for (let index = 0; index < calendars.length; index++) {
    const calendar = calendars[index];
    let events;
    try { events = calendar.events(); } catch (error) { continue; }
    for (let row = 0; row < events.length; row++) {
      const event = events[row];
      let begins;
      try { begins = event.startDate(); } catch (error) { continue; }
      if (begins >= start && begins <= end) {
        out.push(begins.toISOString() + '  ' + event.summary() + '  ' + calendar.name());
      }
    }
  }
  out.sort();
  return out.length ? out.join('\n') : 'No events in range.';
}
"""


@registry.register(
    name="apple_calendar_agenda",
    description=(
        "Read Apple Calendar events in an ISO date range. Read-only; use the "
        "Calendar app or a separate approved action to create or edit events."
    ),
    parameters={
        "type": "object",
        "properties": {
            "start": {"type": "string", "description": "ISO start, e.g. 2026-11-27T00:00:00"},
            "end": {"type": "string", "description": "ISO end, e.g. 2026-11-28T00:00:00"},
        },
        "required": ["start", "end"],
    },
    effects=("exec",),
)
async def apple_calendar_agenda(start: str, end: str) -> str:
    if not shutil.which("osascript"):
        raise ToolError("Apple Calendar automation requires macOS osascript")
    command = [
        "osascript",
        "-l",
        "JavaScript",
        "-e",
        _JXA_CALENDAR_AGENDA,
        "--",
        start.strip(),
        end.strip(),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=45)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ToolError(f"Apple Calendar agenda failed: {type(error).__name__}") from None
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        raise ToolError(f"Apple Calendar agenda failed: {detail[-1] if detail else 'osascript error'}")
    return (result.stdout or "").strip() or "No events in range."

"""web_extract is for public HTTP(S), not local filesystem paths."""

from __future__ import annotations

import pytest

from machina.core.exceptions import ValidationError
from machina.tools.builtins import web_extract


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["src/app.py", "C:/repo/app.py", "/tmp/app.py"])
async def test_local_paths_are_rejected_with_a_file_tool_hint(path: str) -> None:
    with pytest.raises(ValidationError, match="read_file"):
        await web_extract(path)

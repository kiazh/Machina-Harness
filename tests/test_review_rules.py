"""Detection and precision checks for the deterministic review rules."""

from __future__ import annotations

import pytest

from machina.workspace import reviews


def _review(lines: list[str], path: str = "src/app.py") -> list[dict[str, object]]:
    diff = (
        f"--- a/{path}\n+++ b/{path}\n@@ -0,0 +1,{len(lines)} @@\n"
        + "\n".join(f"+{line}" for line in lines)
    )
    return reviews.findings_from_files([{"path": path, "binary": False, "text": diff}])


def _rules(lines: list[str], path: str = "src/app.py") -> set[str]:
    return {str(finding["rule"]) for finding in _review(lines, path)}


@pytest.mark.parametrize(
    ("line", "rule"),
    [
        ('AWS_KEY = "AKIAABCDEFGHIJKLMNOP"', "hardcoded-secret"),
        ('password = "hunter2hunter2"', "hardcoded-secret"),
        ("subprocess.run(cmd, shell=True)", "shell-true"),
        ("requests.get(url, verify=False)", "tls-verify-disabled"),
        ("os.chmod(path, 0o777)", "world-writable"),
        ("result = eval(user_input)", "dynamic-execution"),
        ("data = pickle.loads(payload)", "unsafe-deserialization"),
        ("doc = yaml.load(raw, Loader=yaml.Loader)", "unsafe-yaml-load"),
        ('jwt.decode(token, options={"verify_signature": False})', "jwt-no-verify"),
        ('cursor.execute(f"SELECT * FROM users WHERE id={uid}")', "sql-injection"),
        ('url = "https://user:pass@example.com/db"', "url-credentials"),
        ("except BaseException:", "broad-except"),
        ("except Exception:", "broad-except"),
        ("except:", "bare-except"),
        ("# TODO: fix this later", "unfinished-marker"),
    ],
)
def test_rules_catch_their_hazard(line: str, rule: str) -> None:
    assert rule in _rules([line])


@pytest.mark.parametrize(
    "line",
    [
        "# todos los usuarios",
        'name = "hackathon"',
        "// console.log(debug)",
        "# never use verify=False",
        "# do not eval(user_input)",
        "doc = yaml.load(raw, Loader=yaml.SafeLoader)",
        "try:\n    work()\nexcept ValueError as error:\n    raise RuntimeError() from error",
    ],
)
def test_clean_lines_are_not_flagged(line: str) -> None:
    assert _rules(line.splitlines()) == set()


def test_debug_prints_skip_test_paths() -> None:
    assert "debug-print" not in _rules(["print(debug)"], "tests/test_app.py")
    assert "debug-print" in _rules(["print(debug)"], "src/app.py")


def test_added_line_numbers_follow_hunks() -> None:
    diff = (
        "--- a/src/a.py\n+++ b/src/a.py\n"
        "@@ -10,2 +10,3 @@\n"
        " context\n"
        "+shell = True\n"
        " context\n"
    )
    findings = reviews.findings_from_files([{"path": "src/a.py", "binary": False, "text": diff}])
    assert findings[0]["line"] == 11
    assert findings[0]["file"] == "src/a.py"


def test_fingerprints_are_stable_and_deduplicate() -> None:
    first = _review(["shell = True"])
    second = _review(["shell = True"])
    assert first[0]["fingerprint"] == second[0]["fingerprint"]
    duplicate = reviews.findings_from_files(
        [
            {
                "path": "src/app.py",
                "binary": False,
                "text": "--- a/src/app.py\n+++ b/src/app.py\n@@ -0,0 +1,1 @@\n+shell = True\n",
            },
            {
                "path": "src/app.py",
                "binary": False,
                "text": "--- a/src/app.py\n+++ b/src/app.py\n@@ -0,0 +1,1 @@\n+shell = True\n",
            },
        ]
    )
    assert len(duplicate) == 1

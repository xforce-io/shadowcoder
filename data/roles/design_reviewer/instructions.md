You are reviewing a DESIGN DOCUMENT, not code.
Evaluate the design for: completeness, architectural soundness,
interface clarity, error handling strategy, and testability.
Do NOT check whether source files or code exist — implementation
happens in a later phase. Focus only on the design quality.

CRITICAL review items — flag as HIGH if missing:
- Goal & acceptance criteria: the design MUST define the desired end state
  and list concrete, testable acceptance criteria BEFORE the solution design.
  Vague goals like "improve performance" or "fix the bug" are not acceptable.
- Test strategy with an exact test command (e.g. "make -C module test", "go test ./...")
- A yaml metadata block at the end with test_command field
- For bugfix: root cause analysis must be present. A fix without understanding
  the cause is a guess, not engineering.

Focus on: logic correctness, design quality, potential issues that tests don't catch.

For each issue found, classify its severity:
- critical: breaks core functionality, security vulnerability, data corruption
- high: missing required feature, significant logic bug
- medium: code quality, minor missing feature, style
- low: naming, minor improvement

Also check if previously unresolved feedback items are now addressed.
Propose 1-3 new test cases if you find issues worth testing.

Output ONLY JSON:
{
    "comments": [{"severity": "...", "message": "...", "location": "..."}],
    "resolved_item_ids": ["F1", "F3"],
    "proposed_tests": [{"name": "test_name", "description": "what to test", "expected_behavior": "expected result"}]
}

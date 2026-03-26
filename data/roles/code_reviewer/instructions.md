You are reviewing a code change. The git diff is provided below.
The code has already passed build and all tests via the gate.
If gate failure output is provided, analyze why tests are failing.
Focus on: logic correctness, design quality, potential issues that tests don't catch.
Do NOT check whether source files exist — that is the gate's job.

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

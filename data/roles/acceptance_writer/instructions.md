Write an executable shell script that verifies the acceptance criteria
from the design document. This script will be run BEFORE any code changes
to confirm the problem exists (or the feature is missing), and AFTER
development to confirm the fix/feature works.

Rules:

1. Output ONLY a bash script. No explanation, no markdown, no commentary.
   Start with #!/bin/bash and set -euo pipefail.

2. Each acceptance criterion becomes one or more shell assertions.
   Use simple commands: test, grep, diff, curl, echo + pipe, etc.
   Do NOT depend on any test framework (pytest, jest, go test, etc.).

3. The script runs with cwd set to the project worktree root.
   Use relative paths. Do not hardcode absolute paths.

4. Every assertion MUST be meaningful — it must test a specific,
   observable behavior that will change when the fix/feature is implemented.
   Do NOT write trivially-failing assertions (e.g. `test 1 = 0`).
   Do NOT write assertions that only check file existence without
   verifying behavior.

5. For bugfix: your assertions must reproduce the broken behavior.
   The script should FAIL because the bug exists.
   After the fix, the script should PASS because the bug is gone.

6. For new features: your assertions must exercise the new capability.
   The script should FAIL because the feature doesn't exist yet.
   After implementation, the script should PASS.

7. If the system tells you your script already PASSES on unchanged code,
   you MUST analyze why. Common causes:
   - Assertion too loose (e.g. checking exit code 0 when the real issue is wrong output)
   - Testing the wrong thing (e.g. checking a function exists instead of its behavior)
   - Default/fallback behavior accidentally satisfies the check
   Fix the root cause. Do not just rephrase the same weak assertion.

8. Keep the script short (under 50 lines). Each line should be obvious
   in what it tests and why.

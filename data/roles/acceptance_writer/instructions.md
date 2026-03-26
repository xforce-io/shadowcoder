Write an executable shell script that verifies the acceptance criteria
from the design document. This script will be run BEFORE any code changes
to confirm the problem exists (or the feature is missing), and AFTER
development to confirm the fix/feature works.

Rules:

1. Output ONLY a bash script. No explanation, no markdown, no commentary.
   Start with #!/bin/bash and set -euo pipefail.

2. Your script is SELF-CONTAINED. It verifies behavior directly by
   running the program/library and checking outputs. Examples:

   Good — directly tests behavior:
     result=$(python3 -c "from mylib import add; print(add(2,3))")
     test "$result" = "5"

   Good — tests a CLI tool:
     output=$(echo "2 + 3" | python3 calc.py 2>&1)
     test "$output" = "5"

   Good — tests a numerical result with tolerance:
     python3 -c "
     from network import NeuralNetwork
     nn = NeuralNetwork([784, 32, 10])
     # ... train on small subset ...
     assert accuracy > 0.99, f'Expected >99%, got {accuracy}'
     "

   BAD — delegates to test framework:
     pytest tests/test_foo.py::test_bar        # FORBIDDEN
     go test ./... -run TestFoo                 # FORBIDDEN
     npm test                                   # FORBIDDEN

   BAD — only checks structure, not behavior:
     test -f src/main.py                        # Proves nothing
     grep "def forward" network.py              # Existence ≠ correctness

3. NEVER call pytest, go test, npm test, cargo test, or any test runner.
   You are the test. You run the code yourself and check the results.
   The project's test suite is someone else's responsibility.

4. NEVER check file/function existence as a primary assertion.
   Files and functions can exist but be completely wrong.
   Always verify observable behavior: outputs, return values, exit codes.

5. For libraries/modules: write inline Python (or appropriate language)
   that imports the module, calls functions, and asserts results.
   Use python3 -c "..." for short checks, or a heredoc for longer ones.

6. For CLI tools: pipe input, capture output, compare against expected.

7. The script runs with cwd set to the project worktree root.
   Use relative paths. Do not hardcode absolute paths.

8. Every assertion MUST test a specific, observable behavior that will
   change when the fix/feature is implemented.

9. For bugfix: reproduce the broken behavior. FAIL because the bug exists.
   For new features: exercise the new capability. FAIL because it's missing.

10. If the system tells you your script already PASSES on unchanged code,
    analyze WHY — your assertions are too weak. Strengthen them.

11. Keep the script short (under 50 lines). Fewer strong assertions
    beat many weak ones.

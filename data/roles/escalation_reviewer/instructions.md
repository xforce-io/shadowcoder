The developer has failed to pass the acceptance script multiple times with the same error.
Your job is to determine the root cause: is the bug in the CODE or in the ACCEPTANCE SCRIPT?

You are provided:
- The requirements (需求)
- The acceptance script
- The failure output
- The code diff

## Your Task

1. Read the failure output to understand WHAT failed.
2. Read the acceptance script to understand WHAT it asserts.
3. Read the requirements to understand WHAT the correct behavior should be.
4. Compare: does the acceptance script's assertion match the requirements?
   - If the script asserts something that contradicts the requirements or basic language semantics, the SCRIPT is wrong.
   - If the script correctly tests the requirements but the code doesn't implement them, the CODE is wrong.

## Rules

- You MUST give a verdict. Do not say "it could be either."
- If the same failure repeats across multiple rounds and the developer cannot fix it, strongly consider that the acceptance script may be wrong.
- Do NOT evaluate code quality, style, or design. Only evaluate correctness relative to the requirements.

## Output

If the ACCEPTANCE SCRIPT is wrong, prefix your comment with `[TARGET:acceptance_script]`:

```json
{
    "comments": [{"severity": "high", "message": "[TARGET:acceptance_script] The assertion ... is incorrect because ...", "location": "acceptance.sh"}],
    "resolved_item_ids": [],
    "proposed_tests": []
}
```

If the CODE is wrong, describe the bug:

```json
{
    "comments": [{"severity": "high", "message": "The code fails because ...", "location": "file.py:42"}],
    "resolved_item_ids": [],
    "proposed_tests": []
}
```

Output ONLY JSON.

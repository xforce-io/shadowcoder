Quickly assess the feasibility of this project. Do NOT produce a full design.

CRITICAL: You are running inside the TARGET CODEBASE. Before assessing feasibility,
search the codebase for the key modules, components, classes, or files mentioned in
the requirements. Set `codebase_match` based on what you find:
- `true`: the core modules/components that need to be MODIFIED are present in this codebase
- `false`: the requirements reference specific features or subsystems (e.g. "Decision Agent",
  "explore mode", "payment service") whose implementation code is NOT in this codebase

Note: being able to write TESTS for a bug is not enough. `codebase_match` should be `false`
if the actual code that needs to be FIXED or CHANGED lives in a different repository.

Output ONLY JSON:
{
    "feasibility": "high" | "medium" | "low",
    "estimated_complexity": "simple" | "moderate" | "complex" | "very_complex",
    "risks": ["risk 1", "risk 2", ...],
    "codebase_match": true | false,
    "tech_stack_recommendation": "optional suggestion"
}

Assessment criteria:
- feasibility: can this realistically be built with the specified tech stack?
- codebase_match: are the core modules that need modification present in this codebase?
- complexity: how many subsystems, how much integration work?
- risks: what could go wrong or take much longer than expected?

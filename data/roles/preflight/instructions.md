Quickly assess the feasibility of this project. Do NOT produce a full design.

Output ONLY JSON:
{
    "feasibility": "high" | "medium" | "low",
    "estimated_complexity": "simple" | "moderate" | "complex" | "very_complex",
    "risks": ["risk 1", "risk 2", ...],
    "tech_stack_recommendation": "optional suggestion"
}

Assessment criteria:
- feasibility: can this realistically be built with the specified tech stack?
- complexity: how many subsystems, how much integration work?
- risks: what could go wrong or take much longer than expected?

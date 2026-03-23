# Example 01: Gomoku AI

A Gomoku (Five-in-a-Row) AI engine that iteratively improves until it beats a baseline opponent with >90% win rate.

## What This Demonstrates

- **Iterative optimization**: AI win rate improves across develop rounds as reviewer identifies weaknesses in pattern recognition and evaluation
- **Benchmark as acceptance test**: Win rate checked via `cargo test` exit code — same mechanism as any other test
- **Gate verification**: Each develop round must pass all tests before review
- **Measurable progress**: Win rate is the training curve

## Run

```bash
# Create a new repo for the project
mkdir gomoku && cd gomoku && git init && git commit --allow-empty -m "init"

# Run with ShadowCoder
cd /path/to/shadowcoder
python scripts/run_real.py /path/to/gomoku run "Gomoku AI" --from examples/01-gomoku-ai/requirements.md
```

## Expected Flow

```
Preflight: feasibility=high, complexity=moderate

Design R1 → reviewer proposes benchmark test specs
Design R2 → approved (conditional)

Develop R1 → gate: tests fail (win rate 72%) → developer self-fixes
Develop R2 → gate: tests fail (win rate 85%) → reviewer analyzes: "open-three detection missing"
Develop R3 → gate: tests pass (win rate 93%) → review: pass → DONE
```

The exact number of rounds depends on how quickly the AI's evaluation function converges. Rust implementation ensures the 100-game benchmark runs in seconds, not hours.

## Key Files (expected output)

```
src/
  board.rs          # Board representation, move validation, win detection
  patterns.rs       # Pattern recognition (open-four, half-four, etc.)
  evaluator.rs      # Position evaluation based on pattern counts
  search.rs         # Minimax + Alpha-Beta pruning
  ai.rs             # Top-level AI interface
  baseline.rs       # Baseline opponent (fixed)
  server.rs         # HTTP API
  main.rs           # Entry point

tests/
  test_functional.rs    # Legal moves, win detection, defense, attack, tactics
  test_vs_baseline.rs   # 100-game benchmark (win rate assertions)
  test_performance.rs   # Timing assertions

static/
  index.html        # Web interface
```

## Goal

Build a Gomoku (Five-in-a-Row) AI engine with web interface. The AI should beat a baseline opponent with >90% win rate. Implemented in Rust for performance.

## Tech Stack

- Rust (stable toolchain, Cargo)
- Single-page HTML/JS frontend (communicates with Rust backend via HTTP)
- Actix-web or Axum for HTTP server

## Baseline AI (fixed, do not modify)

The baseline plays by randomly picking an empty cell adjacent to existing stones. It is provided as a Rust function:

```rust
// baseline.rs — DO NOT MODIFY
use rand::Rng;

pub fn baseline_move(board: &[[u8; 15]; 15], player: u8) -> (usize, usize) {
    let mut neighbors = Vec::new();
    for r in 0..15 {
        for c in 0..15 {
            if board[r][c] != 0 {
                for dr in -2i32..=2 {
                    for dc in -2i32..=2 {
                        let nr = r as i32 + dr;
                        let nc = c as i32 + dc;
                        if nr >= 0 && nr < 15 && nc >= 0 && nc < 15 {
                            let (nr, nc) = (nr as usize, nc as usize);
                            if board[nr][nc] == 0 {
                                neighbors.push((nr, nc));
                            }
                        }
                    }
                }
            }
        }
    }
    if neighbors.is_empty() {
        return (7, 7);
    }
    neighbors.sort();
    neighbors.dedup();
    let mut rng = rand::thread_rng();
    neighbors[rng.gen_range(0..neighbors.len())]
}
```

## AI Requirements

### Pattern Recognition
- Five-in-a-row (win)
- Open four (both ends open)
- Half-open four (one end blocked)
- Open three
- Half-open three
- Open two

### Search
- Minimax with Alpha-Beta pruning
- Configurable search depth (default 4)
- Move ordering: prioritize high-threat positions
- Only search positions adjacent to existing stones (radius 2)

### Evaluation Function
- Based on pattern counts
- Evaluate both attack (own patterns) and defense (opponent patterns)
- Reference weights: five=100000, open_four=10000, half_four=1000, open_three=1000, half_three=100, open_two=10

## Web Interface

- 15x15 board, black and white stones
- Click to place stone, AI responds automatically
- Show thinking time and search node count
- Show current position evaluation score
- Game over detection and display

## Acceptance Criteria

### Functional
1. AI never plays an illegal move (outside board or occupied cell)
2. Correct five-in-a-row detection in all 4 directions (horizontal, vertical, both diagonals)
3. Defense test: given 10 board positions where opponent is about to win, AI blocks 100%
4. Attack test: given 10 board positions where AI can win, AI takes the winning move 100%
5. Tactical test: given 5 positions where a double-open-three is possible, AI finds the optimal move

### Win Rate
6. AI (depth=4) vs baseline: 100 games (50 as black, 50 as white), AI win rate > 90%
7. AI (depth=2) vs baseline: 100 games, AI win rate > 70%
8. AI (depth=4) vs AI (depth=2): 50 games, depth=4 win rate > 60%

### Performance
9. AI (depth=4) single move < 2 seconds (mid-game, ~30 stones on board)
10. AI (depth=6) single move < 15 seconds

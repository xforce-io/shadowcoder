# Example 02: Neural Network Tuning

A neural network built from scratch with NumPy that iteratively improves until MNIST test accuracy exceeds 97%.

## What This Demonstrates

- **Quantitative gate**: Test accuracy checked via `pytest` assertions — gate fails if accuracy < threshold
- **Iterative improvement**: Each develop round can tune architecture, learning rate, initialization
- **Numerical correctness**: Gradient checks catch backprop bugs before they waste training time
- **No ML framework**: Everything from scratch — forward pass, backprop, weight updates

## Run

```bash
# Create a new repo for the project
mkdir nn-tuning && cd nn-tuning && git init && git commit --allow-empty -m "init"

# Run with ShadowCoder
cd /path/to/shadowcoder
python scripts/run_real.py /path/to/nn-tuning run "Neural Net MNIST" --from examples/02-neural-net-tuning/requirements.md
```

## Expected Flow

```
Preflight: feasibility=high, complexity=moderate

Design R1 → reviewer checks: gradient stability, numerical tricks, test strategy
Design R2 → approved

Develop R1 → gate: gradient check passes, but accuracy 94.2% (need >97%) → retry
Develop R2 → gate: accuracy 97.3% after tuning LR schedule + He init → review: pass → DONE
```

## Key Files (expected output)

```
nn/
  layers.py         # Dense layer: forward, backward, weight update
  activations.py    # ReLU, softmax (numerically stable)
  loss.py           # Cross-entropy loss + gradient
  network.py        # Sequential network: forward, backward, train, predict
  optimizers.py     # SGD with momentum, LR scheduler

data.py             # MNIST download + preprocessing
train.py            # Training script with configurable hyperparameters

tests/
  test_gradient.py      # Numerical vs analytical gradient check
  test_overfit.py       # Overfit small subset (confirms learning)
  test_shapes.py        # Shape consistency through forward/backward
  test_accuracy.py      # MNIST test accuracy thresholds
  test_performance.py   # Timing assertions
```

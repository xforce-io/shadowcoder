## Goal

Build a neural network from scratch using only NumPy that classifies handwritten digits (0-9). Iteratively tune architecture and hyperparameters until test accuracy exceeds 97%.

## Tech Stack

- Python 3.10+
- NumPy only (no PyTorch, TensorFlow, scikit-learn, or other ML frameworks)
- pytest for testing

## Dataset

MNIST handwritten digits. Download and cache locally on first run:

```python
# data.py — download helper
import gzip
import struct
import urllib.request
from pathlib import Path

URLS = {
    "train_images": "http://yann.lecun.com/exdb/mnist/train-images-idx3-ubyte.gz",
    "train_labels": "http://yann.lecun.com/exdb/mnist/train-labels-idx1-ubyte.gz",
    "test_images": "http://yann.lecun.com/exdb/mnist/t10k-images-idx3-ubyte.gz",
    "test_labels": "http://yann.lecun.com/exdb/mnist/t10k-labels-idx1-ubyte.gz",
}

def load_mnist(cache_dir=".data"):
    """Returns (train_images, train_labels, test_images, test_labels) as numpy arrays.
    Images: float32, shape (N, 784), normalized to [0, 1].
    Labels: int, shape (N,).
    """
    ...
```

## Neural Network Requirements

### Architecture

- Fully connected (MLP) — no convolutions needed
- Configurable layer sizes (e.g. `[784, 256, 128, 10]`)
- ReLU activation for hidden layers, softmax for output
- Cross-entropy loss
- He initialization for weights

### Training

- Mini-batch SGD with configurable batch size
- Learning rate scheduler: start high, decay over epochs
- Optional momentum
- Training/validation split: 55000/5000 from the 60000 training samples

### Implementation Constraints

- All forward pass, backward pass, weight updates implemented from scratch in NumPy
- No autograd, no ML library calls
- Must be numerically stable (log-sum-exp trick for softmax, clip gradients)

## Acceptance Criteria

### Correctness

1. **Gradient check**: Numerical gradient vs analytical gradient relative error < 1e-5 for a small network `[784, 32, 10]` on a batch of 8 samples
2. **Overfitting test**: Network `[784, 128, 10]` reaches >99% train accuracy on a 100-sample subset within 200 epochs (confirms learning works)
3. **Shape test**: All layer outputs, gradients, and weight matrices have correct shapes throughout forward and backward pass
4. **Determinism**: Same random seed produces identical results across runs

### Accuracy (on MNIST test set, 10000 samples)

5. Network `[784, 256, 128, 10]`, trained for 30 epochs: test accuracy > 97%
6. Network `[784, 128, 10]`, trained for 20 epochs: test accuracy > 96%
7. Network `[784, 64, 10]`, trained for 20 epochs: test accuracy > 95%

### Performance

8. Training `[784, 256, 128, 10]` for 1 epoch (batch size 64): < 30 seconds on CPU
9. Single forward pass on 10000 test samples: < 1 second

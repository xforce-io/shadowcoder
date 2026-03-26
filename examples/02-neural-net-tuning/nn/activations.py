import numpy as np


class ReLU:
    """ReLU activation: forward caches input, backward returns grad * (x > 0)."""

    def __init__(self):
        self._input = None

    def forward(self, x):
        self._input = x
        return np.maximum(0, x)

    def backward(self, grad_output):
        return grad_output * (self._input > 0)


class Softmax:
    """Softmax activation using log-sum-exp trick for numerical stability.

    NOTE: backward() is a pass-through (returns grad unchanged). This is intentional.
    The combined softmax+cross-entropy gradient is computed by CrossEntropyLoss.backward(),
    which returns (probs - one_hot) / batch_size — a gradient that already accounts for
    the full softmax Jacobian. Therefore Softmax.backward() simply forwards the gradient
    to the preceding layer without modification.
    """

    def __init__(self):
        self._output = None

    def forward(self, x):
        # Log-sum-exp trick: subtract max for numerical stability
        shifted = x - x.max(axis=1, keepdims=True)
        exp_x = np.exp(shifted)
        self._output = exp_x / exp_x.sum(axis=1, keepdims=True)
        return self._output

    def backward(self, grad_output):
        # Pass-through: the combined softmax+CE gradient is handled by CrossEntropyLoss
        return grad_output

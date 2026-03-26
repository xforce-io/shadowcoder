import numpy as np


class Linear:
    """Fully-connected linear layer: out = x @ weights + bias

    Weights are He-initialized: N(0, sqrt(2/fan_in))
    Bias is zero-initialized.
    """

    def __init__(self, in_features: int, out_features: int):
        # He initialization
        std = np.sqrt(2.0 / in_features)
        self.weights = np.random.randn(in_features, out_features).astype(np.float32) * std
        self.bias = np.zeros((1, out_features), dtype=np.float32)

        # Gradient buffers (set after backward())
        self.grad_w = None
        self.grad_b = None

        # Cache for backward pass
        self._input = None

    def forward(self, x):
        """Forward pass: cache input, return x @ W + b."""
        self._input = x
        return x @ self.weights + self.bias

    def backward(self, grad_output):
        """Backward pass: compute gradients and return dx.

        Args:
            grad_output: (batch_size, out_features) gradient from next layer

        Returns:
            (batch_size, in_features) gradient w.r.t. input
        """
        self.grad_w = self._input.T @ grad_output
        self.grad_b = grad_output.sum(axis=0, keepdims=True)
        return grad_output @ self.weights.T

# tests/test_layers.py
import numpy as np
import pytest
from nn.layers import Linear


class TestLinear:
    def test_forward_shape(self):
        layer = Linear(784, 128)
        x = np.random.randn(32, 784).astype(np.float32)
        out = layer.forward(x)
        assert out.shape == (32, 128)

    def test_backward_shape(self):
        layer = Linear(784, 128)
        x = np.random.randn(32, 784).astype(np.float32)
        layer.forward(x)
        grad_out = np.random.randn(32, 128).astype(np.float32)
        dx = layer.backward(grad_out)
        assert dx.shape == (32, 784)
        assert layer.grad_w.shape == (784, 128)
        assert layer.grad_b.shape == (1, 128)

    def test_he_initialization(self):
        """He init: weights should have std ≈ sqrt(2/fan_in)."""
        np.random.seed(42)
        layer = Linear(1000, 500)
        expected_std = np.sqrt(2.0 / 1000)
        actual_std = layer.weights.std()
        assert abs(actual_std - expected_std) < 0.05

    def test_forward_computation(self):
        layer = Linear(3, 2)
        layer.weights = np.array([[1, 2], [3, 4], [5, 6]], dtype=np.float32)
        layer.bias = np.array([[0.1, 0.2]], dtype=np.float32)
        x = np.array([[1, 0, 0]], dtype=np.float32)
        out = layer.forward(x)
        np.testing.assert_allclose(out, [[1.1, 2.2]], atol=1e-6)

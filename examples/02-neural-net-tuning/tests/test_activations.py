# tests/test_activations.py
import numpy as np
import pytest
from nn.activations import ReLU, Softmax


class TestReLU:
    def test_forward_shape(self):
        relu = ReLU()
        x = np.random.randn(4, 10).astype(np.float32)
        out = relu.forward(x)
        assert out.shape == (4, 10)

    def test_forward_values(self):
        relu = ReLU()
        x = np.array([[-1.0, 2.0, 0.0, -3.0]])
        out = relu.forward(x)
        np.testing.assert_array_equal(out, [[0.0, 2.0, 0.0, 0.0]])

    def test_backward_values(self):
        relu = ReLU()
        x = np.array([[-1.0, 2.0, 0.0, -3.0]])
        relu.forward(x)
        grad = np.ones_like(x)
        dx = relu.backward(grad)
        np.testing.assert_array_equal(dx, [[0.0, 1.0, 0.0, 0.0]])


class TestSoftmax:
    def test_forward_sums_to_one(self):
        sm = Softmax()
        x = np.random.randn(4, 10).astype(np.float32)
        out = sm.forward(x)
        np.testing.assert_allclose(out.sum(axis=1), np.ones(4), atol=1e-6)

    def test_forward_shape(self):
        sm = Softmax()
        x = np.random.randn(4, 10).astype(np.float32)
        out = sm.forward(x)
        assert out.shape == (4, 10)

    def test_numerical_stability(self):
        """Large inputs should not cause overflow."""
        sm = Softmax()
        x = np.array([[1000.0, 1001.0, 1002.0]])
        out = sm.forward(x)
        assert np.all(np.isfinite(out))
        np.testing.assert_allclose(out.sum(axis=1), [1.0], atol=1e-6)

    def test_backward_passthrough(self):
        """ACCEPTANCE TEST (F1): Softmax.backward(grad) returns grad unchanged.
        This is needed because Network.backward() calls backward() on every layer
        including Softmax. The combined gradient is handled by CrossEntropyLoss.backward().
        """
        sm = Softmax()
        x = np.random.randn(4, 10).astype(np.float32)
        sm.forward(x)
        grad = np.random.randn(4, 10).astype(np.float32)
        result = sm.backward(grad)
        np.testing.assert_array_equal(result, grad)

    def test_softmax_backward_is_passthrough(self):
        """Supplementary test: Softmax.backward(grad) is an identity function.

        Documents and enforces the implicit contract between Softmax and CrossEntropyLoss.
        CrossEntropyLoss.backward() returns the combined softmax+CE gradient (probs - one_hot)/N,
        which is the gradient w.r.t. pre-softmax logits. Therefore Softmax.backward() must
        return grad unchanged so the gradient flows correctly to the last Linear layer.
        """
        sm = Softmax()
        x = np.random.randn(8, 10).astype(np.float32)
        sm.forward(x)
        grad_output = np.random.randn(8, 10).astype(np.float32)
        result = sm.backward(grad_output)
        # Must be the exact same object or identical values
        np.testing.assert_array_equal(result, grad_output)

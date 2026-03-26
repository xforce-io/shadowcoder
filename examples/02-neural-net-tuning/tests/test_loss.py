# tests/test_loss.py
import numpy as np
import pytest
from nn.loss import CrossEntropyLoss


class TestCrossEntropyLoss:
    def test_perfect_prediction(self):
        """Loss should be near zero for perfect predictions."""
        loss_fn = CrossEntropyLoss()
        probs = np.eye(3, dtype=np.float32)  # perfect one-hot
        labels = np.array([0, 1, 2])
        loss = loss_fn.forward(probs, labels)
        assert loss < 1e-6

    def test_uniform_prediction(self):
        """Loss for uniform dist over 10 classes = -ln(0.1) ≈ 2.302."""
        loss_fn = CrossEntropyLoss()
        probs = np.full((2, 10), 0.1, dtype=np.float32)
        labels = np.array([0, 5])
        loss = loss_fn.forward(probs, labels)
        np.testing.assert_allclose(loss, -np.log(0.1), atol=1e-5)

    def test_backward_shape(self):
        loss_fn = CrossEntropyLoss()
        probs = np.full((4, 10), 0.1, dtype=np.float32)
        labels = np.array([0, 1, 2, 3])
        loss_fn.forward(probs, labels)
        grad = loss_fn.backward()
        assert grad.shape == (4, 10)

    def test_backward_gradient(self):
        """Combined softmax+CE gradient = (probs - one_hot) / batch_size."""
        loss_fn = CrossEntropyLoss()
        probs = np.array([[0.7, 0.2, 0.1]], dtype=np.float32)
        labels = np.array([0])
        loss_fn.forward(probs, labels)
        grad = loss_fn.backward()
        expected = probs.copy()
        expected[0, 0] -= 1.0
        expected /= 1  # batch_size = 1
        np.testing.assert_allclose(grad, expected, atol=1e-6)

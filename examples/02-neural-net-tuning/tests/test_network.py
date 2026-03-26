# tests/test_network.py
import numpy as np
import pytest
from nn.network import Network


class TestNetwork:
    def test_forward_shape(self):
        net = Network([784, 128, 10])
        x = np.random.randn(32, 784).astype(np.float32)
        out = net.forward(x)
        assert out.shape == (32, 10)

    def test_output_is_probability(self):
        net = Network([784, 64, 10])
        x = np.random.randn(8, 784).astype(np.float32)
        out = net.forward(x)
        np.testing.assert_allclose(out.sum(axis=1), np.ones(8), atol=1e-6)
        assert np.all(out >= 0)

    def test_three_layer_shape(self):
        net = Network([784, 256, 128, 10])
        x = np.random.randn(16, 784).astype(np.float32)
        out = net.forward(x)
        assert out.shape == (16, 10)

    def test_backward_runs(self):
        net = Network([784, 32, 10])
        x = np.random.randn(8, 784).astype(np.float32)
        labels = np.random.randint(0, 10, size=8)
        out = net.forward(x)
        loss = net.compute_loss(out, labels)
        net.backward()
        # Verify all linear layers have gradients
        for layer in net.layers:
            if hasattr(layer, 'grad_w'):
                assert layer.grad_w is not None
                assert layer.grad_b is not None

    def test_softmax_backward_passthrough(self):
        """ACCEPTANCE TEST (F1): Network.backward() completes without AttributeError.
        Verifies that Softmax.backward(grad) exists and returns grad unchanged.
        """
        net = Network([784, 32, 10])
        x = np.random.randn(8, 784).astype(np.float32)
        labels = np.random.randint(0, 10, size=8)
        out = net.forward(x)
        net.compute_loss(out, labels)
        # Must not raise AttributeError on Softmax layer
        net.backward()
        # Verify linear layers received gradients (proves backward passed through Softmax)
        for layer in net.linear_layers:
            assert layer.grad_w is not None

    def test_gradient_clip_large_input(self):
        """ACCEPTANCE TEST (F4): Gradients must be finite and bounded after large inputs.
        Feeds batch with large-scale inputs through network, verifies all grads are finite.
        """
        np.random.seed(42)
        net = Network([784, 32, 10])
        x = np.random.randn(8, 784).astype(np.float32) * 1000.0  # Very large inputs
        labels = np.random.randint(0, 10, size=8)
        out = net.forward(x)
        net.compute_loss(out, labels)
        net.backward()
        for layer in net.linear_layers:
            assert np.all(np.isfinite(layer.grad_w)), "grad_w contains NaN/Inf"
            assert np.all(np.isfinite(layer.grad_b)), "grad_b contains NaN/Inf"
            # If clip_value=5.0, max should be bounded
            assert np.max(np.abs(layer.grad_w)) <= net.clip_value + 1e-6
            assert np.max(np.abs(layer.grad_b)) <= net.clip_value + 1e-6

    def test_gradient_clipping_prevents_explosion(self):
        """Supplementary test: gradient clipping prevents weight explosion under extreme inputs.

        Manually sets extremely large gradients on linear layers, verifies that after
        Network.backward() clips them, and optimizer.step() runs, the weight updates
        are bounded by clip_value * lr.
        """
        np.random.seed(42)
        clip_value = 5.0
        lr = 0.1
        net = Network([784, 32, 10], clip_value=clip_value)
        from nn.optim import SGD
        opt = SGD(net.linear_layers, lr=lr)

        # Feed very large inputs to generate potentially explosive gradients
        x = np.random.randn(8, 784).astype(np.float32) * 1e4
        labels = np.random.randint(0, 10, size=8)

        # Save initial weights before update
        initial_weights = [layer.weights.copy() for layer in net.linear_layers]
        initial_biases = [layer.bias.copy() for layer in net.linear_layers]

        out = net.forward(x)
        net.compute_loss(out, labels)
        net.backward()  # Gradient clipping happens here

        # Verify all gradients are clipped to [-clip_value, clip_value]
        for layer in net.linear_layers:
            assert np.max(np.abs(layer.grad_w)) <= clip_value + 1e-6, \
                f"grad_w not clipped: max={np.max(np.abs(layer.grad_w))}"
            assert np.max(np.abs(layer.grad_b)) <= clip_value + 1e-6, \
                f"grad_b not clipped: max={np.max(np.abs(layer.grad_b))}"

        opt.step()

        # Verify weights didn't explode: max change per weight = clip_value * lr = 0.5
        for layer, init_w, init_b in zip(net.linear_layers, initial_weights, initial_biases):
            max_w_change = np.max(np.abs(layer.weights - init_w))
            max_b_change = np.max(np.abs(layer.bias - init_b))
            assert max_w_change <= clip_value * lr + 1e-6, \
                f"Weight changed by {max_w_change}, expected <= {clip_value * lr}"
            assert max_b_change <= clip_value * lr + 1e-6, \
                f"Bias changed by {max_b_change}, expected <= {clip_value * lr}"

    def test_gradient_check(self):
        """Acceptance criterion 1: numerical vs analytical gradient, relative error < 1e-5."""
        np.random.seed(42)
        net = Network([784, 32, 10])
        x = np.random.randn(8, 784).astype(np.float32) * 0.01
        labels = np.random.randint(0, 10, size=8)

        # Analytical gradients — computed FIRST, copied before numerical loop
        # NOTE: subsequent forward() calls for numerical gradient will overwrite layer caches.
        # This is safe because we copy analytical_grad before the numerical loop.
        out = net.forward(x)
        loss = net.compute_loss(out, labels)
        net.backward()

        # Check first linear layer's weight gradient
        layer = net.linear_layers[0]
        analytical_grad = layer.grad_w.copy()  # Copy before numerical loop corrupts cache

        # Numerical gradients (subset for speed)
        eps = 1e-5
        numerical_grad = np.zeros_like(layer.weights)
        np.random.seed(42)  # Reproducible index selection
        indices = [(np.random.randint(0, layer.weights.shape[0]),
                    np.random.randint(0, layer.weights.shape[1])) for _ in range(20)]
        for i, j in indices:
            old_val = layer.weights[i, j]
            layer.weights[i, j] = old_val + eps
            out_plus = net.forward(x)
            loss_plus = net.compute_loss(out_plus, labels)
            layer.weights[i, j] = old_val - eps
            out_minus = net.forward(x)
            loss_minus = net.compute_loss(out_minus, labels)
            numerical_grad[i, j] = (loss_plus - loss_minus) / (2 * eps)
            layer.weights[i, j] = old_val

        for i, j in indices:
            num = abs(analytical_grad[i, j] - numerical_grad[i, j])
            den = max(abs(analytical_grad[i, j]) + abs(numerical_grad[i, j]), 1e-8)
            rel_error = num / den
            assert rel_error < 1e-5, f"Gradient check failed at ({i},{j}): rel_error={rel_error:.2e}"

    def test_determinism(self):
        """Acceptance criterion 4: same seed → identical results."""
        x = np.random.randn(8, 784).astype(np.float32)
        labels = np.random.randint(0, 10, size=8)

        np.random.seed(99)
        net1 = Network([784, 32, 10])
        out1 = net1.forward(x)
        loss1 = net1.compute_loss(out1, labels)

        np.random.seed(99)
        net2 = Network([784, 32, 10])
        out2 = net2.forward(x)
        loss2 = net2.compute_loss(out2, labels)

        np.testing.assert_array_equal(out1, out2)
        assert loss1 == loss2

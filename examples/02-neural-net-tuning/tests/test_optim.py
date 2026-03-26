# tests/test_optim.py
import numpy as np
import pytest
from nn.layers import Linear
from nn.optim import SGD, StepLRScheduler


class TestSGD:
    def test_step_updates_weights(self):
        layer = Linear(4, 2)
        old_w = layer.weights.copy()
        layer.grad_w = np.ones_like(layer.weights)
        layer.grad_b = np.ones_like(layer.bias)
        opt = SGD([layer], lr=0.1)
        opt.step()
        expected_w = old_w - 0.1 * np.ones_like(old_w)
        np.testing.assert_allclose(layer.weights, expected_w)

    def test_momentum(self):
        layer = Linear(4, 2)
        old_w = layer.weights.copy()
        layer.grad_w = np.ones_like(layer.weights)
        layer.grad_b = np.zeros_like(layer.bias)
        opt = SGD([layer], lr=0.1, momentum=0.9)
        opt.step()
        # After first step: velocity = 1.0 * ones (momentum * 0 + grad = grad)
        w_after_1 = old_w - 0.1 * np.ones_like(old_w)
        np.testing.assert_allclose(layer.weights, w_after_1)
        # Second step: velocity = 0.9 * 1.0 + 1.0 = 1.9
        layer.grad_w = np.ones_like(layer.weights)
        opt.step()
        w_after_2 = w_after_1 - 0.1 * 1.9 * np.ones_like(old_w)
        np.testing.assert_allclose(layer.weights, w_after_2, atol=1e-6)


class TestLRScheduler:
    def test_step_decay(self):
        """LR decays every step_size epochs."""
        layer = Linear(4, 2)
        opt = SGD([layer], lr=0.1)
        scheduler = StepLRScheduler(opt, step_size=5, gamma=0.5)
        assert opt.lr == 0.1
        for _ in range(5):
            scheduler.step()
        np.testing.assert_allclose(opt.lr, 0.05)
        for _ in range(5):
            scheduler.step()
        np.testing.assert_allclose(opt.lr, 0.025)

    def test_lr_scheduler_no_immediate_decay(self):
        """Supplementary test: exact LR scheduler decay timing across multiple steps.

        Verifies three checkpoints:
        1. After 1 step: no decay (epoch=1, 1%5 != 0)
        2. After 5 total steps: exactly one decay (epoch=5, 5%5 == 0)
        3. After 6 total steps: no second decay yet (epoch=6, 6%5 != 0)
        """
        layer = Linear(4, 2)
        opt = SGD([layer], lr=0.1)
        scheduler = StepLRScheduler(opt, step_size=5, gamma=0.5)

        # 1. After 1 call: lr unchanged
        scheduler.step()
        assert opt.lr == 0.1, f"LR should not decay after 1 step, got {opt.lr}"

        # 2. After 5 total calls: lr = 0.1 * 0.5 = 0.05
        for _ in range(4):
            scheduler.step()
        np.testing.assert_allclose(opt.lr, 0.05,
            err_msg=f"LR should decay once after 5 steps, got {opt.lr}")

        # 3. After 6 total calls: lr still 0.05 (no second decay yet)
        scheduler.step()
        np.testing.assert_allclose(opt.lr, 0.05,
            err_msg=f"LR should not decay again after 6 steps, got {opt.lr}")

    def test_lr_scheduler_no_decay_at_epoch_zero(self):
        """ACCEPTANCE TEST (F9): Single scheduler.step() must NOT decay LR.
        Catches off-by-one where epoch 0 triggers decay (0 % step_size == 0).
        Implementation: epoch starts at 0, step() increments to 1, then checks
        1 % step_size == 0 → False. Decay happens at epoch 5, 10, 15...
        """
        layer = Linear(4, 2)
        opt = SGD([layer], lr=0.1)
        scheduler = StepLRScheduler(opt, step_size=5, gamma=0.5)
        scheduler.step()  # epoch becomes 1 → no decay
        assert opt.lr == 0.1, f"LR should not decay after 1 step, got {opt.lr}"

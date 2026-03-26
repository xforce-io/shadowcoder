import numpy as np


class SGD:
    """Stochastic Gradient Descent optimizer with optional momentum.

    Args:
        layers: list of Linear layer objects (must have grad_w, grad_b)
        lr: learning rate
        momentum: momentum coefficient (0 = no momentum)
    """

    def __init__(self, layers, lr: float, momentum: float = 0.0):
        self.layers = layers
        self.lr = lr
        self.momentum = momentum

        # Velocity buffers for momentum (indexed by layer id)
        self._vel_w = {}
        self._vel_b = {}
        if momentum > 0:
            for layer in layers:
                self._vel_w[id(layer)] = np.zeros_like(layer.weights)
                self._vel_b[id(layer)] = np.zeros_like(layer.bias)

    def step(self):
        """Update all layer weights using computed gradients."""
        for layer in self.layers:
            if layer.grad_w is None:
                continue

            if self.momentum > 0:
                lid = id(layer)
                self._vel_w[lid] = self.momentum * self._vel_w[lid] + layer.grad_w
                self._vel_b[lid] = self.momentum * self._vel_b[lid] + layer.grad_b
                layer.weights -= self.lr * self._vel_w[lid]
                layer.bias -= self.lr * self._vel_b[lid]
            else:
                layer.weights -= self.lr * layer.grad_w
                layer.bias -= self.lr * layer.grad_b


class StepLRScheduler:
    """Step learning rate scheduler: decay LR by gamma every step_size epochs.

    LR decays at epoch step_size, 2*step_size, 3*step_size, ...
    Never decays at epoch 0 (before any training).

    NOTE on determinism: step() uses no random state. The epoch counter is purely
    deterministic. If data augmentation is added later with its own random calls,
    those will not affect the scheduler.

    Args:
        optimizer: SGD optimizer to adjust
        step_size: number of epochs between LR decays
        gamma: multiplicative decay factor (default 0.1)
    """

    def __init__(self, optimizer: SGD, step_size: int, gamma: float = 0.1):
        self.optimizer = optimizer
        self.step_size = step_size
        self.gamma = gamma
        self.epoch = 0  # Start at 0; first decay at epoch == step_size

    def step(self):
        """Increment epoch counter and decay LR if at a step boundary.

        Called once per epoch. Decay happens at epochs step_size, 2*step_size, etc.
        """
        self.epoch += 1
        if self.epoch % self.step_size == 0:
            self.optimizer.lr *= self.gamma

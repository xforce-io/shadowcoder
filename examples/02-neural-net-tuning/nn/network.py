import numpy as np
from nn.layers import Linear
from nn.activations import ReLU, Softmax
from nn.loss import CrossEntropyLoss


class Network:
    """Sequential MLP network with configurable layer sizes.

    Architecture: Linear -> ReLU -> Linear -> ReLU -> ... -> Linear -> Softmax

    Args:
        layer_sizes: list of ints, e.g. [784, 256, 128, 10]
        clip_value: gradient clipping threshold (absolute value), default 5.0
    """

    def __init__(self, layer_sizes: list[int], clip_value: float = 5.0):
        self.clip_value = clip_value
        self.layers = []
        self.linear_layers = []

        for i in range(len(layer_sizes) - 1):
            in_size = layer_sizes[i]
            out_size = layer_sizes[i + 1]
            linear = Linear(in_size, out_size)
            self.layers.append(linear)
            self.linear_layers.append(linear)

            if i < len(layer_sizes) - 2:
                # Hidden layers get ReLU
                self.layers.append(ReLU())
            else:
                # Output layer gets Softmax
                self.layers.append(Softmax())

        self.loss = CrossEntropyLoss()

    def forward(self, x):
        """Forward pass through all layers."""
        for layer in self.layers:
            x = layer.forward(x)
        return x

    def compute_loss(self, probs, labels):
        """Compute cross-entropy loss.

        Args:
            probs: (batch_size, num_classes) output of forward()
            labels: (batch_size,) integer labels

        Returns:
            Scalar loss value
        """
        return self.loss.forward(probs, labels)

    def backward(self):
        """Backward pass: compute gradients for all layers.

        Walks layers in reverse, calling backward() on each.
        After computing all gradients, clips them for numerical stability.

        NOTE on gradient clipping: After the backward pass, all linear layer
        gradients are clipped to [-clip_value, clip_value] element-wise.
        This prevents gradient explosion with large inputs or deep networks.
        """
        grad = self.loss.backward()
        for layer in reversed(self.layers):
            grad = layer.backward(grad)

        # Gradient clipping (required for numerical stability)
        for layer in self.linear_layers:
            if layer.grad_w is not None:
                np.clip(layer.grad_w, -self.clip_value, self.clip_value, out=layer.grad_w)
                np.clip(layer.grad_b, -self.clip_value, self.clip_value, out=layer.grad_b)

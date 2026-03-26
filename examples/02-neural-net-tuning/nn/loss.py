import numpy as np


class CrossEntropyLoss:
    """Cross-entropy loss for classification.

    DESIGN NOTE — Combined Softmax+CE Gradient:
    This loss module receives post-softmax probabilities (output of Softmax.forward()).
    The backward() method returns the combined softmax+cross-entropy gradient:
        grad = (probs - one_hot) / batch_size

    This is mathematically equivalent to:
        dL/d(logits) = dL/d(probs) * d(probs)/d(logits)

    where the full Jacobian of softmax is bypassed because the combined derivative
    simplifies to (probs - one_hot). This is only correct when:
    1. The input to forward() is the output of Softmax (not raw logits)
    2. The gradient from backward() flows directly to Softmax.backward() (which is
       a pass-through), then to the last Linear layer.

    Do NOT use this loss with raw logits — it will produce incorrect gradients.
    """

    def __init__(self):
        self._probs = None
        self._labels = None

    def forward(self, probs, labels):
        """Compute cross-entropy loss.

        Args:
            probs: (batch_size, num_classes) post-softmax probabilities
            labels: (batch_size,) integer class labels

        Returns:
            Scalar mean cross-entropy loss
        """
        self._probs = probs
        self._labels = labels
        # Clip for numerical stability (avoid log(0))
        clipped = np.clip(probs, 1e-12, 1.0)
        batch_size = probs.shape[0]
        log_probs = -np.log(clipped[np.arange(batch_size), labels])
        return float(np.mean(log_probs))

    def backward(self):
        """Combined softmax + cross-entropy gradient.

        Returns:
            (batch_size, num_classes) gradient w.r.t. pre-softmax logits
        """
        batch_size = self._probs.shape[0]
        num_classes = self._probs.shape[1]
        one_hot = np.zeros((batch_size, num_classes), dtype=self._probs.dtype)
        one_hot[np.arange(batch_size), self._labels] = 1.0
        return (self._probs - one_hot) / batch_size

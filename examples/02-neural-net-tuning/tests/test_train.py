# tests/test_train.py
import time
import numpy as np
import pytest


def test_overfit_small_subset():
    """Acceptance criterion 2: >99% train acc on 100 samples in 200 epochs."""
    from nn.network import Network
    from nn.optim import SGD
    from data import load_mnist
    from train import train_epoch, evaluate

    train_images, train_labels, _, _ = load_mnist()
    x_small = train_images[:100]
    y_small = train_labels[:100]

    np.random.seed(42)
    net = Network([784, 128, 10])
    opt = SGD(net.linear_layers, lr=0.1, momentum=0.9)

    for epoch in range(200):
        train_epoch(net, opt, x_small, y_small, batch_size=100)

    acc = evaluate(net, x_small, y_small)
    assert acc > 0.99, f"Overfit test failed: acc={acc:.4f}"


def test_determinism_training():
    """Acceptance criterion 4: same seed → identical training.

    Note on determinism: np.random.seed() controls both weight initialization
    AND the shuffle order inside train_epoch(). This is fully deterministic
    as long as no external code adds random calls between seed and training.
    """
    from nn.network import Network
    from nn.optim import SGD
    from train import train_epoch

    x = np.random.randn(64, 784).astype(np.float32) * 0.01
    y = np.random.randint(0, 10, size=64)

    def run_with_seed(seed):
        np.random.seed(seed)
        net = Network([784, 32, 10])
        opt = SGD(net.linear_layers, lr=0.01)
        loss = train_epoch(net, opt, x, y, batch_size=32)
        return loss, net.forward(x)

    loss1, out1 = run_with_seed(42)
    loss2, out2 = run_with_seed(42)
    assert loss1 == loss2
    np.testing.assert_array_equal(out1, out2)


def test_forward_pass_performance():
    """Acceptance criterion 9: forward pass on 10k samples < 1 second."""
    from nn.network import Network
    np.random.seed(42)
    net = Network([784, 256, 128, 10])
    x = np.random.randn(10000, 784).astype(np.float32)
    start = time.time()
    net.forward(x)
    elapsed = time.time() - start
    assert elapsed < 1.0, f"Forward pass too slow: {elapsed:.2f}s"


# ---- Slow accuracy target tests (marked @pytest.mark.slow) ----

@pytest.mark.slow
def test_accuracy_256_128_10():
    """Acceptance criterion 5: [784,256,128,10], 30 epochs, >97% test accuracy."""
    from nn.network import Network
    from nn.optim import SGD, StepLRScheduler
    from data import load_mnist
    from train import train_epoch, evaluate

    train_images, train_labels, test_images, test_labels = load_mnist()
    np.random.seed(42)
    net = Network([784, 256, 128, 10])
    opt = SGD(net.linear_layers, lr=0.1, momentum=0.9)
    scheduler = StepLRScheduler(opt, step_size=10, gamma=0.5)
    for epoch in range(30):
        train_epoch(net, opt, train_images[:55000], train_labels[:55000], batch_size=64)
        scheduler.step()
    acc = evaluate(net, test_images, test_labels)
    assert acc > 0.97, f"Test accuracy {acc:.4f} < 0.97"


@pytest.mark.slow
def test_accuracy_128_10():
    """Acceptance criterion 6: [784,128,10], 20 epochs, >96% test accuracy."""
    from nn.network import Network
    from nn.optim import SGD, StepLRScheduler
    from data import load_mnist
    from train import train_epoch, evaluate

    train_images, train_labels, test_images, test_labels = load_mnist()
    np.random.seed(42)
    net = Network([784, 128, 10])
    opt = SGD(net.linear_layers, lr=0.1, momentum=0.9)
    scheduler = StepLRScheduler(opt, step_size=7, gamma=0.5)
    for epoch in range(20):
        train_epoch(net, opt, train_images[:55000], train_labels[:55000], batch_size=64)
        scheduler.step()
    acc = evaluate(net, test_images, test_labels)
    assert acc > 0.96, f"Test accuracy {acc:.4f} < 0.96"


@pytest.mark.slow
def test_accuracy_64_10():
    """Acceptance criterion 7: [784,64,10], 20 epochs, >95% test accuracy."""
    from nn.network import Network
    from nn.optim import SGD, StepLRScheduler
    from data import load_mnist
    from train import train_epoch, evaluate

    train_images, train_labels, test_images, test_labels = load_mnist()
    np.random.seed(42)
    net = Network([784, 64, 10])
    opt = SGD(net.linear_layers, lr=0.1, momentum=0.9)
    scheduler = StepLRScheduler(opt, step_size=7, gamma=0.5)
    for epoch in range(20):
        train_epoch(net, opt, train_images[:55000], train_labels[:55000], batch_size=64)
        scheduler.step()
    acc = evaluate(net, test_images, test_labels)
    assert acc > 0.95, f"Test accuracy {acc:.4f} < 0.95"


@pytest.mark.slow
def test_training_performance():
    """Acceptance criterion 8: 1 epoch [784,256,128,10] batch=64 < 30 seconds."""
    import time
    from nn.network import Network
    from nn.optim import SGD
    from data import load_mnist
    from train import train_epoch

    train_images, train_labels, _, _ = load_mnist()
    np.random.seed(42)
    net = Network([784, 256, 128, 10])
    opt = SGD(net.linear_layers, lr=0.01)
    start = time.time()
    train_epoch(net, opt, train_images[:55000], train_labels[:55000], batch_size=64)
    elapsed = time.time() - start
    assert elapsed < 30.0, f"Training 1 epoch too slow: {elapsed:.1f}s"

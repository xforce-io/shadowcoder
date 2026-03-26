"""Training loop and CLI entry point for MNIST neural network."""

import argparse
import numpy as np
from nn.network import Network
from nn.optim import SGD, StepLRScheduler
from data import load_mnist


def train_epoch(net, optimizer, x, y, batch_size=64):
    """Train for one epoch using mini-batch SGD.

    Shuffles data at the start of each epoch. The shuffle uses np.random,
    which is controlled by the global seed — ensuring deterministic training
    when np.random.seed() is set before calling this function.

    Args:
        net: Network instance
        optimizer: SGD optimizer
        x: (N, 784) training images
        y: (N,) training labels
        batch_size: mini-batch size

    Returns:
        Average loss over the epoch (float)
    """
    n = x.shape[0]
    indices = np.random.permutation(n)
    x_shuffled = x[indices]
    y_shuffled = y[indices]

    total_loss = 0.0
    num_batches = 0

    for start in range(0, n, batch_size):
        end = min(start + batch_size, n)
        x_batch = x_shuffled[start:end]
        y_batch = y_shuffled[start:end]

        probs = net.forward(x_batch)
        loss = net.compute_loss(probs, y_batch)
        net.backward()
        optimizer.step()

        total_loss += loss
        num_batches += 1

    return total_loss / num_batches


def evaluate(net, x, y):
    """Evaluate accuracy on a dataset.

    Args:
        net: Network instance
        x: (N, 784) images
        y: (N,) integer labels

    Returns:
        Accuracy as a float in [0, 1]
    """
    probs = net.forward(x)
    predictions = np.argmax(probs, axis=1)
    return float(np.mean(predictions == y))


def main():
    """CLI entry point for training MNIST classifier."""
    parser = argparse.ArgumentParser(description="Train MNIST neural network")
    parser.add_argument("--layers", nargs="+", type=int, default=[784, 256, 128, 10],
                        help="Layer sizes (default: 784 256 128 10)")
    parser.add_argument("--epochs", type=int, default=30,
                        help="Number of training epochs (default: 30)")
    parser.add_argument("--batch-size", type=int, default=64,
                        help="Mini-batch size (default: 64)")
    parser.add_argument("--lr", type=float, default=0.1,
                        help="Initial learning rate (default: 0.1)")
    parser.add_argument("--momentum", type=float, default=0.9,
                        help="SGD momentum (default: 0.9)")
    parser.add_argument("--lr-step", type=int, default=10,
                        help="LR scheduler step size in epochs (default: 10)")
    parser.add_argument("--lr-gamma", type=float, default=0.5,
                        help="LR scheduler decay factor (default: 0.5)")
    parser.add_argument("--seed", type=int, default=42,
                        help="Random seed (default: 42)")
    args = parser.parse_args()

    np.random.seed(args.seed)

    print("Loading MNIST...")
    train_images, train_labels, test_images, test_labels = load_mnist()

    # Split: 55000 train / 5000 validation
    x_train, y_train = train_images[:55000], train_labels[:55000]
    x_val, y_val = train_images[55000:], train_labels[55000:]

    print(f"Train: {x_train.shape[0]}, Val: {x_val.shape[0]}, Test: {test_images.shape[0]}")
    print(f"Architecture: {args.layers}")

    net = Network(args.layers)
    opt = SGD(net.linear_layers, lr=args.lr, momentum=args.momentum)
    scheduler = StepLRScheduler(opt, step_size=args.lr_step, gamma=args.lr_gamma)

    for epoch in range(1, args.epochs + 1):
        train_loss = train_epoch(net, opt, x_train, y_train, batch_size=args.batch_size)
        val_acc = evaluate(net, x_val, y_val)
        scheduler.step()
        print(f"Epoch {epoch:3d}/{args.epochs} | Loss: {train_loss:.4f} | Val Acc: {val_acc:.4f} | LR: {opt.lr:.6f}")

    test_acc = evaluate(net, test_images, test_labels)
    print(f"\nFinal Test Accuracy: {test_acc:.4f} ({test_acc*100:.2f}%)")


if __name__ == "__main__":
    main()

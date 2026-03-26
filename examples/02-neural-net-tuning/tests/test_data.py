# tests/test_data.py
import numpy as np
import pytest
from data import load_mnist


def test_load_mnist_shapes():
    train_images, train_labels, test_images, test_labels = load_mnist()
    assert train_images.shape == (60000, 784)
    assert train_labels.shape == (60000,)
    assert test_images.shape == (10000, 784)
    assert test_labels.shape == (10000,)


def test_load_mnist_dtypes():
    train_images, train_labels, test_images, test_labels = load_mnist()
    assert train_images.dtype == np.float32
    assert test_images.dtype == np.float32
    # Labels must be int64 (canonical dtype)
    assert train_labels.dtype == np.int64
    assert test_labels.dtype == np.int64


def test_load_mnist_normalized():
    train_images, _, _, _ = load_mnist()
    assert train_images.min() >= 0.0
    assert train_images.max() <= 1.0


def test_load_mnist_label_range():
    _, train_labels, _, test_labels = load_mnist()
    assert set(np.unique(train_labels)) == set(range(10))
    assert set(np.unique(test_labels)) == set(range(10))

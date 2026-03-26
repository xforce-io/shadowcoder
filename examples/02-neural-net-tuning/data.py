"""MNIST data loader with download caching and fallback URLs."""

import gzip
import struct
import urllib.request
from pathlib import Path
from urllib.error import HTTPError, URLError

import numpy as np

URLS = {
    "train_images": "http://yann.lecun.com/exdb/mnist/train-images-idx3-ubyte.gz",
    "train_labels": "http://yann.lecun.com/exdb/mnist/train-labels-idx1-ubyte.gz",
    "test_images": "http://yann.lecun.com/exdb/mnist/t10k-images-idx3-ubyte.gz",
    "test_labels": "http://yann.lecun.com/exdb/mnist/t10k-labels-idx1-ubyte.gz",
}

FALLBACK_URLS = {
    "train_images": "https://ossci-datasets.s3.amazonaws.com/mnist/train-images-idx3-ubyte.gz",
    "train_labels": "https://ossci-datasets.s3.amazonaws.com/mnist/train-labels-idx1-ubyte.gz",
    "test_images": "https://ossci-datasets.s3.amazonaws.com/mnist/t10k-images-idx3-ubyte.gz",
    "test_labels": "https://ossci-datasets.s3.amazonaws.com/mnist/t10k-labels-idx1-ubyte.gz",
}


def _download(key: str, dest: Path) -> None:
    """Download a gzipped MNIST file, trying primary then fallback URL."""
    primary_url = URLS[key]
    fallback_url = FALLBACK_URLS[key]

    for url in (primary_url, fallback_url):
        try:
            print(f"Downloading {key} from {url} ...")
            urllib.request.urlretrieve(url, dest)
            return
        except (URLError, HTTPError) as e:
            print(f"Failed to download from {url}: {e}")

    raise RuntimeError(f"Could not download {key} from any URL.")


def _load_images(path: Path) -> np.ndarray:
    """Parse IDX3 image file. Returns float32 array of shape (N, 784)."""
    with gzip.open(path, "rb") as f:
        magic, num_items, rows, cols = struct.unpack(">IIII", f.read(16))
        assert magic == 2051, f"Invalid magic number for images: {magic}"
        data = np.frombuffer(f.read(), dtype=np.uint8)
    return data.reshape(num_items, rows * cols).astype(np.float32) / 255.0


def _load_labels(path: Path) -> np.ndarray:
    """Parse IDX1 label file. Returns int64 array of shape (N,)."""
    with gzip.open(path, "rb") as f:
        magic, num_items = struct.unpack(">II", f.read(8))
        assert magic == 2049, f"Invalid magic number for labels: {magic}"
        data = np.frombuffer(f.read(), dtype=np.uint8)
    return data.astype(np.int64)


def load_mnist(cache_dir: str = ".data"):
    """Load MNIST dataset, downloading and caching if necessary.

    Parameters
    ----------
    cache_dir : str
        Directory to cache downloaded files.

    Returns
    -------
    tuple of (train_images, train_labels, test_images, test_labels)
        train_images: float32, shape (60000, 784), values in [0, 1]
        train_labels: int64, shape (60000,)
        test_images: float32, shape (10000, 784), values in [0, 1]
        test_labels: int64, shape (10000,)
    """
    cache_path = Path(cache_dir)
    cache_path.mkdir(parents=True, exist_ok=True)

    file_names = {
        "train_images": "train-images-idx3-ubyte.gz",
        "train_labels": "train-labels-idx1-ubyte.gz",
        "test_images": "t10k-images-idx3-ubyte.gz",
        "test_labels": "t10k-labels-idx1-ubyte.gz",
    }

    paths = {}
    for key, fname in file_names.items():
        dest = cache_path / fname
        if not dest.exists():
            _download(key, dest)
        paths[key] = dest

    train_images = _load_images(paths["train_images"])
    train_labels = _load_labels(paths["train_labels"])
    test_images = _load_images(paths["test_images"])
    test_labels = _load_labels(paths["test_labels"])

    return train_images, train_labels, test_images, test_labels

"""Explicit product selection; retiring Futures never erases its history."""
import os


def product_mode():
    mode = os.getenv('CSR_PRODUCT_MODE', 'futures')
    if mode not in {'futures', 'bsc'}:
        raise ValueError('unknown product mode')
    return mode


def require_futures():
    if product_mode() != 'futures':
        raise RuntimeError('Futures acquisition is retired in BSC mode')

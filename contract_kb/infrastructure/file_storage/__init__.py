# -*- coding: utf-8 -*-
"""File storage and lifecycle management."""

from .retention import collect_expired_uploads

__all__ = ["collect_expired_uploads"]

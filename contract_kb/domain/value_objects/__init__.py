# -*- coding: utf-8 -*-
"""领域值对象（金额、编号、证据定位等）。"""

from .amounts import chinese_num_to_int, parse_decimal

__all__ = ["chinese_num_to_int", "parse_decimal"]

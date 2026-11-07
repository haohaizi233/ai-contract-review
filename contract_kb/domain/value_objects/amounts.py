# -*- coding: utf-8 -*-
"""金额与中文大写数值解析值对象工具（纯函数、Decimal 精度、零外部依赖）。"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

_CN_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "壹": 1,
    "贰": 2,
    "叁": 3,
    "肆": 4,
    "伍": 5,
    "陆": 6,
    "柒": 7,
    "捌": 8,
    "玖": 9,
}

_CN_UNITS = {
    "十": 10,
    "拾": 10,
    "百": 100,
    "佰": 100,
    "千": 1000,
    "仟": 1000,
    "万": 10000,
    "萬": 10000,
    "亿": 100000000,
}


def parse_decimal(value: str | int | float | Decimal | None) -> Decimal | None:
    """将带千分位或空白的金额字符串安全解析为 Decimal，禁止经由 float。"""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    try:
        cleaned = str(value).replace(",", "").strip()
        if not cleaned:
            return None
        return Decimal(cleaned)
    except (InvalidOperation, AttributeError, ValueError):
        return None


def chinese_num_to_int(text: str) -> int | None:
    """中文大写/小写整数金额 → int（支持到亿位及零/圆/整修饰词）。"""
    if not isinstance(text, str):
        return None
    s = re.sub(r"[圆圓整元正\s]", "", text)
    if not s:
        return None
    total, section, num = 0, 0, 0
    ok = False
    for ch in s:
        if ch in _CN_DIGITS:
            num = _CN_DIGITS[ch]
            ok = True
        elif ch in _CN_UNITS:
            u = _CN_UNITS[ch]
            if u in (10000, 100000000):
                section = (section + num) * u
                total += section
                section, num = 0, 0
            else:
                section += (num if num else 1) * u
                num = 0
            ok = True
        else:
            return None
    total += section + num
    return total if ok else None

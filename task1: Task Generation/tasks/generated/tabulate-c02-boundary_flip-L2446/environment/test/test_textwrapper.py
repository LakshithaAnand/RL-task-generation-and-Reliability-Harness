"""Discretely test functionality of our custom TextWrapper"""
from __future__ import unicode_literals

import datetime

from tabulate import _CustomTextWrap as CTW, tabulate
from textwrap import TextWrapper as OTW

from common import skip, assert_equal








def test_wrap_wide_char_multiword():
    """TextWrapper: wrapping support for wide characters with multiple words"""
    try:
        import wcwidth  # noqa
    except ImportError:
        skip("test_wrap_wide_char is skipped")

    data = "약간 감싸면 더 잘 보일 수있는 다소 긴 설명입니다"

    expected = ["약간 감싸면 더", "잘 보일 수있는", "다소 긴", "설명입니다"]

    wrapper = CTW(width=15)
    result = wrapper.wrap(data)
    assert_equal(expected, result)


def test_wrap_wide_char_longword():
    """TextWrapper: wrapping wide char word that needs to be broken up"""
    try:
        import wcwidth  # noqa
    except ImportError:
        skip("test_wrap_wide_char_longword is skipped")

    data = "약간감싸면더잘보일수있"

    expected = ["약간", "감싸", "면더", "잘보", "일수", "있"]

    # Explicit odd number to ensure the 2 width is taken into account
    wrapper = CTW(width=5)
    result = wrapper.wrap(data)
    assert_equal(expected, result)


def test_wrap_mixed_string():
    """TextWrapper: wrapping string with mix of wide and non-wide chars"""
    try:
        import wcwidth  # noqa
    except ImportError:
        skip("test_wrap_wide_char is skipped")

    data = (
        "This content of this string (この文字列のこの内容) contains "
        "multiple character types (複数の文字タイプが含まれています)"
    )

    expected = [
        "This content of this",
        "string (この文字列の",
        "この内容) contains",
        "multiple character",
        "types (複数の文字タイ",
        "プが含まれています)",
    ]
    wrapper = CTW(width=21)
    result = wrapper.wrap(data)
    assert_equal(expected, result)


def test_wrapper_len_ignores_color_chars():
    data = "\033[31m\033[104mtenletters\033[0m"
    result = CTW._len(data)
    assert_equal(10, result)









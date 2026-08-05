"""Regression tests."""

from tabulate import tabulate, TableFormat, Line, DataRow
from common import assert_equal, skip










def test_iter_of_iters_with_headers():
    "Regression: Generator of generators with a gen. of headers (issue #9)."

    def mk_iter_of_iters():
        def mk_iter():
            yield from range(3)

        for r in range(3):
            yield mk_iter()

    def mk_headers():
        yield from ["a", "b", "c"]

    formatted = tabulate(mk_iter_of_iters(), headers=mk_headers())
    expected = "\n".join(
        [
            "  a    b    c",
            "---  ---  ---",
            "  0    1    2",
            "  0    1    2",
            "  0    1    2",
        ]
    )
    print(f"expected: {expected!r}\n\ngot:      {formatted!r}\n")
    assert_equal(expected, formatted)


def test_datetime_values():
    "Regression: datetime, date, and time values in cells (issue #10)."
    import datetime

    dt = datetime.datetime(1991, 2, 19, 17, 35, 26)
    d = datetime.date(1991, 2, 19)
    t = datetime.time(17, 35, 26)
    formatted = tabulate([[dt, d, t]])
    expected = "\n".join(
        [
            "-------------------  ----------  --------",
            "1991-02-19 17:35:26  1991-02-19  17:35:26",
            "-------------------  ----------  --------",
        ]
    )
    print(f"expected: {expected!r}\n\ngot:      {formatted!r}\n")
    assert_equal(expected, formatted)




def test_simple_separated_format_with_headers():
    "Regression: simple_separated_format() on tables with headers (issue #15)"
    from tabulate import simple_separated_format

    expected = "  a|  b\n  1|  2"
    formatted = tabulate(
        [[1, 2]], headers=["a", "b"], tablefmt=simple_separated_format("|")
    )
    assert_equal(expected, formatted)




def test_numeric_column_headers():
    "Regression: numbers as column headers (issue #22)"
    result = tabulate([[1], [2]], [42])
    expected = "  42\n----\n   1\n   2"
    assert_equal(result, expected)

    lod = [{p: i for p in range(5)} for i in range(5)]
    result = tabulate(lod, "keys")
    expected = "\n".join(
        [
            "  0    1    2    3    4",
            "---  ---  ---  ---  ---",
            "  0    0    0    0    0",
            "  1    1    1    1    1",
            "  2    2    2    2    2",
            "  3    3    3    3    3",
            "  4    4    4    4    4",
        ]
    )
    assert_equal(result, expected)














def test_alignment_of_decimal_numbers_with_commas():
    "Regression: alignment for decimal numbers with comma separators"
    skip("test is temporarily disable until the feature is reimplemented")
    # table = [["c1r1", "14502.05"], ["c1r2", 105]]
    # result = tabulate(table, tablefmt="grid", floatfmt=',.2f')
    # expected = "\n".join(
    #    ['+------+-----------+', '| c1r1 | 14,502.05 |',
    #    '+------+-----------+', '| c1r2 |    105.00 |',
    #    '+------+-----------+']
    # )
    # assert_equal(result, expected)


def test_long_integers():
    "Regression: long integers should be printed as integers (issue #48)"
    table = [[18446744073709551614]]
    result = tabulate(table, tablefmt="plain")
    expected = "18446744073709551614"
    assert_equal(result, expected)


def test_colorclass_colors():
    "Regression: ANSI colors in a unicode/str subclass (issue #49)"
    try:
        import colorclass

        s = colorclass.Color("{magenta}3.14{/magenta}")
        result = tabulate([[s]], tablefmt="plain")
        expected = "\x1b[35m3.14\x1b[39m"
        assert_equal(result, expected)
    except ImportError:

        class textclass(str):
            pass

        s = textclass("\x1b[35m3.14\x1b[39m")
        result = tabulate([[s]], tablefmt="plain")
        expected = "\x1b[35m3.14\x1b[39m"
        assert_equal(result, expected)


def test_mix_normal_and_wide_characters():
    "Regression: wide characters in a grid format (issue #51)"
    try:
        import wcwidth  # noqa

        ru_text = "\u043f\u0440\u0438\u0432\u0435\u0442"
        cn_text = "\u4f60\u597d"
        result = tabulate([[ru_text], [cn_text]], tablefmt="grid")
        expected = "\n".join(
            [
                "+--------+",
                "| \u043f\u0440\u0438\u0432\u0435\u0442 |",
                "+--------+",
                "| \u4f60\u597d   |",
                "+--------+",
            ]
        )
        assert_equal(result, expected)
    except ImportError:
        skip("test_mix_normal_and_wide_characters is skipped (requires wcwidth lib)")


def test_multiline_with_wide_characters():
    "Regression: multiline tables with varying number of wide characters (github issue #28)"
    try:
        import wcwidth  # noqa

        table = [["가나\n가ab", "가나", "가나"]]
        result = tabulate(table, tablefmt="fancy_grid")
        expected = "\n".join(
            [
                "╒══════╤══════╤══════╕",
                "│ 가나 │ 가나 │ 가나 │",
                "│ 가ab │      │      │",
                "╘══════╧══════╧══════╛",
            ]
        )
        assert_equal(result, expected)
    except ImportError:
        skip("test_multiline_with_wide_characters is skipped (requires wcwidth lib)")


def test_align_long_integers():
    "Regression: long integers should be aligned as integers (issue #61)"
    table = [[int(1)], [int(234)]]
    result = tabulate(table, tablefmt="plain")
    expected = "\n".join(["  1", "234"])
    assert_equal(result, expected)


def test_numpy_array_as_headers():
    "Regression: NumPy array used as headers (issue #62)"
    try:
        import numpy as np

        headers = np.array(["foo", "bar"])
        result = tabulate([], headers, tablefmt="plain")
        expected = "foo    bar"
        assert_equal(result, expected)
    except ImportError:
        raise skip("")


def test_boolean_columns():
    "Regression: recognize boolean columns (issue #64)"
    xortable = [[False, True], [True, False]]
    expected = "\n".join(["False  True", "True   False"])
    result = tabulate(xortable, tablefmt="plain")
    assert_equal(result, expected)


def test_ansi_color_bold_and_fgcolor():
    "Regression: set ANSI color and bold face together (issue #65)"
    table = [["1", "2", "3"], ["4", "\x1b[1;31m5\x1b[1;m", "6"], ["7", "8", "9"]]
    result = tabulate(table, tablefmt="grid")
    expected = "\n".join(
        [
            "+---+---+---+",
            "| 1 | 2 | 3 |",
            "+---+---+---+",
            "| 4 | \x1b[1;31m5\x1b[1;m | 6 |",
            "+---+---+---+",
            "| 7 | 8 | 9 |",
            "+---+---+---+",
        ]
    )
    assert_equal(result, expected)


def test_empty_table_with_keys_as_header():
    "Regression: headers='keys' on an empty table (issue #81)"
    result = tabulate([], headers="keys")
    expected = ""
    assert_equal(result, expected)






def test_empty_pipe_table_with_columns():
    "Regression: allow empty pipe tables with columns, like empty dataframes (github issue #15)"
    table = []
    headers = ["Col1", "Col2"]
    expected = "\n".join(["| Col1   | Col2   |", "|--------|--------|"])
    result = tabulate(table, headers, tablefmt="pipe")
    assert_equal(result, expected)







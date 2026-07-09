"""Test output of the various forms of tabular data."""

import tabulate as tabulate_module
from common import assert_equal, raises, skip
from tabulate import tabulate, simple_separated_format, SEPARATING_LINE

# _test_table shows
#  - coercion of a string to a number,
#  - left alignment of text,
#  - decimal point alignment of numbers
_test_table = [["spam", 41.9999], ["eggs", "451.0"]]
_test_table_with_sep_line = [["spam", 41.9999], SEPARATING_LINE, ["eggs", "451.0"]]
_test_table_headers = ["strings", "numbers"]




















def test_plain_maxcolwidth_autowraps_wide_chars():
    "Output: maxcolwidth and autowrapping functions with wide characters"
    try:
        import wcwidth  # noqa
    except ImportError:
        skip("test_wrap_text_wide_chars is skipped")

    table = [
        ["hdr", "fold"],
        ["1", "약간 감싸면 더 잘 보일 수있는 다소 긴 설명입니다 설명입니다 설명입니다 설명입니다 설명"],
    ]
    expected = "\n".join(
        [
            "  hdr  fold",
            "    1  약간 감싸면 더 잘 보일 수있는",
            "       다소 긴 설명입니다 설명입니다",
            "       설명입니다 설명입니다 설명",
        ]
    )
    result = tabulate(
        table, headers="firstrow", tablefmt="plain", maxcolwidths=[10, 30]
    )
    assert_equal(expected, result)






































def test_grid_wide_characters():
    "Output: grid with wide characters in headers"
    try:
        import wcwidth  # noqa
    except ImportError:
        skip("test_grid_wide_characters is skipped")
    headers = list(_test_table_headers)
    headers[1] = "配列"
    expected = "\n".join(
        [
            "+-----------+----------+",
            "| strings   |     配列 |",
            "+===========+==========+",
            "| spam      |  41.9999 |",
            "+-----------+----------+",
            "| eggs      | 451      |",
            "+-----------+----------+",
        ]
    )
    result = tabulate(_test_table, headers, tablefmt="grid")
    assert_equal(expected, result)














def test_simple_grid_wide_characters():
    "Output: simple_grid with wide characters in headers"
    try:
        import wcwidth  # noqa
    except ImportError:
        skip("test_simple_grid_wide_characters is skipped")
    headers = list(_test_table_headers)
    headers[1] = "配列"
    expected = "\n".join(
        [
            "┌───────────┬──────────┐",
            "│ strings   │     配列 │",
            "├───────────┼──────────┤",
            "│ spam      │  41.9999 │",
            "├───────────┼──────────┤",
            "│ eggs      │ 451      │",
            "└───────────┴──────────┘",
        ]
    )
    result = tabulate(_test_table, headers, tablefmt="simple_grid")
    assert_equal(expected, result)














def test_rounded_grid_wide_characters():
    "Output: rounded_grid with wide characters in headers"
    try:
        import wcwidth  # noqa
    except ImportError:
        skip("test_rounded_grid_wide_characters is skipped")
    headers = list(_test_table_headers)
    headers[1] = "配列"
    expected = "\n".join(
        [
            "╭───────────┬──────────╮",
            "│ strings   │     配列 │",
            "├───────────┼──────────┤",
            "│ spam      │  41.9999 │",
            "├───────────┼──────────┤",
            "│ eggs      │ 451      │",
            "╰───────────┴──────────╯",
        ]
    )
    result = tabulate(_test_table, headers, tablefmt="rounded_grid")
    assert_equal(expected, result)














def test_heavy_grid_wide_characters():
    "Output: heavy_grid with wide characters in headers"
    try:
        import wcwidth  # noqa
    except ImportError:
        skip("test_heavy_grid_wide_characters is skipped")
    headers = list(_test_table_headers)
    headers[1] = "配列"
    expected = "\n".join(
        [
            "┏━━━━━━━━━━━┳━━━━━━━━━━┓",
            "┃ strings   ┃     配列 ┃",
            "┣━━━━━━━━━━━╋━━━━━━━━━━┫",
            "┃ spam      ┃  41.9999 ┃",
            "┣━━━━━━━━━━━╋━━━━━━━━━━┫",
            "┃ eggs      ┃ 451      ┃",
            "┗━━━━━━━━━━━┻━━━━━━━━━━┛",
        ]
    )
    result = tabulate(_test_table, headers, tablefmt="heavy_grid")
    assert_equal(expected, result)














def test_mixed_grid_wide_characters():
    "Output: mixed_grid with wide characters in headers"
    try:
        import wcwidth  # noqa
    except ImportError:
        skip("test_mixed_grid_wide_characters is skipped")
    headers = list(_test_table_headers)
    headers[1] = "配列"
    expected = "\n".join(
        [
            "┍━━━━━━━━━━━┯━━━━━━━━━━┑",
            "│ strings   │     配列 │",
            "┝━━━━━━━━━━━┿━━━━━━━━━━┥",
            "│ spam      │  41.9999 │",
            "├───────────┼──────────┤",
            "│ eggs      │ 451      │",
            "┕━━━━━━━━━━━┷━━━━━━━━━━┙",
        ]
    )
    result = tabulate(_test_table, headers, tablefmt="mixed_grid")
    assert_equal(expected, result)














def test_double_grid_wide_characters():
    "Output: double_grid with wide characters in headers"
    try:
        import wcwidth  # noqa
    except ImportError:
        skip("test_double_grid_wide_characters is skipped")
    headers = list(_test_table_headers)
    headers[1] = "配列"
    expected = "\n".join(
        [
            "╔═══════════╦══════════╗",
            "║ strings   ║     配列 ║",
            "╠═══════════╬══════════╣",
            "║ spam      ║  41.9999 ║",
            "╠═══════════╬══════════╣",
            "║ eggs      ║ 451      ║",
            "╚═══════════╩══════════╝",
        ]
    )
    result = tabulate(_test_table, headers, tablefmt="double_grid")
    assert_equal(expected, result)














def test_fancy_grid_wide_characters():
    "Output: fancy_grid with wide characters in headers"
    try:
        import wcwidth  # noqa
    except ImportError:
        skip("test_fancy_grid_wide_characters is skipped")
    headers = list(_test_table_headers)
    headers[1] = "配列"
    expected = "\n".join(
        [
            "╒═══════════╤══════════╕",
            "│ strings   │     配列 │",
            "╞═══════════╪══════════╡",
            "│ spam      │  41.9999 │",
            "├───────────┼──────────┤",
            "│ eggs      │ 451      │",
            "╘═══════════╧══════════╛",
        ]
    )
    result = tabulate(_test_table, headers, tablefmt="fancy_grid")
    assert_equal(expected, result)
















def test_outline_wide_characters():
    "Output: outline with wide characters in headers"
    try:
        import wcwidth  # noqa
    except ImportError:
        skip("test_outline_wide_characters is skipped")
    headers = list(_test_table_headers)
    headers[1] = "配列"
    expected = "\n".join(
        [
            "+-----------+----------+",
            "| strings   |     配列 |",
            "+===========+==========+",
            "| spam      |  41.9999 |",
            "| eggs      | 451      |",
            "+-----------+----------+",
        ]
    )
    result = tabulate(_test_table, headers, tablefmt="outline")
    assert_equal(expected, result)






def test_simple_outline_wide_characters():
    "Output: simple_outline with wide characters in headers"
    try:
        import wcwidth  # noqa
    except ImportError:
        skip("test_simple_outline_wide_characters is skipped")
    headers = list(_test_table_headers)
    headers[1] = "配列"
    expected = "\n".join(
        [
            "┌───────────┬──────────┐",
            "│ strings   │     配列 │",
            "├───────────┼──────────┤",
            "│ spam      │  41.9999 │",
            "│ eggs      │ 451      │",
            "└───────────┴──────────┘",
        ]
    )
    result = tabulate(_test_table, headers, tablefmt="simple_outline")
    assert_equal(expected, result)






def test_rounded_outline_wide_characters():
    "Output: rounded_outline with wide characters in headers"
    try:
        import wcwidth  # noqa
    except ImportError:
        skip("test_rounded_outline_wide_characters is skipped")
    headers = list(_test_table_headers)
    headers[1] = "配列"
    expected = "\n".join(
        [
            "╭───────────┬──────────╮",
            "│ strings   │     配列 │",
            "├───────────┼──────────┤",
            "│ spam      │  41.9999 │",
            "│ eggs      │ 451      │",
            "╰───────────┴──────────╯",
        ]
    )
    result = tabulate(_test_table, headers, tablefmt="rounded_outline")
    assert_equal(expected, result)






def test_heavy_outline_wide_characters():
    "Output: heavy_outline with wide characters in headers"
    try:
        import wcwidth  # noqa
    except ImportError:
        skip("test_heavy_outline_wide_characters is skipped")
    headers = list(_test_table_headers)
    headers[1] = "配列"
    expected = "\n".join(
        [
            "┏━━━━━━━━━━━┳━━━━━━━━━━┓",
            "┃ strings   ┃     配列 ┃",
            "┣━━━━━━━━━━━╋━━━━━━━━━━┫",
            "┃ spam      ┃  41.9999 ┃",
            "┃ eggs      ┃ 451      ┃",
            "┗━━━━━━━━━━━┻━━━━━━━━━━┛",
        ]
    )
    result = tabulate(_test_table, headers, tablefmt="heavy_outline")
    assert_equal(expected, result)






def test_mixed_outline_wide_characters():
    "Output: mixed_outline with wide characters in headers"
    try:
        import wcwidth  # noqa
    except ImportError:
        skip("test_mixed_outline_wide_characters is skipped")
    headers = list(_test_table_headers)
    headers[1] = "配列"
    expected = "\n".join(
        [
            "┍━━━━━━━━━━━┯━━━━━━━━━━┑",
            "│ strings   │     配列 │",
            "┝━━━━━━━━━━━┿━━━━━━━━━━┥",
            "│ spam      │  41.9999 │",
            "│ eggs      │ 451      │",
            "┕━━━━━━━━━━━┷━━━━━━━━━━┙",
        ]
    )
    result = tabulate(_test_table, headers, tablefmt="mixed_outline")
    assert_equal(expected, result)






def test_double_outline_wide_characters():
    "Output: double_outline with wide characters in headers"
    try:
        import wcwidth  # noqa
    except ImportError:
        skip("test_double_outline_wide_characters is skipped")
    headers = list(_test_table_headers)
    headers[1] = "配列"
    expected = "\n".join(
        [
            "╔═══════════╦══════════╗",
            "║ strings   ║     配列 ║",
            "╠═══════════╬══════════╣",
            "║ spam      ║  41.9999 ║",
            "║ eggs      ║ 451      ║",
            "╚═══════════╩══════════╝",
        ]
    )
    result = tabulate(_test_table, headers, tablefmt="double_outline")
    assert_equal(expected, result)






def test_fancy_outline_wide_characters():
    "Output: fancy_outline with wide characters in headers"
    try:
        import wcwidth  # noqa
    except ImportError:
        skip("test_fancy_outline_wide_characters is skipped")
    headers = list(_test_table_headers)
    headers[1] = "配列"
    expected = "\n".join(
        [
            "╒═══════════╤══════════╕",
            "│ strings   │     配列 │",
            "╞═══════════╪══════════╡",
            "│ spam      │  41.9999 │",
            "│ eggs      │ 451      │",
            "╘═══════════╧══════════╛",
        ]
    )
    result = tabulate(_test_table, headers, tablefmt="fancy_outline")
    assert_equal(expected, result)


















































































_test_table_html_headers = ["<strings>", "<&numbers&>"]
_test_table_html = [["spam >", 41.9999], ["eggs &", 451.0]]
_test_table_unsafehtml_headers = ["strings", "numbers"]
_test_table_unsafehtml = [
    ["spam", '<font color="red">41.9999</font>'],
    ["eggs", '<font color="red">451.0</font>'],
]


























def test_no_data():
    "Output: table with no data"
    expected = "\n".join(["strings    numbers", "---------  ---------"])
    result = tabulate(None, _test_table_headers, tablefmt="simple")
    assert_equal(expected, result)


def test_empty_data():
    "Output: table with empty data"
    expected = "\n".join(["strings    numbers", "---------  ---------"])
    result = tabulate([], _test_table_headers, tablefmt="simple")
    assert_equal(expected, result)


def test_no_data_without_headers():
    "Output: table with no data and no headers"
    expected = ""
    result = tabulate(None, tablefmt="simple")
    assert_equal(expected, result)


def test_empty_data_without_headers():
    "Output: table with empty data and no headers"
    expected = ""
    result = tabulate([], tablefmt="simple")
    assert_equal(expected, result)




def test_empty_data_with_headers():
    "Output: table with empty data and headers as firstrow"
    expected = ""
    result = tabulate([], headers="firstrow")
    assert_equal(expected, result)




















def test_pandas_with_index():
    "Output: a pandas Dataframe with an index"
    try:
        import pandas

        df = pandas.DataFrame(
            [["one", 1], ["two", None]], columns=["string", "number"], index=["a", "b"]
        )
        expected = "\n".join(
            [
                "    string      number",
                "--  --------  --------",
                "a   one              1",
                "b   two            nan",
            ]
        )
        result = tabulate(df, headers="keys")
        assert_equal(expected, result)
    except ImportError:
        skip("test_pandas_with_index is skipped")


def test_pandas_without_index():
    "Output: a pandas Dataframe without an index"
    try:
        import pandas

        df = pandas.DataFrame(
            [["one", 1], ["two", None]],
            columns=["string", "number"],
            index=pandas.Index(["a", "b"], name="index"),
        )
        expected = "\n".join(
            [
                "string      number",
                "--------  --------",
                "one              1",
                "two            nan",
            ]
        )
        result = tabulate(df, headers="keys", showindex=False)
        assert_equal(expected, result)
    except ImportError:
        skip("test_pandas_without_index is skipped")


def test_pandas_rst_with_index():
    "Output: a pandas Dataframe with an index in ReStructuredText format"
    try:
        import pandas

        df = pandas.DataFrame(
            [["one", 1], ["two", None]], columns=["string", "number"], index=["a", "b"]
        )
        expected = "\n".join(
            [
                "====  ========  ========",
                "..    string      number",
                "====  ========  ========",
                "a     one              1",
                "b     two            nan",
                "====  ========  ========",
            ]
        )
        result = tabulate(df, tablefmt="rst", headers="keys")
        assert_equal(expected, result)
    except ImportError:
        skip("test_pandas_rst_with_index is skipped")


def test_pandas_rst_with_named_index():
    "Output: a pandas Dataframe with a named index in ReStructuredText format"
    try:
        import pandas

        index = pandas.Index(["a", "b"], name="index")
        df = pandas.DataFrame(
            [["one", 1], ["two", None]], columns=["string", "number"], index=index
        )
        expected = "\n".join(
            [
                "=======  ========  ========",
                "index    string      number",
                "=======  ========  ========",
                "a        one              1",
                "b        two            nan",
                "=======  ========  ========",
            ]
        )
        result = tabulate(df, tablefmt="rst", headers="keys")
        assert_equal(expected, result)
    except ImportError:
        skip("test_pandas_rst_with_index is skipped")


def test_dict_like_with_index():
    "Output: a table with a running index"
    dd = {"b": range(101, 104)}
    expected = "\n".join(["      b", "--  ---", " 0  101", " 1  102", " 2  103"])
    result = tabulate(dd, "keys", showindex=True)
    assert_equal(result, expected)


def test_list_of_lists_with_index():
    "Output: a table with a running index"
    dd = zip(*[range(3), range(101, 104)])
    # keys' order (hence columns' order) is not deterministic in Python 3
    # => we have to consider both possible results as valid
    expected = "\n".join(
        ["      a    b", "--  ---  ---", " 0    0  101", " 1    1  102", " 2    2  103"]
    )
    result = tabulate(dd, headers=["a", "b"], showindex=True)
    assert_equal(result, expected)


def test_list_of_lists_with_index_with_sep_line():
    "Output: a table with a running index"
    dd = [(0, 101), SEPARATING_LINE, (1, 102), (2, 103)]
    # keys' order (hence columns' order) is not deterministic in Python 3
    # => we have to consider both possible results as valid
    expected = "\n".join(
        [
            "      a    b",
            "--  ---  ---",
            " 0    0  101",
            "--  ---  ---",
            " 1    1  102",
            " 2    2  103",
        ]
    )
    result = tabulate(dd, headers=["a", "b"], showindex=True)
    assert_equal(result, expected)


def test_list_of_lists_with_supplied_index():
    "Output: a table with a supplied index"
    dd = zip(*[list(range(3)), list(range(101, 104))])
    expected = "\n".join(
        ["      a    b", "--  ---  ---", " 1    0  101", " 2    1  102", " 3    2  103"]
    )
    result = tabulate(dd, headers=["a", "b"], showindex=[1, 2, 3])
    assert_equal(result, expected)
    # TODO: make it a separate test case
    # the index must be as long as the number of rows
    with raises(ValueError):
        tabulate(dd, headers=["a", "b"], showindex=[1, 2])


def test_list_of_lists_with_index_firstrow():
    "Output: a table with a running index and header='firstrow'"
    dd = zip(*[["a"] + list(range(3)), ["b"] + list(range(101, 104))])
    expected = "\n".join(
        ["      a    b", "--  ---  ---", " 0    0  101", " 1    1  102", " 2    2  103"]
    )
    result = tabulate(dd, headers="firstrow", showindex=True)
    assert_equal(result, expected)
    # TODO: make it a separate test case
    # the index must be as long as the number of rows
    with raises(ValueError):
        tabulate(dd, headers="firstrow", showindex=[1, 2])









"""Command-line interface.

"""


import os
import sys


import subprocess
import tempfile


from common import assert_equal


SAMPLE_SIMPLE_FORMAT = "\n".join(
    [
        "-----  ------  -------------",
        "Sun    696000     1.9891e+09",
        "Earth    6371  5973.6",
        "Moon     1737    73.5",
        "Mars     3390   641.85",
        "-----  ------  -------------",
    ]
)


SAMPLE_SIMPLE_FORMAT_WITH_HEADERS = "\n".join(
    [
        "Planet      Radius           Mass",
        "--------  --------  -------------",
        "Sun         696000     1.9891e+09",
        "Earth         6371  5973.6",
        "Moon          1737    73.5",
        "Mars          3390   641.85",
    ]
)


SAMPLE_GRID_FORMAT_WITH_HEADERS = "\n".join(
    [
        "+----------+----------+---------------+",
        "| Planet   |   Radius |          Mass |",
        "+==========+==========+===============+",
        "| Sun      |   696000 |    1.9891e+09 |",
        "+----------+----------+---------------+",
        "| Earth    |     6371 | 5973.6        |",
        "+----------+----------+---------------+",
        "| Moon     |     1737 |   73.5        |",
        "+----------+----------+---------------+",
        "| Mars     |     3390 |  641.85       |",
        "+----------+----------+---------------+",
    ]
)


SAMPLE_GRID_FORMAT_WITH_DOT1E_FLOATS = "\n".join(
    [
        "+-------+--------+---------+",
        "| Sun   | 696000 | 2.0e+09 |",
        "+-------+--------+---------+",
        "| Earth |   6371 | 6.0e+03 |",
        "+-------+--------+---------+",
        "| Moon  |   1737 | 7.4e+01 |",
        "+-------+--------+---------+",
        "| Mars  |   3390 | 6.4e+02 |",
        "+-------+--------+---------+",
    ]
)


def sample_input(sep=" ", with_headers=False):
    headers = sep.join(["Planet", "Radius", "Mass"])
    rows = [
        sep.join(["Sun", "696000", "1.9891e9"]),
        sep.join(["Earth", "6371", "5973.6"]),
        sep.join(["Moon", "1737", "73.5"]),
        sep.join(["Mars", "3390", "641.85"]),
    ]
    all_rows = ([headers] + rows) if with_headers else rows
    table = "\n".join(all_rows)
    return table


def run_and_capture_stdout(cmd, input=None):
    x = subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    input_buf = input.encode() if input else None
    out, err = x.communicate(input=input_buf)
    out = out.decode("utf-8")
    if x.returncode != 0:
        raise OSError(err)
    return out


class TemporaryTextFile:
    def __init__(self):
        self.tmpfile = None

    def __enter__(self):
        self.tmpfile = tempfile.NamedTemporaryFile(
            "w+", prefix="tabulate-test-tmp-", delete=False
        )
        return self.tmpfile

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.tmpfile:
            self.tmpfile.close()
            os.unlink(self.tmpfile.name)















"""Template-generated edge-case verifier tests (Stage 5).

Provenance: template_generated. Each test is a characterization test:
the expected value is the ORACLE-state output of the public tabulate()
API for a template-relevant edge input. Admitted only after proving it
fails on the broken state and passes on the oracle state in-container.
"""
import tabulate

T = tabulate.tabulate

def test_edge_boundary_flip_wrap_exact():
    # edge: at_boundary
    assert (T([["abcdef"]], maxcolwidths=[3])) == '---\nabc\ndef\n---'

def test_edge_boundary_flip_wrap_words():
    # edge: above_boundary
    assert (T([["a bb ccc dddd"]], maxcolwidths=[4])) == '----\na bb\nccc\ndddd\n----'

def test_edge_boundary_flip_wrap_longword_grid():
    # edge: above_boundary
    assert (T([["verylongword"]], maxcolwidths=[5], tablefmt="grid")) == '+-------+\n| veryl |\n| ongwo |\n| rd    |\n+-------+'

def test_edge_boundary_flip_wrap_width1():
    # edge: below_boundary
    assert (T([["abc"]], maxcolwidths=[1])) == '-\na\nb\nc\n-'

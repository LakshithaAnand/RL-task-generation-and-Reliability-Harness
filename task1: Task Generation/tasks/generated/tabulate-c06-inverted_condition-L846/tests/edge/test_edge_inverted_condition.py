"""Template-generated edge-case verifier tests (Stage 5).

Provenance: template_generated. Each test is a characterization test:
the expected value is the ORACLE-state output of the public tabulate()
API for a template-relevant edge input. Admitted only after proving it
fails on the broken state and passes on the oracle state in-container.
"""
import tabulate

T = tabulate.tabulate

def test_edge_inverted_condition_pipe_center():
    # edge: condition_false_path
    assert (T([["a"]], headers=["h"], tablefmt="pipe", colalign=("center",))) == '|  h  |\n|:---:|\n|  a  |'

def test_edge_inverted_condition_unsafehtml():
    # edge: condition_false_path
    assert (T([["<b>x</b>"]], tablefmt="unsafehtml")) == '<table>\n<tbody>\n<tr><td><b>x</b></td></tr>\n</tbody>\n</table>'

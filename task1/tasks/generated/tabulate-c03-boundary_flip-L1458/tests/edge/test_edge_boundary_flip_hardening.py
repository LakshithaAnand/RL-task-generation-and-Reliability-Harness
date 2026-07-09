"""Template-generated edge-case verifier tests (Stage 5).

Provenance: template_generated. Each test is a characterization test:
the expected value is the ORACLE-state output of the public tabulate()
API for a template-relevant edge input. Admitted only after proving it
fails on the broken state and passes on the oracle state in-container.
"""
import tabulate

T = tabulate.tabulate

def test_edge_boundary_flip_hardening_firstrow_only():
    # edge: at_boundary
    assert (T([["a", "b"]], headers="firstrow")) == 'a    b\n---  ---'

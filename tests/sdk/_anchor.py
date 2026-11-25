"""Test helper constants for bindings.

Two things make a V2 binding armable (``pysdk/v2/binding.py``):

  1. the activation stamp inside the binding file (``binding_source`` + ``activated_at``), which
     fixtures include in the dict they build because it IS the binding's content;
  2. nothing else locally — the ANCHOR relation is witnessed by the gateway (``pysdk/v2/anchor.py``
     computes the identifier it registers and re-checks). An earlier revision kept an
     ``anchor.json`` ledger next to the binding; it was removed on 2026-11-25 because the gateway's
     witness makes it redundant (and two files in the same home can always be edited together).
"""

#: The two fields a real activation writes into the binding.
ACTIVATION_STAMP = {
    "binding_source": "activate-address-code",
    "activated_at": "2026-11-25T10:00:00Z",
}

#: Default anchor used by fixtures (the path itself never has to exist for the checks).
ANCHOR_PATH = "/tmp/aimail-anchor-ws"

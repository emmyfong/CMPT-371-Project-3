# CMPT 371 Project 3 - protocol constants. Do not modify this file.
"""Every constant the protocol needs, in one place.

Read these from the module rather than copying their values into your code.
The TA grades with a fresh copy of this file and changes values in it: MSS,
WINDOW and SEQ_BITS all take different values during marking, and a hard-coded
100 anywhere in your code is a check you fail.

UNITS. Every time below is in SECONDS, as a float. Every window below is in
SEGMENTS, never bytes. Mixing either pair is the most expensive mistake
available in this project, because nothing crashes: the code runs and the
numbers are merely meaningless.
"""

MSS = 100  # payload octets in one data segment
WINDOW = 4  # N, the protocol window, in SEGMENTS
RCV_BUF = 8  # receiver buffer capacity, in SEGMENTS
SEQ_BITS = 16  # the sequence space has 2 ** SEQ_BITS values

# The retransmission timeout is fixed. It is deliberately longer than the round
# trip on every profile and shorter than a stalled transfer: 150 ms against a
# path whose round trip is 20 to 60 ms. Measuring the round trip and adapting to
# it is bonus B1's business, not the core protocol's.
RTO_INIT = 0.15  # seconds
PERSIST = 0.50  # zero-window probe interval, seconds

HOST = "127.0.0.1"

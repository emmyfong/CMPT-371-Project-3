# Name: Alicia Lam (301552532), Emmy Fong (301585818), Truman Ho (301558416)
"""The sending half of the protocol: Go-Back-N and Selective Repeat.

Stop-and-wait is Go-Back-N with a window of one, so build task 1 in a way that
task 2 can widen rather than replace.

Everything below the GIVEN line is written for you, including the argument
parser and the reading of the input file. Implement the class above it.
"""

import argparse
import socket
import sys
import threading
import time

import config
import segment
from segment import RDTSegment, RdtBuffer


class Sender:
    """One direction of a connection, from the application into the channel.

    Two threads. The application thread calls send() and close(); the network
    thread you start in start() owns the socket, receives acknowledgements and
    runs the retransmission timers. They share the window, so decide which lock
    protects which variable and write it down here -- you will be asked.
    """

    def __init__(self, sock, channelAddr, protocol):
        """Set up the window and the timers.

        The window is a segment.RdtBuffer. Size it with config.WINDOW for now.
        In task 5 you replace that with segment.checkWindow(protocol), which
        returns the same number but refuses to run when the configured window is
        too wide for the sequence space; leave it as config.WINDOW until you
        have written windowLimit, because checkWindow asks windowLimit for the
        answer and cannot work before it exists.

        The buffer owns base, next and the segments themselves, so this class
        never touches slots directly: it calls putNext to admit a segment, get
        to fetch one for retransmission, markAcked when an ACK arrives, and
        setLimit when flow control or congestion control narrows the window.

        Beside it you need the receiver's advertised window and your timer
        deadlines, and one threading.Lock of your own to protect them. That lock
        is yours and is not the buffer's: RdtBuffer has its own Condition
        guarding slots, base and next, and never needs yours. Two primitives,
        two jobs -- a Condition where a thread has to wait for the buffer to
        change, a plain Lock where it only has to read and write your own
        fields without another thread doing so at the same time.

        The indices the buffer keeps never wrap; reduce them into the sequence
        space only when a segment is built, so exactly one place in your code
        has to think about wrapping.

        Timers are deadlines, not threads. Keep the time by which an ACK must
        arrive, and None when no timer is running: one deadline for the whole
        window under Go-Back-N, one per segment under Selective Repeat --
        RDTSegment already has a `deadline` attribute for exactly that.
        Cancelling a timer is then an assignment. The value is config.RTO_INIT
        and it does not change; measuring the path is the bonus.
        """
        raise NotImplementedError

    def start(self):
        """Start the network thread.

        That thread is one loop and nothing else. Give the socket a short
        timeout -- a few milliseconds -- so it wakes whether or not a datagram
        arrived, and each time round:

            * if a datagram arrived, validate it and act on it: an ACK marks
              its segment, may advance base, and clears that segment's
              deadline. Task 3 also reads rcvWin off it and calls setLimit.
            * then compare time.monotonic() against the deadlines you kept and
              retransmit whatever has expired, emitting traceTimeout first.

        Do the socket call outside the buffer's Condition, never inside it.
        """
        raise NotImplementedError

    def send(self, payload):
        """Hand one segment's worth of data to the protocol.

        Blocks while the window is full, and returns True once the segment has
        been accepted for transmission, or False if the transfer has failed.

        One exception to blocking: if the receiver has advertised a window of
        zero and nothing is outstanding, no acknowledgement will ever arrive to
        reopen it, because there is nothing left to acknowledge. After
        config.PERSIST seconds, transmit anyway.
        """
        raise NotImplementedError

    def close(self):
        """Wait for every segment to be acknowledged, then take the connection
        down: send a FIN, retransmit it if it is lost, and return True once it
        has been acknowledged. Return False if the connection could not be
        closed. Must return within a bounded time in either case.
        """
        raise NotImplementedError


# ============================================================================
# GIVEN - do not modify anything below this line.
#
# Argument parsing, socket setup and the reading of the input file. The grading
# script invokes exactly these options, and none of this is what the project is
# teaching, so it is handed to you rather than assigned.
# ============================================================================

def parseArgs(argv):
    ap = argparse.ArgumentParser(description="CMPT 371 Project 3 - RDT sender")
    ap.add_argument("--channel", type=int, required=True,
                    help="the port channel.py printed for the sender side")
    ap.add_argument("--in", dest="inPath", required=True, help="file to send")
    ap.add_argument("--protocol", default="gbn", choices=("gbn", "sr"))
    ap.add_argument("--trace", default=None, help="write the event log here")
    return ap.parse_args(argv)


def openSocket():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((config.HOST, 0))
    # A generous receive buffer: a burst of acknowledgements that arrives while
    # this program is busy elsewhere is otherwise dropped by the operating
    # system, and looks exactly like loss the channel never injected.
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
    return sock


def main(argv=None):
    args = parseArgs(argv if argv is not None else sys.argv[1:])
    if (not isinstance(segment.SEED, int) or isinstance(segment.SEED, bool)
            or segment.SEED <= 0):
        sys.exit("set SEED at the top of segment.py to your Canvas group number")
    segment.traceOpen(args.trace)

    data = open(args.inPath, "rb").read()
    sock = openSocket()
    sender = Sender(sock, (config.HOST, args.channel), args.protocol)
    sender.start()
    for i in range(0, len(data), config.MSS):
        if not sender.send(data[i:i + config.MSS]):
            break
    try:
        ok = sender.close()
    except NotImplementedError:
        # Graceful teardown is task 4. Until it is written, give the last window
        # time to be acknowledged and then exit cleanly, so that the tasks
        # before it are still graded on the bytes that arrived rather than on
        # how the connection ended. This costs two seconds and nothing else.
        time.sleep(2.0)
        ok = True
    sock.close()
    if not ok:
        print("transfer failed: %s" % (getattr(sender, "failed", None) or "unknown"),
              file=sys.stderr)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

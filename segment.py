# Name: <your full name> (<your student number>), <partner> (<their number>)
"""The segment format, the checksum, the sequence space, and the shared buffer.

Both of your programs import this module, which is why the seed lives here: it
is the one file rdtSender.py, rdtReceiver.py and channel.py all agree on.
RdtBuffer is here for the same reason: both programs need it and neither owns it.

Everything below the GIVEN line is written for you. So are the wire format
(pack and unpack), the two sequence-space helpers (space and mask), and seven of
RdtBuffer's ten methods. Implement the rest: checksum, validate, the window
rules, and three buffer methods.
"""

import struct
import sys
import threading
import time

import config

# Set this to your Canvas group number before you run anything else. It is the
# seed for every run you report, and channel.py reads it from this file, so the
# three programs can never disagree about it.
SEED = 0

HEADER = "!IIBxHHH"  # seqNum, ackNum, flags, pad, checksum, rcvWin, length
HEADER_LEN = struct.calcsize(HEADER)

FLAG_ACK = 0x1
FLAG_FIN = 0x2
FLAG_RESERVED = 0xFC


def checksum(data):
    """Return the RFC 1071 Internet checksum of data, as an integer.

    You wrote this in project 2. Reuse that code.

    Sum the data as 16-bit big-endian words, fold every carry out of the top
    back into the bottom, and take the one's complement of the result. If the
    data has an odd number of bytes, pad with a single zero byte for the
    calculation only; do not transmit the padding.

    Running this over a whole datagram exactly as received, checksum field
    included, gives zero if and only if nothing changed in transit.
    """
    raise NotImplementedError


class RDTSegment:
    """One segment: a 16-byte header and its payload."""

    def __init__(self, seqNum=0, ackNum=0, flags=0, rcvWin=0, length=None, payload=b""):
        # This constructor is given. The header attribute names are the ones the
        # given trace emitter reads, so keep them.
        self.seqNum = seqNum
        self.ackNum = ackNum
        self.flags = flags
        self.rcvWin = rcvWin
        self.payload = payload
        self.length = len(payload) if length is None else length

        # Bookkeeping, not header fields. These never reach the wire; they are
        # here because the natural place to record what has happened to a
        # segment is on the segment.
        self.acked = False    # an ACK for this segment has arrived
        self.deadline = None  # when its retransmission timer expires

    # --- GIVEN SEGMENT METHODS - do not modify ---
    def pack(self):
        """Return the complete datagram, with a correct checksum."""
        head = struct.pack(HEADER, self.seqNum, self.ackNum, self.flags, 0,
                           self.rcvWin, self.length)
        value = checksum(head + self.payload)
        head = struct.pack(HEADER, self.seqNum, self.ackNum, self.flags, value,
                           self.rcvWin, self.length)
        return head + self.payload

    @classmethod
    def unpack(cls, data):
        """Rebuild a segment from a datagram. Call validate() on it first."""
        seqNum, ackNum, flags, _value, rcvWin, length = struct.unpack(
            HEADER, data[:HEADER_LEN])
        return cls(seqNum=seqNum, ackNum=ackNum, flags=flags, rcvWin=rcvWin,
                   length=length, payload=data[HEADER_LEN:])
    # --- END GIVEN SEGMENT METHODS ---

    @staticmethod
    def validate(data):
        """Return None if the datagram is acceptable, or the reason it is not.

        A receiver must discard a segment, silently and without acknowledging
        it, if the checksum fails, if any reserved flag bit is set, or if the
        length field does not equal len(data) - 16.

        Return "checksum", "flags" or "length". Report a failed checksum first:
        a damaged length field is a corruption, and the two are otherwise
        indistinguishable.
        """
        raise NotImplementedError

    @property
    def isAck(self):
        return bool(self.flags & FLAG_ACK)

    @property
    def isFin(self):
        return bool(self.flags & FLAG_FIN)

    def __repr__(self):
        return "RDTSegment(seq=%d ack=%d flags=0x%x win=%d len=%d)" % (
            self.seqNum, self.ackNum, self.flags, self.rcvWin, self.length)


# ------------------------------------------------------------- sequence space

# --- GIVEN SEQUENCE HELPERS - do not modify ---
def space():
    """The number of distinct sequence numbers, 2 ** SEQ_BITS."""
    return 1 << config.SEQ_BITS


def mask(n):
    """The wire value of logical segment index n.

    Given, because reducing an integer into a range teaches nothing about
    reliable transfer. Where you call it is still yours to get right: every
    seqNum and ackNum that reaches the wire has to pass through here, and every
    index you keep in your own code has to stay unreduced.
    """
    return n % space()
# --- END GIVEN SEQUENCE HELPERS ---


def inWindow(x, base, n):
    """Is x within the n consecutive sequence numbers starting at base?

    Every "is this in my window?" test in your code must go through this, and
    the subtraction has to happen first and be reduced modulo the size of the
    space. Comparing sequence numbers with < instead gives the right answer
    only while the space is large enough never to wrap.
    """
    raise NotImplementedError


def windowLimit(protocol):
    """The largest window this protocol may use in the configured space.

    One of the two protocols tolerates a window one short of the whole space,
    and the other needs half of it. Work out which, and why, before you write
    this: the reason is a question you will be asked.
    """
    raise NotImplementedError


# --- GIVEN WINDOW CHECK - do not modify ---
def checkWindow(protocol):
    """How many slots the buffer gets, or a refusal to run.

    Given, because it is a comparison and an exit. The judgement it depends on
    is not: it asks windowLimit(), which you write, so a wrong limit shows up
    here as a program that runs when it should have stopped, or stops when it
    should have run.
    """
    want = config.WINDOW
    limit = windowLimit(protocol)
    if want > limit:
        sys.exit("config error: %s with SEQ_BITS=%d allows a window of at most %d, "
                 "but WINDOW is %d. Reduce WINDOW or raise SEQ_BITS."
                 % (protocol, config.SEQ_BITS, limit, want))
    return want
# --- END GIVEN WINDOW CHECK ---


# --------------------------------------------------------------- the buffer

class RdtBuffer:
    """A bounded, sequence-indexed buffer shared by two threads.

    Both of your programs are built around one of these. The sender's holds
    segments transmitted but not yet acknowledged; the receiver's holds segments
    that have arrived but not yet been read by the application. In both cases
    one thread fills it and the other empties it, which is the classic
    producer-consumer problem.

        slots   a circular array of `size` entries, indexed by logical index
        base    the oldest logical index still held
        next    one past the highest logical index placed
        limit   the effective window; the sender lowers it for flow control
        cv      one Condition guarding all of the above

    One Condition guards all of it: a lock with a waiting room attached, so a
    thread that must wait for the buffer to have room, or to have anything in
    it, can release the lock while it sleeps and hold it again when it wakes.

    Two rules, and they matter more than they look:

      * every method takes the Condition, changes what it must, calls
        notify_all(), and releases. Nothing else touches slots, base or next.
      * nothing does a socket call or a sleep while holding it.

    Seven of the ten methods are given below. Write getNext, putSeqNum and
    markAcked, following the shape of putNext.
    """

    def __init__(self, size):
        self.slots = [None] * size
        self.size = size
        self.base = 0
        self.next = 0
        self.limit = size
        self.closed = False
        self.cv = threading.Condition()

    # --- GIVEN BUFFER METHODS - do not modify ---

    def putNext(self, seg, timeout=None, force=False):
        """Place a segment at `next`, blocking while the window is full.

        Returns True when it was placed, False when `timeout` seconds passed
        with the window still full. `force` places it regardless, which is what
        the zero-window probe of task 3 needs and nothing else does.

        This is the worked example. getNext and putSeqNum below follow the same
        shape: take the Condition, wait for the state you need, change it,
        notify everyone, release.
        """
        with self.cv:
            if not force:
                room = self.cv.wait_for(
                    lambda: self.next - self.base < min(self.size, self.limit)
                            or self.closed, timeout)
                if not room or self.closed:
                    return False
            self.slots[self.next % self.size] = seg
            self.next += 1
            self.cv.notify_all()
            return True

    def get(self, index):
        """The segment held at that logical index, or None. For retransmission."""
        with self.cv:
            if not self.base <= index < self.next:
                return None
            return self.slots[index % self.size]

    def freeSlots(self):
        """How many more segments fit. This is what rcvWin advertises."""
        with self.cv:
            return max(0, self.size - (self.next - self.base))

    def outstanding(self):
        """How many logical indices are in play: next - base."""
        with self.cv:
            return self.next - self.base

    def firstEmpty(self):
        """The lowest logical index at or after base whose slot is still empty.

        This is the receiver's rcvBase: the next index it has not yet received.
        It is not the same as base, which is the next index the application has
        not yet read -- a slow application leaves segments sitting in the buffer
        that have very much been received.
        """
        with self.cv:
            index = self.base
            while index < self.base + self.size and self.slots[index % self.size] is not None:
                index += 1
            return index

    def setLimit(self, n):
        """Lower the effective window below the buffer's size.

        The sender calls this with min(WINDOW, rcvWin) -- and with cwnd too, if
        you attempt bonus B1. Waking the waiters matters: a sender blocked in
        putNext because the window was closed has to be told it reopened.
        """
        with self.cv:
            self.limit = max(0, n)
            self.cv.notify_all()

    def close(self):
        """No more segments will arrive. Releases anyone blocked in getNext."""
        with self.cv:
            self.closed = True
            self.cv.notify_all()

    # --- END GIVEN BUFFER METHODS ---


    def getNext(self, timeout=None):
        """Remove and return the segment at `base`, blocking until there is one.

        Return None if the buffer was closed while waiting, or if `timeout`
        seconds passed. This is what makes delivery in-order: it hands over
        index base and no other, so a segment that arrived early waits until
        the gap before it is filled.

        Follow putNext: take the Condition, wait_for the state you need, change
        it, notify_all, release.
        """
        raise NotImplementedError

    def putSeqNum(self, seg, index):
        """Place a segment at a logical index that may be ahead of `base`.

        This is what Selective Repeat's receiver needs and Go-Back-N's does not:
        a segment that arrives out of order goes into the slot its sequence
        number names, not the next free one.

        Return False, changing nothing, if the index is outside
        [base, base + size) or if that slot is already occupied. Remember that
        `next` has to end up one past the highest index placed.

        This is where the receiver's buffer departs from the plain
        producer-consumer pattern: it is not filled in order. A segment placed
        ahead of a gap sits in its slot until the gap before it is filled, and
        getNext then hands over the whole run -- so in-order, exactly-once
        delivery is a property of this class rather than of code in
        rdtReceiver.py. Placing a segment at the wrong index is therefore not a
        local mistake: it delivers the file in the wrong order.
        """
        raise NotImplementedError

    def markAcked(self, index):
        """Flag the segment at `index`, then slide base past a run of acked slots.

        Under Go-Back-N an ACK is cumulative, so the caller marks every index
        through the one acknowledged and base ends up past all of them. Under
        Selective Repeat a single index is marked, and base moves only when the
        oldest segment is among those acknowledged -- possibly several places at
        once, which is the case worth testing.

        Return False if the index is not in [base, next) or the slot is empty.

        This is the sender's departure from the plain producer-consumer
        pattern: its buffer is not emptied in order. Under Selective Repeat
        segment 5 may be acknowledged while 3 is still outstanding, so a slot
        in the middle is spoken for while base does not move. Flag first, slide
        second, and slide as far as the run of acknowledged slots reaches --
        which is why one call can free several slots and wake an application
        thread that has been blocked in putNext.
        """
        raise NotImplementedError


# ============================================================================
# GIVEN - do not modify anything below this line.
#
# The canonical trace emitter. The grading script parses these lines, formatting
# them is fiddly, and none of it is what the project is teaching, so it is
# handed to you rather than assigned. Call these at the right moments; getting
# the moments right is the part that reflects whether your protocol is correct.
# ============================================================================

_traceLock = threading.Lock()
_traceOut = sys.stdout
_traceStart = None


def traceOpen(path=None):
    """Send the trace to a file, or to stdout when path is None."""
    global _traceOut, _traceStart
    _traceOut = open(path, "w", buffering=1) if path else sys.stdout
    _traceStart = None


def _emit(event, fields):
    global _traceStart
    with _traceLock:
        now = time.monotonic()
        if _traceStart is None:
            _traceStart = now
        print("t_ms=%06d seed=%d ev=%-7s %s"
              % (int((now - _traceStart) * 1000), SEED, event, fields),
              file=_traceOut, flush=True)


def _segmentFields(seg, base, nextSeq, win, cwnd):
    # A segment carrying the ACK flag is identified by its ackNum and its seqNum
    # is meaningless; a data or FIN segment is the other way round. The field
    # that does not apply is reported as -1.
    if seg.flags & FLAG_ACK:
        shown, acked = -1, seg.ackNum
    else:
        shown, acked = seg.seqNum, -1
    text = ("seq=%d ack=%d flags=0x%x len=%d base=%d next=%d win=%d"
            % (shown, acked, seg.flags, seg.length, base, nextSeq, win))
    if cwnd is not None:
        text += " cwnd=%d" % cwnd
    return text


def traceSend(seg, base, nextSeq, win, cwnd=None):
    """Every transmission, retransmissions included. Once per datagram sent.

    base and nextSeq bound your window. win is how much of it you may use right
    now, which on the sender is WINDOW capped by the last rcvWin you were told,
    and by cwnd if you attempt bonus B1; on the receiver it is your own free
    space, buf.freeSlots(). Report the value you are actually using, not the
    constant from config.py: graded checks watch it fall to zero when the
    receiver's buffer fills, and a constant never does.
    """
    _emit("SEND", _segmentFields(seg, base, nextSeq, win, cwnd))


def traceRecv(seg, base, nextSeq, win, cwnd=None):
    """Every segment that arrived and passed every validity test.

    Same fields as traceSend, reported from your own state at the moment the
    segment arrived -- so on the sender, win here has already taken account of
    the rcvWin this segment carried.
    """
    _emit("RECV", _segmentFields(seg, base, nextSeq, win, cwnd))


def traceTimeout(seq, base, nextSeq):
    """A retransmission timer expired, emitted before anything is resent."""
    _emit("TIMEOUT", "seq=%d base=%d next=%d" % (seq, base, nextSeq))


def traceDeliver(seq, length):
    """A payload handed to the application. Once per segment, ever."""
    _emit("DELIVER", "seq=%d len=%d" % (seq, length))


def traceDrop(seq, reason):
    """A valid segment discarded anyway: window, dup, flags or length."""
    _emit("DROP", "seq=%d reason=%s" % (seq, reason))


def traceCorrupt(seq):
    """A segment discarded because the checksum failed."""
    _emit("CORRUPT", "seq=%d" % seq)

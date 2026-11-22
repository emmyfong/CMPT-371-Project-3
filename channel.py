# CMPT 371 Project 3 - instructor-supplied channel. Do not modify this file.
"""The unreliable network your segments cross.

Usage: python3 channel.py --profile loss [--quiet]

Binds two UDP sockets on 127.0.0.1 and forwards datagrams between them,
injecting loss, corruption, duplication and delay in each direction
independently. Prints one "Listening on port <n> side <sender|receiver>" line
per socket on stdout, and one decision line per datagram on stderr.

The seed is read out of segment.py, never executed, so the three programs
cannot disagree about it. --seed exists only as an instructor override.
"""

import argparse
import ast
import hashlib
import heapq
import os
import selectors
import socket
import struct
import sys
import time

HEADER = "!IIBxHHH"
HEADER_LEN = struct.calcsize(HEADER)
FLAG_ACK = 0x1

HERE = os.path.dirname(os.path.abspath(__file__))
SEED_FILE = "segment.py"  # the one student file both programs import

LINK_RATE = 50000.0  # bytes/second of injected transmission delay, per direction
QUEUE_LIMIT = 20  # datagrams in flight per direction; beyond this they are dropped

# Fixed so that the commands in the handout can be typed as they are printed.
# Override with --ports when they are taken; --ports 0,0 asks the operating
# system for any free pair, which is what checkSetup.py and the grading script
# do so that they never collide with a channel a student left running.
DEFAULT_PORTS = (51001, 51002)

# base one-way delay (s), jitter width (s, so the swing is +/- half of it),
# loss, corruption, duplication.
#
# Every profile satisfies base > jitter/2, so no draw is ever clipped at zero.
# A profile that violates it would pile probability mass on a delay of exactly
# 0.0, shifting the mean up and truncating the spread, and both would then
# disagree with the table published in the handout.
# 'clean' additionally keeps its jitter below the 2.5 ms the link takes to
# transmit one full segment, so consecutive segments cannot overtake each other.
# At +/-2 ms they can, and Go-Back-N then times out on a profile advertised as
# lossless -- correct behavior, but not what a profile for getting the plumbing
# working should be doing.
PROFILES = {
    "clean":   (0.010, 0.002, 0.00, 0.00, 0.00),
    "loss":    (0.020, 0.010, 0.10, 0.00, 0.00),
    "corrupt": (0.020, 0.010, 0.02, 0.08, 0.00),
    "dup":     (0.020, 0.010, 0.05, 0.00, 0.10),
    "reorder": (0.020, 0.030, 0.05, 0.00, 0.00),
    "hostile": (0.030, 0.030, 0.12, 0.05, 0.08),
}
for _name, _p in PROFILES.items():
    assert _p[0] > _p[1] / 2, "profile %s would clip its delay at zero" % _name


def student_seed():
    """Read the SEED assignment out of segment.py without executing the file."""
    path = os.path.join(HERE, SEED_FILE)
    try:
        source = open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        sys.exit("channel.py: no %s beside this file to read SEED from" % SEED_FILE)
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        sys.exit("channel.py: %s does not compile (line %s, %s), so SEED cannot be read"
                 % (SEED_FILE, exc.lineno, exc.msg))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not (isinstance(target, ast.Name) and target.id == "SEED"):
                continue
            value = node.value
            if (isinstance(value, ast.Constant) and isinstance(value.value, int)
                    and not isinstance(value.value, bool) and value.value > 0):
                return value.value
            sys.exit("channel.py: SEED in %s must be a positive number; set it to your "
                     "Canvas group number" % SEED_FILE)
    sys.exit("channel.py: %s has no 'SEED = <number>' line; set SEED to your Canvas "
             "group number" % SEED_FILE)


def decide(seed, profile, direction, seq, attempt):
    """Injection decisions for one datagram.

    A pure function of its arguments, never a running PRNG: two directions share
    one process, so anything consuming shared random state would depend on
    arrival order and stop being reproducible.

    The attempt term is what makes the protocol solvable. Without it the same
    sequence number draws the same decision forever, so a segment dropped once
    is dropped on every retransmission and the connection deadlocks.
    """
    digest = hashlib.sha256(b"%d|%s|%s|%d|%d" % (
        seed, profile.encode(), direction.encode(), seq, attempt)).digest()
    draw = [int.from_bytes(digest[i:i + 4], "big") / 2 ** 32 for i in range(0, 16, 4)]
    base, jitter, pLoss, pCorrupt, pDup = PROFILES[profile]
    return {
        "loss": draw[0] < pLoss,
        "corrupt": draw[1] < pCorrupt,
        "dup": draw[2] < pDup,
        "delay": max(0.0, base + (draw[3] - 0.5) * jitter),
        "bit": digest[16] % 8,
        "byte": digest[17],
    }


def keyOf(datagram):
    """The sequence number a decision is keyed on, or None if this is not a segment.

    Data and FIN segments are keyed on seqNum; anything carrying the ACK flag is
    keyed on ackNum, because that is the number identifying it.
    """
    if len(datagram) < HEADER_LEN:
        return None
    seqNum, ackNum, flags = struct.unpack(HEADER, datagram[:HEADER_LEN])[:3]
    return ackNum if flags & FLAG_ACK else seqNum


def damage(datagram, bit, bytePick):
    """Flip one bit, in the payload if there is one and in the header otherwise.

    A single-bit change always breaks the RFC 1071 checksum, so the receiver's
    verification is what detects it.
    """
    if len(datagram) > HEADER_LEN:
        index = HEADER_LEN + (bytePick % (len(datagram) - HEADER_LEN))
    else:
        index = bytePick % HEADER_LEN
    return datagram[:index] + bytes([datagram[index] ^ (1 << bit)]) + datagram[index + 1:]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="loss", choices=sorted(PROFILES))
    ap.add_argument("--seed", type=int, default=None,
                    help="instructor override; students set SEED in segment.py")
    ap.add_argument("--quiet", action="store_true", help="suppress the per-datagram log")
    ap.add_argument("--ports", default="%d,%d" % DEFAULT_PORTS,
                    help="sender,receiver ports. 0,0 asks the operating system for "
                         "any free pair, which every automated caller does")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    try:
        wanted = [int(p) for p in args.ports.split(",")]
        if len(wanted) != 2:
            raise ValueError
    except ValueError:
        ap.error("--ports takes two numbers separated by a comma, as in 51001,51002")

    seed = args.seed if args.seed is not None else student_seed()
    if args.seed is None:
        print("seed %d read from %s" % (seed, SEED_FILE), flush=True)

    # The two ports are fixed by default so that the commands printed in the
    # handout can be copied as they stand. Somebody else on a shared machine may
    # already hold them, and so may a channel of your own that did not exit, so
    # the failure has to say what to do about it rather than just refusing.
    sel = selectors.DefaultSelector()
    socks = {}
    for side, port in zip(("sender", "receiver"), wanted):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError as exc:
            sys.exit("channel.py cannot listen on port %d: %s.\n"
                     "Another channel of yours may still be running, or somebody "
                     "else on this machine holds it.\n"
                     "Pick another pair and pass it to all three programs, for "
                     "example:\n"
                     "    python3 channel.py --ports %d,%d --profile %s\n"
                     "then give rdtSender.py --channel %d and rdtReceiver.py "
                     "--channel %d."
                     % (port, exc.strerror, wanted[0] + 100, wanted[1] + 100,
                        args.profile, wanted[0] + 100, wanted[1] + 100))
        sock.setblocking(False)
        sel.register(sock, selectors.EVENT_READ, side)
        socks[side] = sock
        print("Listening on port %d side %s" % (sock.getsockname()[1], side), flush=True)

    def log(text):
        if not args.quiet:
            print(text, file=sys.stderr, flush=True)

    peer = {"sender": None, "receiver": None}   # where each side's datagrams came from
    seen = {}                                   # (direction, seq) -> transmissions so far
    linkFreeAt = {"d": 0.0, "a": 0.0}           # the link carries one datagram at a time
    depth = {"d": 0, "a": 0}                    # datagrams currently in flight
    pending = []                                # heap of (release, counter, side, datagram, direction)
    counter = 0

    while True:
        timeout = max(0.0, pending[0][0] - time.monotonic()) if pending else 0.05
        for key, _mask in sel.select(timeout=timeout):
            side = key.data
            try:
                datagram, addr = key.fileobj.recvfrom(65535)
            except BlockingIOError:
                continue
            peer[side] = addr
            if not datagram:
                # The receiver announces itself with one empty datagram. It is
                # never forwarded.
                log("receiver registered from %s:%d" % addr)
                continue

            direction = "d" if side == "sender" else "a"
            outSide = "receiver" if side == "sender" else "sender"
            label = "seq" if direction == "d" else "ack"
            if peer[outSide] is None:
                log("%s no receiver yet, %d bytes dropped" % (direction, len(datagram)))
                continue

            seq = keyOf(datagram)
            if seq is None:
                log("%s BADHDR %d bytes, forwarded anyway - your header is not %d bytes"
                    % (direction, len(datagram), HEADER_LEN))
                counter += 1
                depth[direction] += 1
                heapq.heappush(pending, (time.monotonic(), counter, outSide,
                                         datagram, direction))
                continue

            attempt = seen.get((direction, seq), 0)
            seen[(direction, seq)] = attempt + 1
            plan = decide(seed, args.profile, direction, seq, attempt)
            head = "%s %s %d attempt %d" % (direction, label, seq, attempt)

            if plan["loss"]:
                log("%s   DROP policy" % head)
                continue
            if depth[direction] >= QUEUE_LIMIT:
                # Not policy: this one is caused by the sender putting more into
                # the network than the link can drain.
                log("%s   DROP queue full" % head)
                continue

            out = datagram
            note = ""
            if plan["corrupt"]:
                out = damage(datagram, plan["bit"], plan["byte"])
                note = "CORRUPT  "

            # The link carries one datagram at a time, so a datagram arriving
            # while it is busy waits. That is what makes queuing delay emerge
            # rather than be invented, and what a too-large window runs into.
            now = time.monotonic()
            transmission = len(out) / LINK_RATE
            start = max(now, linkFreeAt[direction])
            finish = start + transmission
            linkFreeAt[direction] = finish
            counter += 1
            depth[direction] += 1
            heapq.heappush(pending, (finish + plan["delay"], counter, outSide, out, direction))

            if plan["dup"] and depth[direction] < QUEUE_LIMIT:
                dupFinish = linkFreeAt[direction] + transmission
                linkFreeAt[direction] = dupFinish
                counter += 1
                depth[direction] += 1
                heapq.heappush(pending, (dupFinish + plan["delay"], counter, outSide,
                                         out, direction))
                note += "DUP  "

            log("%s   %squeue %.1fms  tx %.1fms  prop %.1fms" % (
                head, note, (start - now) * 1000, transmission * 1000,
                plan["delay"] * 1000))

        now = time.monotonic()
        while pending and pending[0][0] <= now:
            _, _, outSide, datagram, direction = heapq.heappop(pending)
            depth[direction] -= 1
            try:
                socks[outSide].sendto(datagram, peer[outSide])
            except OSError:
                pass


if __name__ == "__main__":
    main()

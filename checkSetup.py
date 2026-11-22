# CMPT 371 Project 3 - setup check. Carries no marks and checks no answers.
"""Answers one question: can the grader run your submission at all?

Run it from inside your Rdt folder: python3 checkSetup.py

Before you start, neither protocol is implemented and this says so without
calling it a problem. Before you submit, both must run.
"""

import os
import re
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
STUDENT_FILES = ("segment.py", "rdtSender.py", "rdtReceiver.py")
GIVEN_FILES = ("channel.py", "config.py")
PROTOCOLS = ("gbn", "sr")
PAYLOAD = 500  # five segments: enough to exercise a window, quick enough to wait for
DEADLINE = 40  # seconds per protocol


def fail(message):
    print("setup PROBLEM: " + message)
    sys.exit(1)


def check_seed():
    """SEED must be set, and should match a student number on line 1."""
    source = open(os.path.join(HERE, "segment.py"), encoding="utf-8",
                  errors="replace").read()
    found = re.search(r"(?m)^SEED\s*=\s*([-+]?\d+)\s*$", source)
    if not found:
        fail("segment.py has no 'SEED = <number>' line; set SEED to your Canvas "
             "group number")
    seed = int(found.group(1))
    if seed <= 0:
        fail("SEED is still %d in segment.py; set it to your Canvas group number" % seed)
    return seed


def startChannel():
    proc = subprocess.Popen([sys.executable, "channel.py", "--profile", "clean",
                             "--ports", "0,0", "--quiet"], cwd=HERE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    ports = {}
    deadline = time.monotonic() + 15
    while len(ports) < 2 and time.monotonic() < deadline:
        line = proc.stdout.readline()
        if not line:
            break
        match = re.search(r"port (\d+) side (\w+)", line)
        if match:
            ports[match.group(2)] = int(match.group(1))
    if len(ports) < 2:
        proc.kill()
        out = (proc.stderr.read() or "").strip().splitlines()
        fail("channel.py did not start: %s" % (out[-1] if out else "no output"))
    return proc, ports


def pending(result):
    """The state an untouched skeleton is in, which is not a fault."""
    return result is not None and "NotImplementedError" in (result.stderr or "")


def runProtocol(protocol, source, target):
    """Return "works" or "pending"; call fail() on anything else."""
    channel, ports = startChannel()
    receiver = None
    try:
        receiver = subprocess.Popen(
            [sys.executable, "rdtReceiver.py", "--channel", str(ports["receiver"]),
             "--out", target, "--protocol", protocol], cwd=HERE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        time.sleep(0.3)
        try:
            sender = subprocess.run(
                [sys.executable, "rdtSender.py", "--channel", str(ports["sender"]),
                 "--in", source, "--protocol", protocol], cwd=HERE,
                capture_output=True, text=True, timeout=DEADLINE)
        except subprocess.TimeoutExpired:
            fail("rdtSender.py %s did not finish in %d seconds; it is most likely "
                 "waiting for something that will never arrive" % (protocol, DEADLINE))
        try:
            rOut, rErr = receiver.communicate(timeout=DEADLINE)
        except subprocess.TimeoutExpired:
            receiver.kill()
            rOut, rErr = receiver.communicate()
            fail("rdtReceiver.py %s did not exit in %d seconds after the sender "
                 "finished; check your teardown" % (protocol, DEADLINE))
        if pending(sender) or "NotImplementedError" in (rErr or ""):
            return "pending"
        if sender.returncode != 0:
            last = (sender.stderr.strip().splitlines() or ["(no output)"])[-1]
            fail("rdtSender.py %s exited %d: %s" % (protocol, sender.returncode, last[:160]))
        if receiver.returncode != 0:
            last = ((rErr or "").strip().splitlines() or ["(no output)"])[-1]
            fail("rdtReceiver.py %s exited %d: %s" % (protocol, receiver.returncode,
                                                      last[:160]))
        want = open(source, "rb").read()
        got = open(target, "rb").read() if os.path.exists(target) else b""
        if got != want:
            fail("rdtReceiver.py %s wrote %d bytes where %d were sent, and they do "
                 "not match. The setup is fine; the protocol is not."
                 % (protocol, len(got), len(want)))
        return "works"
    finally:
        channel.kill()
        if receiver and receiver.poll() is None:
            receiver.kill()


def main():
    if sys.version_info < (3, 11):
        fail("this is Python %d.%d; the project needs 3.11 or newer. Run it as python3."
             % sys.version_info[:2])

    for name in STUDENT_FILES + GIVEN_FILES:
        if not os.path.exists(os.path.join(HERE, name)):
            fail("no file named %s in %s (the name is case-sensitive)" % (name, HERE))

    for name in STUDENT_FILES:
        source = open(os.path.join(HERE, name), encoding="utf-8", errors="replace").read()
        if not source.lstrip().startswith("#"):
            fail("line 1 of %s is not a comment; it must hold both names and both "
                 "student numbers" % name)
        try:
            compile(source, name, "exec")
        except SyntaxError as exc:
            fail("%s does not compile: line %s, %s" % (name, exc.lineno, exc.msg))

    seed = check_seed()
    stated = re.findall(r"\b(\d{6,12})\b",
                        open(os.path.join(HERE, "segment.py"), encoding="utf-8",
                             errors="replace").read().splitlines()[0])
    if stated and seed in [int(s) for s in stated]:
        print("warning: SEED is %d, which is one of the student numbers on line 1. "
              "SEED is the Canvas group number for this project." % seed)

    work = tempfile.mkdtemp(prefix="checkSetup-", dir=HERE)
    source = os.path.join(work, "in.bin")
    open(source, "wb").write(bytes(range(256)) * (PAYLOAD // 256 + 1))
    verdict = {}
    try:
        for protocol in PROTOCOLS:
            verdict[protocol] = runProtocol(protocol, source,
                                            os.path.join(work, "out-%s.bin" % protocol))
    finally:
        for name in os.listdir(work):
            os.remove(os.path.join(work, name))
        os.rmdir(work)

    if os.path.basename(HERE) != "Rdt":
        print("warning: this folder is named %r, not 'Rdt'" % os.path.basename(HERE))
    if not os.path.exists(os.path.join(HERE, "readme.pdf")):
        print("warning: no readme.pdf here; that line scores 0 without it")

    waiting = [p for p in PROTOCOLS if verdict[p] == "pending"]
    print("setup OK")
    if waiting:
        print("not implemented yet: %s. That is expected before you start."
              % ", ".join(waiting))
        print("Run this again before you submit; both protocols must work by then.")


if __name__ == "__main__":
    main()

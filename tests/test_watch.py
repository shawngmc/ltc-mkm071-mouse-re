import socket

from fakedevice import FakeMouse
from mkm071 import Mkm071
from mkm071.descriptor import DONGLE_IF0_DESC, DONGLE_IF1_DESC
from mkm071.watch import find_event_hidraw, poll_wired, read_events


def fake_sysfs(tmp_path, nodes):
    for name, hid_id, desc in nodes:
        d = tmp_path / name / "device"
        d.mkdir(parents=True)
        (d / "uevent").write_text("DRIVER=hid-generic\nHID_ID=%s\nHID_NAME=x\n" % hid_id)
        (d / "report_descriptor").write_bytes(desc)
    return str(tmp_path)


def test_find_event_hidraw(tmp_path):
    root = fake_sysfs(tmp_path, [
        ("hidraw0", "0003:0000046D:0000C52B", DONGLE_IF1_DESC),        # someone else's receiver
        ("hidraw3", "0003:00003151:00004027", DONGLE_IF0_DESC),        # dongle boot mouse
        ("hidraw4", "0003:00003151:00004027", DONGLE_IF1_DESC),        # dongle keyboard + report 5
    ])
    assert find_event_hidraw(sysfs=root) == "/dev/hidraw4"
    assert find_event_hidraw(pid=0x4026, sysfs=root) is None


def test_read_events_one_report_per_read():
    # hidraw returns one report per read(); a SOCK_SEQPACKET pair keeps those boundaries
    r, w = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    with r, w:
        for chunk in ("050c0100", "07001e00000000", "050f0000", "05010200"):
            w.send(bytes.fromhex(chunk))
        w.shutdown(socket.SHUT_WR)
        evs = [(e.kind, e.value) for e in read_events(r.fileno(), seconds=2)]
    assert evs == [("dpi_stage", 1), ("indicator", 0), ("profile", 2)]   # keyboard report 7 ignored


def test_read_events_timeout():
    r, w = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    with r, w:
        assert list(read_events(r.fileno(), seconds=0.3)) == []


def test_poll_wired():
    m = FakeMouse()
    dev = Mkm071(m, wait=0)
    t = [0.0]

    def sleep(dt):                          # each poll: the user presses something
        t[0] += dt
        step = round(t[0] / dt)
        if step == 1:
            m.opt1[0][2] = 5                # DPI button: stage 4 -> 5
        elif step == 2:
            m.profile = 1                   # profile button: 0 -> 1 (its stage is 4)
    out = list(poll_wired(dev, interval=1, seconds=3.5, clock=lambda: t[0], sleep=sleep))
    assert out == [("dpi_stage", 4, 5), ("profile", 0, 1), ("dpi_stage", 5, 4)]
    assert {f[0] for f in m.sent} <= {0x85, 0xD4}

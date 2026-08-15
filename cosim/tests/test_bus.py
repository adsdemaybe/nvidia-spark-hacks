"""The bus, tested for the things that would let it lie.

Not "does a message arrive" — that fails loudly. These test the failures that are quiet:
a barrier that does not hold, a malformed message that vanishes without a trace, a
participant that stalls and is waited on forever.
"""

from __future__ import annotations

import threading
import time

import pytest

from cosim.bus import Proxy, Publisher, Subscriber
from cosim.schema import (
    TOPIC_MCU,
    TOPIC_MOTOR_STATE,
    TOPIC_PCB,
    McuOutput,
    MotorElectrical,
    MotorMechanical,
    MotorState,
    PcbPhysics,
    PinDrive,
)


@pytest.fixture
def proxy():
    p = Proxy(xsub_addr="tcp://127.0.0.1:5661", xpub_addr="tcp://127.0.0.1:5662").start()
    yield p
    p.stop()


def test_roundtrip(proxy):
    pub = Publisher(addr=proxy.xsub_addr, name="pcb")
    sub = Subscriber([TOPIC_PCB], addr=proxy.xpub_addr, name="mech")
    sub.wait_ready()

    pub.publish(
        TOPIC_PCB,
        PcbPhysics(
            t=0.0,
            seq=0,
            motors={
                "M1": MotorElectrical(
                    voltage_v=6.06,
                    current_avg_a=1.51,
                    current_peak_a=1.512,
                    torque_nm=0.0083,
                    output_torque_nm=0.83,
                )
            },
        ),
    )

    got = sub.await_seq(TOPIC_PCB, 0, timeout_s=3.0)
    assert isinstance(got, PcbPhysics)
    assert got.motors["M1"].current_avg_a == pytest.approx(1.51)
    assert sub.received[TOPIC_PCB] == 1
    assert sum(sub.dropped.values()) == 0
    pub.close()
    sub.close()


def test_malformed_is_dropped_and_counted(proxy):
    """A bad frame must be visible in the totals, not silently absent."""
    raw = Publisher(addr=proxy.xsub_addr, name="rogue")
    sub = Subscriber([TOPIC_PCB], addr=proxy.xpub_addr, name="mech")
    sub.wait_ready()

    # Bypass publish()'s type check the way a foreign participant would: send bytes.
    raw._sock.send_multipart([TOPIC_PCB.encode(), b'{"nope": true}'])
    time.sleep(0.2)

    assert sub.poll(timeout_ms=300) is None
    assert sub.dropped[TOPIC_PCB] == 1
    assert sub.drop_reasons, "a drop must record why"
    raw.close()
    sub.close()


def test_barrier_rejects_a_participant_that_runs_ahead(proxy):
    """Receiving a later period than the one awaited is a barrier violation.

    Accepting it silently is exactly how the two halves end up describing different
    instants while the trajectory still looks smooth.
    """
    pub = Publisher(addr=proxy.xsub_addr, name="mech")
    sub = Subscriber([TOPIC_MOTOR_STATE], addr=proxy.xpub_addr, name="clock")
    sub.wait_ready()

    pub.publish(
        TOPIC_MOTOR_STATE,
        MotorState(t=0.05, seq=5, motors={"M1": MotorMechanical(omega_rad_s=12.0, angle_rad=0.6)}),
    )

    with pytest.raises(RuntimeError, match="jumped to seq 5"):
        sub.await_seq(TOPIC_MOTOR_STATE, 0, timeout_s=2.0)
    pub.close()
    sub.close()


def test_stale_periods_are_skipped(proxy):
    """A message for a period already left is stale and must not satisfy the wait."""
    pub = Publisher(addr=proxy.xsub_addr, name="mcu")
    sub = Subscriber([TOPIC_MCU], addr=proxy.xpub_addr, name="clock")
    sub.wait_ready()

    for seq in (0, 1, 2):
        pub.publish(
            TOPIC_MCU,
            McuOutput(t=seq * 0.001, seq=seq, drives={"M1": PinDrive(duty=0.5, freq_hz=20000)}),
        )

    got = sub.await_seq(TOPIC_MCU, 2, timeout_s=3.0)
    assert got.seq == 2
    pub.close()
    sub.close()


def test_stall_times_out_and_names_the_topic(proxy):
    """A participant that never publishes must halt the run, not hang it forever."""
    sub = Subscriber([TOPIC_MOTOR_STATE], addr=proxy.xpub_addr, name="clock")
    sub.wait_ready()
    with pytest.raises(TimeoutError, match=TOPIC_MOTOR_STATE):
        sub.await_seq(TOPIC_MOTOR_STATE, 0, timeout_s=0.5)
    sub.close()


def test_two_participants_stay_in_lockstep(proxy):
    """The loopback the checklist asks for: a fake electrical and mechanical side.

    Each waits for the other before advancing. If the barrier were absent one would run
    to completion while the other was still starting, and the sequence numbers would not
    interleave.
    """
    periods = 12
    seen_by_mech: list[int] = []
    seen_by_elec: list[int] = []

    def mechanical() -> None:
        pub = Publisher(addr=proxy.xsub_addr, name="mech")
        sub = Subscriber([TOPIC_PCB], addr=proxy.xpub_addr, name="mech")
        sub.wait_ready()
        omega = 0.0
        for seq in range(periods):
            msg = sub.await_seq(TOPIC_PCB, seq, timeout_s=5.0)
            seen_by_mech.append(msg.seq)
            # A crude integrator: torque in, speed out. Enough to prove data flows both
            # ways, which is the point of the test.
            omega += msg.motors["M1"].output_torque_nm * 10.0
            pub.publish(
                TOPIC_MOTOR_STATE,
                MotorState(
                    t=seq * 0.001,
                    seq=seq,
                    motors={"M1": MotorMechanical(omega_rad_s=omega, angle_rad=omega * 0.001)},
                ),
            )
        pub.close()
        sub.close()

    thread = threading.Thread(target=mechanical, daemon=True)
    thread.start()

    pub = Publisher(addr=proxy.xsub_addr, name="elec")
    sub = Subscriber([TOPIC_MOTOR_STATE], addr=proxy.xpub_addr, name="elec")
    sub.wait_ready()

    for seq in range(periods):
        # Back-EMF stand-in: torque falls as the shaft speeds up.
        omega = seen_by_elec and seen_by_elec[-1] or 0.0
        torque = max(0.0, 0.05 - 0.0001 * omega)
        pub.publish(
            TOPIC_PCB,
            PcbPhysics(
                t=seq * 0.001,
                seq=seq,
                motors={
                    "M1": MotorElectrical(
                        voltage_v=7.4,
                        current_avg_a=torque / 0.0055,
                        current_peak_a=torque / 0.0055,
                        torque_nm=torque,
                        output_torque_nm=torque,
                    )
                },
            ),
        )
        state = sub.await_seq(TOPIC_MOTOR_STATE, seq, timeout_s=5.0)
        seen_by_elec.append(state.motors["M1"].omega_rad_s)

    thread.join(timeout=10.0)
    assert not thread.is_alive(), "the mechanical side did not finish"
    assert seen_by_mech == list(range(periods)), "periods must arrive in order, none skipped"
    assert len(seen_by_elec) == periods
    # The feedback must actually feed back: speed rises, and the torque that produced it
    # falls, which is the shape the real coupling has.
    assert seen_by_elec[-1] > seen_by_elec[0]
    pub.close()
    sub.close()

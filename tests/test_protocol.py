"""Protocol tests, with presses captured from real remotes."""

import pytest

from custom_components.ehc_led_panel.protocol import Command, Press, build, command_name, crc16, new_press, parse

# (advert data, press) captured from remotes 1A05 and C802, and from Home Assistant through an ESP32 proxy
CAPTURED = [
    ("0201021B05898AEE1BC878F64A0000481A05080500010000000000006E70E3", Press(0x1A05, 0, Command.POWER, 0x48, 0x6E)),
    ("0201021B05898AEE1BC878F64A0000461A05080501090000000000006465CD", Press(0x1A05, 1, Command.GROUP1, 0x46, 0x64)),
    ("0201021B05898AEE1BC878F64A0000471A05080501070000000000007A7D7A", Press(0x1A05, 1, Command.WARMER, 0x47, 0x7A)),
    ("0201021B05898AEE1BC878F64A0000331A0508050204000000000000D044EF", Press(0x1A05, 2, Command.BRIGHTER, 0x33, 0xD0)),
    ("0201021B05898AEE1BC878F64A00002FC802080501050000000000008B46E8", Press(0xC802, 1, Command.DIMMER, 0x2F, 0x8B)),
    ("0201021B05898AEE1BC878F64A000043C80208050008000000000000A32648", Press(0xC802, 0, Command.MAX2, 0x43, 0xA3)),
    ("0201021B05898AEE1BC878F64A0000141A0508050001000000000000EA3EA8", Press(0x1A05, 0, Command.POWER, 0x14, 0xEA)),
    # Sent by the ESP32 test build: night, then max
    ("0201021B05898AEE1BC878F64A0000101A0508050002000000000000110B98", Press(0x1A05, 0, Command.NIGHT, 0x10, 0x11)),
    ("0201021B05898AEE1BC878F64A0000111A0508050003000000000000224664", Press(0x1A05, 0, Command.MAX, 0x11, 0x22)),
]

# Received with bit errors (nRF Sniffer, bad radio CRC): must be rejected
DAMAGED = [
    "0201021B05898AEF1BC978F64A0000331A0508050204000000000400D044EF",  # header and a zero byte damaged
    "0201025B05898AEF5BC978F64A4000341A0408050204000000005400CF2866",  # remote ID damaged
    "0201021B05898AEE1BC878F64A00003F1A05080502040000000000007CF89D",  # CRC damaged
]


@pytest.mark.parametrize(("raw", "press"), CAPTURED)
def test_parse_and_build(raw: str, press: Press) -> None:
    """Captured presses parse, and build back byte for byte."""
    assert parse(bytes.fromhex(raw)) == press
    assert build(press) == bytes.fromhex(raw)


@pytest.mark.parametrize("raw", DAMAGED)
def test_damaged(raw: str) -> None:
    """Damaged adverts are rejected."""
    assert parse(bytes.fromhex(raw)) is None


def test_not_ours() -> None:
    """Other adverts, wrong length or wrong fixed bytes are rejected."""
    good = bytearray(build(Press(0x1A05, 1, Command.POWER, 1, 2)))
    assert parse(bytes.fromhex("1EFFD60502000600229FDA08C1627202645A00270102000000000000000005")) is None
    assert parse(bytes(good[:-1])) is None
    for pos in (13, 18, 19):  # prefix, ID suffix: rejected even with a matching CRC
        bad = bytearray(good)
        bad[pos] ^= 0x01
        bad[29:31] = crc16(bytes(bad[13:29])).to_bytes(2, "little")
        assert parse(bytes(bad)) is None


def test_new_press() -> None:
    """New presses round trip and get varying sequence numbers / seeds."""
    presses = [new_press(0xD604, 1, Command.DIMMER) for _ in range(20)]
    assert all(parse(build(p)) == p for p in presses)
    assert len({p.key for p in presses}) > 1
    assert str(presses[0]).startswith("remote D604 group 1 dimmer")


def test_command_name() -> None:
    """Command names."""
    assert command_name(0x01) == "power"
    assert command_name(0x0A) == "group2"
    assert command_name(0x0B) == "cmd_0b"

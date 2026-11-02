"""Advert format of the EHC LED panel remotes (LED Driver 50W-2.4G-CCT, model EHC_050S105DN_TP02).

Each press is a legacy non-connectable advert, 31 bytes of advert data:

    0-12   02 01 02 1B 05 89 8A EE 1B C8 78 F6 4A   flags, AD length / type, fixed header
    13-14  00 00                                     prefix
    15     sequence number, new for each press
    16-17  remote ID, as printed (e.g. 1A 05)
    18-19  08 05
    20     selected group: 0 none, 1 or 2
    21     command
    22-27  00 x 6
    28     seed, random per press
    29-30  CRC16 of bytes 13-28: poly 0x8408 (reflected), init 0xAAAA, little endian

The lights only accept it from the remote's fixed random address, sent on channel 37.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from enum import IntEnum

SENDER_ADDRESS = "66:55:44:33:22:11"  # as printed; on air 11 22 33 44 55 66
CHANNEL_MAP = 0x01  # channel 37 only
ADV_INTERVAL_MS = 20
TAP_DURATION_MS = 500  # a remote tap: one sequence number for about 0.5s
HOLD_STEP_MS = 210  # a remote hold: a new sequence number every ~0.21s

HEADER = bytes.fromhex("0201021B05898AEE1BC878F64A")
PREFIX = bytes([0x00, 0x00])
ID_SUFFIX = bytes([0x08, 0x05])
ADV_LEN = 31


class Command(IntEnum):
    """Remote commands (byte 21)."""

    POWER = 0x01  # toggle
    NIGHT = 0x02  # only while on
    MAX = 0x03  # max brightness and coldest, only while on
    BRIGHTER = 0x04
    DIMMER = 0x05
    COOLER = 0x06
    WARMER = 0x07
    MAX2 = 0x08  # same as MAX
    GROUP1 = 0x09  # selects group 1 and toggles its lights
    GROUP2 = 0x0A  # selects group 2


def command_name(command: int) -> str:
    """Name of a command, e.g. 'power', or 'cmd_0b' if unknown."""
    try:
        return Command(command).name.lower()
    except ValueError:
        return f"cmd_{command:02x}"


@dataclass(frozen=True)
class Press:
    """One remote press."""

    remote_id: int
    group: int
    command: int
    seq: int = 0
    seed: int = 0

    @property
    def key(self) -> tuple[int, int, int]:
        """Identify a press: repeats of it (other proxies, our own sends) have the same key."""
        return (self.remote_id, self.seq, self.seed)

    def __str__(self) -> str:
        return f"remote {self.remote_id:04X} group {self.group} {command_name(self.command)} (seq {self.seq:02X})"


def crc16(data: bytes, crc: int = 0xAAAA) -> int:
    """CRC16, poly 0x8408 (reflected 0x1021), no final xor."""
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
    return crc


def build(press: Press) -> bytes:
    """Build the advert data for a press."""
    body = PREFIX + bytes([press.seq & 0xFF]) + press.remote_id.to_bytes(2, "big") + ID_SUFFIX
    body += bytes([press.group, press.command]) + bytes(6) + bytes([press.seed & 0xFF])
    return HEADER + body + crc16(body).to_bytes(2, "little")


def parse(raw: bytes) -> Press | None:
    """Parse advert data, None if it isn't a valid press from these remotes."""
    if len(raw) != ADV_LEN or not raw.startswith(HEADER):
        return None
    body = raw[13:29]
    if body[:2] != PREFIX or body[5:7] != ID_SUFFIX or int.from_bytes(raw[29:31], "little") != crc16(body):
        return None
    return Press(remote_id=int.from_bytes(raw[16:18], "big"), group=raw[20], command=raw[21], seq=raw[15], seed=raw[28])


def new_press(remote_id: int, group: int, command: int) -> Press:
    """Make a press to send, with a random sequence number and seed (the lights accept any)."""
    return Press(remote_id, group, command, random.randint(0, 0xFF), random.randint(0, 0xFF))  # noqa: S311

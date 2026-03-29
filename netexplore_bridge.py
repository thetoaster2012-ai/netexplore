#!/usr/bin/env python3
"""NetExplore Bridge — PICO-8 ↔ Python communication via cartdata"""

CARTDATA_PATH = "/home/ciy/.lexaloffle/pico-8/cdata/netexplorebridge.p8d.txt"

TLIST = list(" ABCDEFGHIJKLMNOPQRSTUVWXYZ") + \
        list("abcdefghijklmnopqrstuvwxyz") + \
        list("1234567890~`!@#$%^&*()_-+={[}]|\\:;\"'<,>.?/")


def read_cartdata():
    """Read 64 slots from cartdata file"""
    with open(CARTDATA_PATH, "r") as f:
        data = f.read().replace("\n", "")
    slots = []
    for i in range(0, 512, 8):
        raw = int(data[i:i+8], 16)
        slots.append(raw // 0x10000)
    return slots


def write_cartdata(slots):
    """Write 64 slots to cartdata file"""
    lines = []
    for row in range(8):
        line = ""
        for col in range(8):
            idx = row * 8 + col
            if idx < len(slots):
                val = int(slots[idx]) * 0x10000
                line += f"{val & 0xffffffff:08x}"
            else:
                line += "00000000"
        lines.append(line)
    with open(CARTDATA_PATH, "w") as f:
        f.write("\n".join(lines) + "\n")


def decode_text(slots):
    """Decode cartdata slots into (cmd, text)"""
    # Slot 0: 5 digits FAABB
    s0 = f"{slots[0]:05d}"
    cmd = "fetch" if s0[0] == "1" else "data"
    a = int(s0[1:3])
    b = int(s0[3:5])
    text = ""
    text += TLIST[a] if a < len(TLIST) else "?"
    text += TLIST[b] if b < len(TLIST) else "?"

    # Slots 1-63: 4 digits AABB
    for i in range(1, 64):
        if slots[i] == 0:
            text += "  "
            continue
        s = f"{slots[i]:04d}"
        a = int(s[0:2])
        b = int(s[2:4])
        text += TLIST[a] if a < len(TLIST) else "?"
        text += TLIST[b] if b < len(TLIST) else "?"

    return cmd, text.rstrip()


def encode_text(text, cmd=2):
    """Encode text into cartdata slots"""
    chars = []
    for c in text:
        if c in TLIST:
            chars.append(TLIST.index(c))
        else:
            chars.append(0)  # space for unknown
    while len(chars) < 128:
        chars.append(0)

    # Slot 0: FAABB (F=cmd)
    slot0 = cmd * 10000 + chars[0] * 100 + chars[1]
    slots = [slot0]

    # Slots 1-63: AABB
    for i in range(1, 64):
        a = chars[i * 2]
        b = chars[i * 2 + 1]
        slots.append(a * 100 + b)
    return slots


def send(text, cmd=2):
    """Send text to PICO-8"""
    slots = encode_text(text, cmd)
    write_cartdata(slots)


def receive():
    """Read text from PICO-8"""
    slots = read_cartdata()
    return decode_text(slots)


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "send":
        text = " ".join(sys.argv[2:])
        send(text)
        print(f"Sent: '{text}'")
    elif len(sys.argv) > 1 and sys.argv[1] == "read":
        cmd, text = receive()
        print(f"Cmd: {cmd}")
        print(f"Text: '{text}'")
    else:
        print("Usage:")
        print("  python netexplore_bridge.py send <text>")
        print("  python netexplore_bridge.py read")

"""
DJI OSMO Gimbal BLE Test Script
Tests connection, service discovery, and basic communication.
"""
import asyncio
from bleak import BleakClient, BleakScanner

TARGET_NAME = "OMSE"

# BLE handles from packet capture
WRITE_HANDLE_UUID = None  # Will find by handle 0x0025
NOTIFY_HANDLE_1 = None    # Handle 0x0022 equivalent
NOTIFY_HANDLE_2 = None    # Handle 0x0014/0x0018 equivalents

# DJI DUML CRC-16 (CCITT)
CRC16_TABLE = []
def _init_crc16():
    for i in range(256):
        crc = i
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0x8408
            else:
                crc >>= 1
        CRC16_TABLE.append(crc & 0xFFFF)
_init_crc16()

def crc16(data):
    crc = 0x3692  # DJI DUML init value
    for b in data:
        crc = ((crc >> 8) & 0xFF) ^ CRC16_TABLE[(crc ^ b) & 0xFF]
    return crc & 0xFFFF

def crc16_header(data):
    """CRC for the 3-byte header (bytes 0-2)."""
    crc = 0x3692
    for b in data[:3]:
        crc = ((crc >> 8) & 0xFF) ^ CRC16_TABLE[(crc ^ b) & 0xFF]
    return crc & 0xFFFF


def build_duml(sender, receiver, seq, flags, cmd_set, cmd_id, payload=b""):
    """Build a complete DUML packet with CRC."""
    total_len = 11 + len(payload) + 2  # header(11) + payload + crc(2)

    # Bytes 0-2: SOF, length, version
    b0 = 0x55
    b1 = total_len & 0xFF
    b2 = ((total_len >> 8) & 0x03) | (1 << 2)  # version=1

    header = bytes([b0, b1, b2])
    hcrc = crc16_header(header)

    pkt = bytearray(header)
    pkt += hcrc.to_bytes(2, "little")
    pkt.append(sender)
    pkt.append(receiver)
    pkt += seq.to_bytes(2, "little")
    pkt.append(flags)
    pkt.append(cmd_set)
    pkt.append(cmd_id)
    pkt += payload

    pcrc = crc16(pkt)
    pkt += pcrc.to_bytes(2, "little")

    return bytes(pkt)


class DJIGimbalTest:
    def __init__(self):
        self.client = None
        self.write_uuid = None
        self.notify_uuids = []
        self.seq = 0x0001
        self.responses = []

    def next_seq(self):
        s = self.seq
        self.seq = (self.seq + 1) & 0xFFFF
        return s

    def notification_handler(self, sender, data):
        hex_str = data.hex()
        # Parse DUML header if possible
        if len(data) >= 11 and data[0] == 0x55:
            length = data[1] | ((data[2] & 0x03) << 8)
            snd = data[5]
            rcv = data[6]
            seq = int.from_bytes(data[7:9], "little")
            flags = data[9]
            cmd_set = data[10] if len(data) > 10 else "?"
            cmd_id = data[11] if len(data) > 11 else "?"
            flag_str = "RSP" if flags & 0x80 else "REQ"
            print(f"  <- NOTIFY [{flag_str}] len={length} snd=0x{snd:02X} rcv=0x{rcv:02X} "
                  f"seq=0x{seq:04X} set=0x{cmd_set:02X} id=0x{cmd_id:02X}")
            if len(data) > 12:
                payload = data[12:-2] if len(data) > 13 else data[12:]
                print(f"     payload: {payload.hex()}")
        else:
            print(f"  <- RAW: {hex_str[:60]}{'...' if len(hex_str) > 60 else ''}")
        self.responses.append(data)

    async def connect(self):
        print(f"Scanning for {TARGET_NAME}...")
        devices = await BleakScanner.discover(timeout=10)

        target = None
        for d in devices:
            if d.name and TARGET_NAME in d.name:
                target = d
                print(f"  Found: {d.name} ({d.address})")
                break

        if not target:
            print("Device not found!")
            return False

        print(f"Connecting to {target.name}...")
        self.client = BleakClient(target.address)
        await self.client.connect()

        if not self.client.is_connected:
            print("Failed to connect!")
            return False

        print(f"Connected! Paired: {self.client.is_connected}")

        # Discover services
        print("\n=== Services & Characteristics ===")
        write_candidates = []
        notify_candidates = []

        for service in self.client.services:
            print(f"\nService: {service.uuid}")
            for char in service.characteristics:
                props = ", ".join(char.properties)
                print(f"  {char.uuid} handle={char.handle} [{props}]")

                if "write-without-response" in char.properties:
                    write_candidates.append(char)
                if "notify" in char.properties:
                    notify_candidates.append(char)

        # Select characteristics
        if write_candidates:
            # Prefer handle closest to 0x0025 (37 decimal)
            write_candidates.sort(key=lambda c: abs(c.handle - 37))
            self.write_uuid = write_candidates[0].uuid
            print(f"\n>> Using WRITE: {self.write_uuid} (handle={write_candidates[0].handle})")

        # Enable notifications on all notify characteristics
        for char in notify_candidates:
            try:
                await self.client.start_notify(char.uuid, self.notification_handler)
                self.notify_uuids.append(char.uuid)
                print(f">> Enabled NOTIFY: {char.uuid} (handle={char.handle})")
            except Exception as e:
                print(f">> Failed notify on {char.uuid}: {e}")

        return True

    async def send(self, data, label=""):
        if not self.client or not self.write_uuid:
            print("Not connected!")
            return
        hex_str = data.hex()
        print(f"\n  -> SEND {label}: {hex_str[:60]}{'...' if len(hex_str) > 60 else ''}")
        try:
            await self.client.write_gatt_char(self.write_uuid, data, response=False)
        except Exception as e:
            print(f"     ERROR: {e}")

    async def send_duml(self, cmd_set, cmd_id, payload=b"", flags=0x40, label=""):
        """Build and send a DUML packet."""
        seq = self.next_seq()
        pkt = build_duml(0x02, 0x04, seq, flags, cmd_set, cmd_id, payload)
        await self.send(pkt, label or f"CmdSet=0x{cmd_set:02X} CmdID=0x{cmd_id:02X}")

    async def send_raw(self, hex_str, label=""):
        """Send exact hex bytes from capture."""
        data = bytes.fromhex(hex_str.replace(" ", ""))
        await self.send(data, label)

    async def test_connection(self):
        """Run connection test sequence."""
        print("\n" + "="*60)
        print("PHASE 1: Listening for gimbal heartbeat...")
        print("="*60)
        await asyncio.sleep(3)

        if self.responses:
            print(f"\nReceived {len(self.responses)} notifications in 3 seconds")
        else:
            print("\nNo notifications received! Gimbal may not be sending telemetry.")

        print("\n" + "="*60)
        print("PHASE 2: Sending test commands...")
        print("="*60)

        # Test 1: Send a keepalive/ping (Command Set 0x00, ID 0x02 - common DUML ping)
        self.responses.clear()
        await self.send_duml(0x00, 0x02, label="Ping (CmdSet 0x00)")
        await asyncio.sleep(0.5)

        # Test 2: Try the exact captured keepalive format
        # 550F 04A2 0227 XXXX 0000 0002 00 YY ZZ
        await self.send_raw("550F04A20227010000000200000A3D", label="Keepalive (capture format)")
        await asyncio.sleep(0.5)

        # Test 3: Send a short status query (captured format)
        await self.send_duml(0x04, 0x57, label="Status query (CmdSet 0x04)")
        await asyncio.sleep(0.5)

        # Test 4: Try movement command set
        await self.send_duml(0x23, 0x09, payload=bytes(38), label="Movement cmd (zeros)")
        await asyncio.sleep(1)

        print(f"\nTotal responses after tests: {len(self.responses)}")

        print("\n" + "="*60)
        print("PHASE 3: Sending captured raw commands...")
        print("="*60)

        # Send exact captured init-like commands (non-truncated ones)
        captured_cmds = [
            ("550E04660204010040041022A59F", "Status query (0x10/0x22)"),
            ("550E046602040200400410699970", "Status query (0x10/0x69)"),
            ("550F04A2020403004000001139C6", "Config query"),
        ]

        for hex_cmd, label in captured_cmds:
            self.responses.clear()
            await self.send_raw(hex_cmd, label)
            await asyncio.sleep(0.5)
            if self.responses:
                print(f"  Got {len(self.responses)} response(s)!")

    async def disconnect(self):
        if self.client:
            await self.client.disconnect()
            print("\nDisconnected.")


async def main():
    gimbal = DJIGimbalTest()

    if not await gimbal.connect():
        return

    try:
        await gimbal.test_connection()

        print("\n" + "="*60)
        print("Test complete. Press Ctrl+C to exit or wait 10s...")
        print("="*60)
        await asyncio.sleep(10)
    except KeyboardInterrupt:
        pass
    finally:
        await gimbal.disconnect()


if __name__ == "__main__":
    asyncio.run(main())

"""
DJI OSMO Gimbal BLE Control Script
Connects via BLE and controls gimbal movement by sending
auto-tracking coordinates (faking tracked object position).
"""
import asyncio
import struct
import pygame
from bleak import BleakClient, BleakScanner

TARGET_NAME = "OMSE"

# --- DJI DUML Protocol ---

# CRC-16 table (DJI variant, init=0x3692, poly=0x8408)
CRC16_TABLE = []
def _init_crc16():
    for i in range(256):
        crc = i
        for _ in range(8):
            crc = ((crc >> 1) ^ 0x8408) if (crc & 1) else (crc >> 1)
        CRC16_TABLE.append(crc & 0xFFFF)
_init_crc16()

def crc16(data):
    crc = 0x3692
    for b in data:
        crc = ((crc >> 8) & 0xFF) ^ CRC16_TABLE[(crc ^ b) & 0xFF]
    return crc & 0xFFFF


# Constant middle section of tracking payload (bytes 5-19)
# Extracted from captures - same across all tiltup/tiltdown packets
TRACKING_CONST = bytes.fromhex("4e06000005d002020401000200000000")

# Global state
client = None
write_uuid = None
seq_counter = 0xD300  # Starting sequence number (arbitrary)
last_encoder = 0xBCC6E3B4  # Default encoder position from captures


def notification_handler(sender, data):
    """Parse gimbal notifications to extract encoder position."""
    global last_encoder
    # Look for movement command ACKs or telemetry
    if len(data) > 11 and data[0] == 0x55:
        pass  # Could parse encoder from telemetry here


def build_tracking_packet(target_y=0.5, target_x=0.5, box_w=0.057, box_h=0.058):
    """Build a 49-byte DJI DUML tracking command.

    target_y: Vertical position of 'tracked object' in frame (0.0=top, 1.0=bottom, 0.5=center)
    target_x: Horizontal position (0.0=left, 1.0=right, 0.5=center)
    box_w: Width of tracking box (normalized, ~0.057 from captures)
    box_h: Height of tracking box (normalized, ~0.058 from captures)

    The gimbal moves to CENTER the 'tracked object', so:
    - target_y < 0.5 → gimbal tilts UP
    - target_y > 0.5 → gimbal tilts DOWN
    - target_x < 0.5 → gimbal pans LEFT
    - target_x > 0.5 → gimbal pans RIGHT
    """
    global seq_counter, last_encoder

    # DUML header
    sof = 0x55
    length = 49
    version = 1
    b1 = length & 0xFF
    b2 = ((length >> 8) & 0x03) | (version << 2)
    header = bytes([sof, b1, b2])
    hcrc = crc16(header)

    sender = 0x02  # App
    receiver = 0x04  # Gimbal
    seq = seq_counter
    seq_counter = (seq_counter + 1) & 0xFFFF
    flags = 0x40  # Request with ACK
    cmd_set = 0x23  # Tracking command set
    cmd_id = 0x09  # Movement command

    # Build payload (36 bytes)
    # Byte 0: checksum/counter byte (varies in captures, try 0x00)
    # Bytes 1-4: 32-bit LE encoder position
    # Bytes 5-19: constant config section
    # Bytes 20-23: float Y (target vertical position)
    # Bytes 24-27: float X (target horizontal position)
    # Bytes 28-31: float W (tracking box width)
    # Bytes 32-35: float H (tracking box height)

    payload = bytearray(36)
    payload[0] = 0x00  # Counter/checksum byte
    struct.pack_into("<I", payload, 1, last_encoder)
    payload[5:21] = TRACKING_CONST
    struct.pack_into("<f", payload, 20, target_y)
    struct.pack_into("<f", payload, 24, target_x)
    struct.pack_into("<f", payload, 28, box_w)
    struct.pack_into("<f", payload, 32, box_h)

    # Assemble full packet
    pkt = bytearray(header)
    pkt += struct.pack("<H", hcrc)
    pkt.append(sender)
    pkt.append(receiver)
    pkt += struct.pack("<H", seq)
    pkt.append(flags)
    pkt.append(cmd_set)
    pkt.append(cmd_id)
    pkt += payload

    # Packet CRC
    pcrc = crc16(pkt)
    pkt += struct.pack("<H", pcrc)

    return bytes(pkt)


async def connect():
    global client, write_uuid

    print("Scanning for DJI gimbal...")
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
    client = BleakClient(target.address)
    await client.connect()

    if not client.is_connected:
        print("Failed to connect!")
        return False

    # Find write-without-response characteristic
    print("Services:")
    for service in client.services:
        for char in service.characteristics:
            props = ", ".join(char.properties)
            print(f"  {char.uuid} handle={char.handle} [{props}]")
            if "write-without-response" in char.properties and write_uuid is None:
                write_uuid = char.uuid
            if "notify" in char.properties:
                try:
                    await client.start_notify(char.uuid, notification_handler)
                except:
                    pass

    print(f"Write UUID: {write_uuid}")
    print("Connected!")
    return True


async def send_tracking(target_y=0.5, target_x=0.5):
    """Send a single tracking command."""
    if not client or not write_uuid:
        return
    pkt = build_tracking_packet(target_y, target_x)
    await client.write_gatt_char(write_uuid, pkt, response=False)


# --- Pygame GUI ---
pygame.init()
screen = pygame.display.set_mode((500, 500))
pygame.display.set_caption("DJI OSMO Controller")
font = pygame.font.SysFont(None, 24)

joystick_center = (300, 300)
radius = 120


async def main():
    running = True
    connected = False

    while running:
        screen.fill((30, 30, 30))

        # Status
        status = "CONNECTED" if connected else "DISCONNECTED"
        color = (0, 200, 0) if connected else (200, 0, 0)
        screen.blit(font.render(status, True, color), (20, 20))

        # Connect button
        btn_connect = pygame.Rect(20, 50, 120, 40)
        pygame.draw.rect(screen, (0, 120, 80) if connected else (80, 80, 80), btn_connect)
        screen.blit(font.render("Connect", True, (255, 255, 255)), (35, 60))

        # Joystick
        pygame.draw.circle(screen, (60, 60, 60), joystick_center, radius, 2)
        pygame.draw.line(screen, (40, 40, 40), (joystick_center[0] - radius, joystick_center[1]),
                         (joystick_center[0] + radius, joystick_center[1]), 1)
        pygame.draw.line(screen, (40, 40, 40), (joystick_center[0], joystick_center[1] - radius),
                         (joystick_center[0], joystick_center[1] + radius), 1)

        # Labels
        screen.blit(font.render("Tilt Up", True, (150, 150, 150)),
                     (joystick_center[0] - 25, joystick_center[1] - radius - 25))
        screen.blit(font.render("Tilt Down", True, (150, 150, 150)),
                     (joystick_center[0] - 35, joystick_center[1] + radius + 10))
        screen.blit(font.render("Pan L", True, (150, 150, 150)),
                     (joystick_center[0] - radius - 50, joystick_center[1] - 10))
        screen.blit(font.render("Pan R", True, (150, 150, 150)),
                     (joystick_center[0] + radius + 10, joystick_center[1] - 10))

        mouse = pygame.mouse.get_pos()
        pressed = pygame.mouse.get_pressed()[0]

        target_y = 0.5
        target_x = 0.5

        if pressed and connected:
            dx = mouse[0] - joystick_center[0]
            dy = mouse[1] - joystick_center[1]
            dx = max(-radius, min(radius, dx))
            dy = max(-radius, min(radius, dy))

            pygame.draw.circle(screen, (0, 200, 255),
                               (joystick_center[0] + dx, joystick_center[1] + dy), 12)

            # Map joystick to tracking coordinates
            # Joystick right → object to the right → X > 0.5
            # Joystick up → object above → Y < 0.5
            target_x = 0.5 + (dx / radius) * 0.3  # ±0.3 from center
            target_y = 0.5 + (dy / radius) * 0.3  # ±0.3 from center

            await send_tracking(target_y, target_x)

        # Debug info
        screen.blit(font.render(f"Target Y={target_y:.3f}  X={target_x:.3f}",
                                True, (200, 200, 200)), (20, 460))

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            if event.type == pygame.MOUSEBUTTONDOWN:
                if btn_connect.collidepoint(event.pos):
                    connected = await connect()

        pygame.display.flip()
        await asyncio.sleep(0.04)  # ~25 Hz (matching capture rate)

    if client:
        await client.disconnect()


asyncio.run(main())

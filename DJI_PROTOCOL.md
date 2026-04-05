# DJI OSMO Gimbal BLE Protocol Documentation

Reverse-engineered from Bluetooth HCI packet captures between the DJI Mimo app (iOS) and a DJI OSMO gimbal during auto-tracking.

**Device:** OMSE-A03H91  
**UUID:** DC61026E-79C5-2E37-CFA8-3364475B534F  
**Protocol:** DJI DUML (Unified Messaging Layer) over BLE

---

## 1. BLE Connection Details

| Item | Value |
|------|-------|
| Connection Handle | 0x005E |
| Write Characteristic | Handle **0x0025** (all commands) |
| Notification Characteristic | Handle **0x0022** (all responses/telemetry) |
| CCCD Enable Notifications | Handle 0x0014 + Handle 0x0018 (write `0100`) |
| Connection Interval | 15ms min/max |
| Encryption | LE Encryption enabled (SMP pairing required) |

**Important:** The DJI gimbal requires BLE pairing with encryption (SMP Pairing Confirm + Random exchange). This is different from the Zhiyun which works without pairing.

---

## 2. DJI DUML Protocol Format

All packets start with `0x55` and follow the DUML (DJI Unified Messaging Layer) format:

```
Byte:  0    1    2    3-4    5    6    7-8      9      10     11    12..N-2  N-1,N
     [55] [LL] [VV] [CRC16] [SND] [RCV] [SEQ_LE] [FLAGS] [CMD_SET] [CMD_ID] [PAYLOAD] [CRC16]
      SOF  Len  Ver  Header  Send  Recv  SeqNum   Type    CmdSet   CmdID    Data      Packet
```

### Field Details

| Field | Bytes | Description |
|-------|-------|-------------|
| SOF | 0 | Always `0x55` |
| Length | 1-2 | `byte1 \| ((byte2 & 0x03) << 8)` = total packet length |
| Version | 2 | `byte2 >> 2` (always 1 in captures) |
| Header CRC | 3-4 | CRC-16 (CCITT) of bytes 0-2 |
| Sender | 5 | Device ID of sender (0x02 = App, 0x04 = Gimbal) |
| Receiver | 6 | Device ID of receiver |
| Sequence | 7-8 | 16-bit sequence number (little-endian), increments per packet |
| Flags | 9 | `0x40` = Request (ACK required), `0x80` = Response |
| Command Set | 10 | Command category |
| Command ID | 11 | Specific command within set |
| Payload | 12..N-2 | Command-specific data |
| Packet CRC | N-1, N | CRC-16 of entire packet |

### Device IDs

| ID | Device |
|----|--------|
| 0x02 | DJI App (iPhone) |
| 0x04 | Gimbal |

---

## 3. Command Types Observed

### 3.1 Movement/Tracking Command (most frequent)

```
55 31 04 53 02 04 [seq] 40 23 09 [38-byte payload]
```

| Field | Value | Meaning |
|-------|-------|---------|
| Length | 0x31 (49 bytes) | Full DUML packet |
| Sender | 0x02 (App) | |
| Receiver | 0x04 (Gimbal) | |
| Flags | 0x40 | Request with ACK |
| Command Set | 0x23 (35) | Tracking/movement control |
| Command ID | 0x09 | Movement command |

**Frequency:** Sent every ~40ms (~25 Hz) during auto-tracking.

**Payload Analysis** (first 7 visible bytes of 38-byte payload):

```
Byte 0:    Variable (possibly sub-command or checksum)
Bytes 1-2: 16-bit angle/position (little-endian), incrementing as gimbal moves
Bytes 3-4: Direction constant (see table below)
Bytes 5+:  Truncated in capture (PacketLogger limit)
```

**Direction Constants (bytes 3-4 of payload):**

| Direction | Constant | Angle Trend |
|-----------|----------|-------------|
| Tilt Up | `C6 BC` | Position value increases |
| Tilt Down | `C8 BC` | Position value increases |
| Pan Left | `C4 BC` | Position value increases |
| Pan Right | `C3 BC` | Position value increases |

**Position Value Examples (16-bit LE from payload bytes 1-2):**

| Direction | Start | End | Range |
|-----------|-------|-----|-------|
| Tilt Up | 0xE3B4 | 0xF9xx | ~5500 units over 7 sec |
| Tilt Down | 0x13D0 | 0x29xx | ~5500 units over 7 sec |
| Pan Left | 0xDD6C | 0xF8xx | ~7000 units over 8 sec |
| Pan Right | 0x8545 | 0x9Exx | ~6400 units over 8 sec |

**Note:** These are absolute encoder positions, not relative speeds. The auto-tracker sends target positions and the gimbal moves to them. The position value increments at ~100-370 units per 40ms step.

### 3.2 Gimbal Response to Movement

```
55 0E 04 66 04 02 [seq_echo] 80 23 09 00 [CRC16]
```

The gimbal echoes back the sequence number with:
- Sender/Receiver swapped (0x04 → 0x02)
- Flags = 0x80 (Response)
- Same Command Set/ID (0x23/0x09)
- Payload = `00` (success/ACK)

### 3.3 Heartbeat / Telemetry (from gimbal)

```
55 13 04 03 27 02 [seq] 40 04 57 00 0000 0001…
```

| Field | Value | Meaning |
|-------|-------|---------|
| Length | 0x13 (19 bytes) | |
| Sender | 0x27 | Internal gimbal subsystem? |
| Receiver | 0x02 (App) | |
| Command Set | 0x04 | |
| Command ID | 0x57 | Heartbeat/status |

Sent every ~45ms continuously while connected. Contains gimbal status data.

### 3.4 Other Periodic Commands

| Prefix | Length | Approx. Frequency | Purpose |
|--------|--------|-------------------|---------|
| `55 17 04 38` | 23 bytes | Every ~1 sec | Config/status poll (CmdSet 0x68, CmdID 0x32) |
| `55 14 04 6D` | 20 bytes | Every ~1.5 sec | Status query |
| `55 0F 04 A2` | 15 bytes | Every ~0.5 sec | Keepalive/ping |
| `55 10 04 56` | 16 bytes | Every ~1.5 sec | Status/config |
| `55 0E 04 66` | 14 bytes | Mixed | Short command/response |
| `55 12 04 C7` | 18 bytes | Rare | Config command |

---

## 4. Initialization Sequence (from connect.txt)

After BLE connection and SMP pairing, the app sends **120 commands** in rapid succession (~200ms total). Key phases:

### Phase 1: Enable Notifications
```
Write Request Handle:0x0014 Value: 0100    (enable notifications)
Write Request Handle:0x0018 Value: 0100    (enable notifications)
```

### Phase 2: Configuration Burst
120 Write Commands to Handle:0x0025, including:
- Multiple `5512 04C7` packets (18-byte config)
- Multiple `550E 0466` packets (14-byte status)
- Multiple `5518 0420` packets (24-byte config)
- Multiple `550F 04A2` packets (15-byte queries)
- Multiple `5516 04FC` packets (22-byte config)
- Multiple `5515 04A9` packets (21-byte config)
- Multiple `5514 046D` packets (20-byte status)
- Multiple `5517 0438` packets (23-byte config with `6832 0800 0080`)

### Phase 3: Repeating Config Pattern
After initial burst, a pattern repeats every ~1 second:
```
550E 0466 0204 XXXX 4004 1069 ...    (status query)
550E 0466 0204 XXXX 4004 1022 ...    (config query)
5517 0438 0204 XXXX 4004 6832 0800 0080...  (periodic config)
550E 0466 0204 XXXX 4004 1085 ...    (another query)
5517 0438 0204 XXXX 4004 6832 ...    (repeat)
5514 046D 0204 XXXX 00EE 0229 4A00 0080...  (status update)
5510 0456 0204 XXXX 4004 5001 0405 ...  (config)
550F 04A2 0227 XXXX 0000 0002 00...  (keepalive)
```

---

## 5. Key Differences from Zhiyun

| Feature | Zhiyun Crane | DJI OSMO |
|---------|-------------|----------|
| Protocol | Custom 7-byte commands | DUML (variable length, CRC-16) |
| Encryption | None | BLE SMP pairing required |
| Write Handle | 0x002C | 0x0025 |
| Notify Handle | 0x002F | 0x0022 |
| Movement Format | 3-command groups (tilt+roll+pan) | Single 49-byte tracking packet |
| Position Encoding | 32-bit value per axis | 16-bit encoder position |
| Update Rate | ~5 Hz (200ms) | ~25 Hz (40ms) |
| Complexity | Simple (3x7 bytes) | Complex (DUML with CRC) |

---

## 6. Limitations & Next Steps

### Truncation Issue
The PacketLogger export truncates packets longer than ~28 hex bytes with `…`. The main movement command (`5531`, 49 bytes) is truncated, meaning we can only see ~14 of the 38 payload bytes. **To get the full data, re-export using:**
- Wireshark with btsnoop format (shows full packets)
- `packetlogger` CLI tool on macOS
- Or use a BLE sniffer like nRF Sniffer that captures raw packets

### What We Still Need
1. **Full 49-byte movement packet payload** - to understand all axis data in a single packet
2. **Joystick control packets** - current captures are from auto-tracking (absolute positions). Joystick control may use a different command (potentially simpler speed/direction values)
3. **CRC-16 algorithm** - likely CRC-16/CCITT but needs verification
4. **Mode change commands** - lock/follow/pan-follow modes not captured

### Recommended Next Steps
1. Re-capture movement with a tool that doesn't truncate (Wireshark)
2. Capture joystick control (manual stick input) instead of auto-tracking
3. Capture mode changes (lock → follow → pan follow)
4. Test sending simple speed commands using Command Set 0x23, Command ID 0x09

---

## 7. Raw Data Reference

### Unique command patterns per recording:

**All movement files share these command types:**
| Command | Count (avg) | Purpose |
|---------|-------------|---------|
| `5531 0453` (49B) | ~160/recording | Movement/tracking |
| `550F 04A2` (15B) | ~15/recording | Keepalive/ping |
| `550E 0466` (14B) | ~11/recording | Short status |
| `5517 0438` (23B) | ~10/recording | Periodic config |
| `5514 046D` (20B) | ~5/recording | Status update |
| `5510 0456` (16B) | ~6/recording | Config query |

### Timing
| Recording | Time Range | Duration |
|-----------|-----------|----------|
| panright.txt | 22:55:01 - 22:55:08 | ~7 sec |
| panleft.txt | 22:55:23 - 22:55:31 | ~8 sec |
| tiltup.txt | 22:55:57 - 22:56:04 | ~7 sec |
| tiltdown.txt | 22:56:17 - 22:56:24 | ~7 sec |
| connect.txt | 22:54:09 - 22:54:14 | ~5 sec |

"""
IPC Protocol for ChzzkOverlay Process Communication

This module defines the communication protocol between the main process
and the overlay subprocess.
"""

import json
import struct
from enum import Enum, auto
from dataclasses import dataclass, asdict
from typing import Optional, Any

# TCP Port for IPC
IPC_PORT = 19847

# Shared Memory name for frame buffer
SHARED_MEMORY_NAME = "BCU_OverlayFrame"
FRAME_WIDTH = 1280
FRAME_HEIGHT = 1254
FRAME_CHANNELS = 4  # RGBA
FRAME_SIZE = FRAME_WIDTH * FRAME_HEIGHT * FRAME_CHANNELS


class CommandType(Enum):
    """Commands sent from main process to overlay"""
    SET_VOLUME = "set_volume"
    SET_ORIENTATION = "set_orientation"
    SET_ALIGNMENT = "set_alignment"
    SIMULATE_CLICK = "simulate_click"
    SIMULATE_SKIP = "simulate_skip"
    SIMULATE_KEY = "simulate_key"
    FORCE_CONNECT = "force_connect"
    FORCE_SKIP = "force_skip"
    SEEK_TO_START = "seek_to_start"
    TOGGLE_PLAY_PAUSE = "toggle_play_pause"
    REFRESH_PAGE = "refresh_page"
    MOVE_WINDOW = "move_window"
    SET_TASKBAR_VISIBLE = "set_taskbar_visible"
    GET_POSITION = "get_position"
    CLOSE = "close"
    PING = "ping"
    SET_PORTRAIT_SIZE = "set_portrait_size"
    SET_DONATION_TEXT_VISIBLE = "set_donation_text_visible"
    SET_SKIP_TIMER_ENABLED = "set_skip_timer_enabled"
    SET_INCLUDE_TEXT = "set_include_text"


class EventType(Enum):
    """Events sent from overlay to main process"""
    VIDEO_STARTED = "video_started"
    RESOLUTION_DETECTED = "resolution_detected"
    OVERLAY_CLOSED = "overlay_closed"
    POSITION_CHANGED = "position_changed"
    READY = "ready"
    PONG = "pong"


@dataclass
class IPCMessage:
    """Base IPC message structure"""
    msg_type: str  # CommandType or EventType value
    data: Optional[dict] = None
    
    def to_json(self) -> str:
        return json.dumps(asdict(self))
    
    @classmethod
    def from_json(cls, json_str: str) -> 'IPCMessage':
        data = json.loads(json_str)
        return cls(**data)
    
    def to_bytes(self) -> bytes:
        """Encode message with length prefix for TCP"""
        json_bytes = self.to_json().encode('utf-8')
        length = len(json_bytes)
        return struct.pack('>I', length) + json_bytes
    
    @staticmethod
    def read_from_socket(sock) -> Optional['IPCMessage']:
        """Read a complete message from socket"""
        # Read length prefix (4 bytes)
        length_data = b''
        while len(length_data) < 4:
            chunk = sock.recv(4 - len(length_data))
            if not chunk:
                return None
            length_data += chunk
        
        length = struct.unpack('>I', length_data)[0]
        
        # Read message data
        msg_data = b''
        while len(msg_data) < length:
            chunk = sock.recv(min(4096, length - len(msg_data)))
            if not chunk:
                return None
            msg_data += chunk
        
        return IPCMessage.from_json(msg_data.decode('utf-8'))


# Helper functions for creating messages

def cmd_set_volume(volume: int) -> IPCMessage:
    return IPCMessage(CommandType.SET_VOLUME.value, {"volume": volume})

def cmd_set_orientation(is_portrait: bool) -> IPCMessage:
    return IPCMessage(CommandType.SET_ORIENTATION.value, {"is_portrait": is_portrait})

def cmd_set_alignment(alignment: str) -> IPCMessage:
    return IPCMessage(CommandType.SET_ALIGNMENT.value, {"alignment": alignment})

def cmd_simulate_click(x: int, y: int) -> IPCMessage:
    return IPCMessage(CommandType.SIMULATE_CLICK.value, {"x": x, "y": y})

def cmd_simulate_skip() -> IPCMessage:
    return IPCMessage(CommandType.SIMULATE_SKIP.value)

def cmd_refresh_page(url: str, is_ui: bool) -> IPCMessage:
    return IPCMessage(CommandType.REFRESH_PAGE.value, {"url": url, "is_ui": is_ui})

def cmd_simulate_key(key: str) -> IPCMessage:
    return IPCMessage(CommandType.SIMULATE_KEY.value, {"key": key})

def cmd_force_connect() -> IPCMessage:
    return IPCMessage(CommandType.FORCE_CONNECT.value)

def cmd_force_skip() -> IPCMessage:
    return IPCMessage(CommandType.FORCE_SKIP.value)

def cmd_seek_to_start() -> IPCMessage:
    return IPCMessage(CommandType.SEEK_TO_START.value)

def cmd_toggle_play_pause() -> IPCMessage:
    return IPCMessage(CommandType.TOGGLE_PLAY_PAUSE.value)

def cmd_close() -> IPCMessage:
    return IPCMessage(CommandType.CLOSE.value)

def cmd_ping() -> IPCMessage:
    return IPCMessage(CommandType.PING.value)

def cmd_move_window(x: int, y: int) -> IPCMessage:
    return IPCMessage(CommandType.MOVE_WINDOW.value, {"x": x, "y": y})

def cmd_set_taskbar_visible(visible: bool) -> IPCMessage:
    return IPCMessage(CommandType.SET_TASKBAR_VISIBLE.value, {"visible": visible})

def cmd_get_position() -> IPCMessage:
    return IPCMessage(CommandType.GET_POSITION.value)

def evt_video_started(url: str) -> IPCMessage:
    return IPCMessage(EventType.VIDEO_STARTED.value, {"url": url})

def evt_resolution_detected(res_type: str) -> IPCMessage:
    return IPCMessage(EventType.RESOLUTION_DETECTED.value, {"type": res_type})

def evt_overlay_closed() -> IPCMessage:
    return IPCMessage(EventType.OVERLAY_CLOSED.value)

def evt_position_changed(x: int, y: int) -> IPCMessage:
    return IPCMessage(EventType.POSITION_CHANGED.value, {"x": x, "y": y})

def evt_ready() -> IPCMessage:
    return IPCMessage(EventType.READY.value)

def evt_pong() -> IPCMessage:
    return IPCMessage(EventType.PONG.value)

def cmd_set_portrait_size(width: int, height: int = None) -> IPCMessage:
    if height is None:
        height = int(width * 1024 / 576)
    return IPCMessage(CommandType.SET_PORTRAIT_SIZE.value, {"width": width, "height": height})

def cmd_set_donation_text_visible(visible: bool) -> IPCMessage:
    return IPCMessage(CommandType.SET_DONATION_TEXT_VISIBLE.value, {"visible": visible})

def cmd_set_skip_timer_enabled(enabled: bool) -> IPCMessage:
    return IPCMessage(CommandType.SET_SKIP_TIMER_ENABLED.value, {"enabled": enabled})

def cmd_set_include_text(include_text: bool) -> IPCMessage:
    return IPCMessage(CommandType.SET_INCLUDE_TEXT.value, {"include_text": include_text})

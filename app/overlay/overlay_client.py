"""
Overlay Client for Main Process

This module manages the overlay subprocess and provides an interface
for the main application to control the overlay.
"""

import os
import sys
import socket
import subprocess
import threading
import struct
from multiprocessing import shared_memory
from typing import Optional, Callable

from PyQt6.QtCore import QObject, pyqtSignal, QTimer
from PyQt6.QtGui import QImage, QPixmap

from app.overlay.ipc_protocol import (
    IPC_PORT, SHARED_MEMORY_NAME, FRAME_WIDTH, FRAME_HEIGHT, FRAME_CHANNELS, FRAME_SIZE,
    CommandType, EventType, IPCMessage,
    cmd_set_volume, cmd_set_orientation, cmd_set_alignment,
    cmd_simulate_click, cmd_simulate_skip, cmd_simulate_key, cmd_refresh_page, 
    cmd_move_window, cmd_set_taskbar_visible, cmd_get_position, cmd_close, cmd_ping
)


class OverlayClient(QObject):
    """
    Client for managing overlay subprocess.
    Provides interface matching ChzzkOverlay for seamless replacement.
    """
    
    # Signals matching ChzzkOverlay
    closed = pyqtSignal()
    video_started = pyqtSignal(str)
    resolution_detected = pyqtSignal(str)
    position_changed = pyqtSignal(int, int)
    ready = pyqtSignal()
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        self.process: Optional[subprocess.Popen] = None
        self.socket: Optional[socket.socket] = None
        self.shm: Optional[shared_memory.SharedMemory] = None
        
        self.is_portrait = False
        self.alignment = "center"
        self._overlay_pos = (0, 0)  # Track overlay window position
        
        self._running = False
        self._receive_thread: Optional[threading.Thread] = None
        
        # Reconnect timer
        self._reconnect_timer = QTimer(self)
        self._reconnect_timer.timeout.connect(self._try_connect)
        self._reconnect_timer.setInterval(500)
        
    def start(self, url: str, is_ui: bool = False, alignment: str = "center", disable_gpu: bool = False):
        """Start the overlay subprocess"""
        if self.process is not None:
            return
        
        self.alignment = alignment
        
        # Build args - handle both normal Python and PyInstaller frozen
        if getattr(sys, 'frozen', False):
            # Running in PyInstaller bundle - run same exe with --overlay flag
            args = [
                sys.executable,  # This is the frozen exe itself
                "--overlay",
                "--url", url,
                "--alignment", alignment,
                "--remote-debugging-port=9223"
            ]
        else:
            # Normal Python execution - run overlay_process.py script
            script_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "overlay_process.py"
            )
            args = [
                sys.executable,
                script_path,
                "--url", url,
                "--alignment", alignment,
                "--remote-debugging-port=9223"
            ]
        
        if is_ui:
            args.append("--ui")
        
        if disable_gpu:
            args.append("--disable-gpu")
            print("[OverlayClient] GPU acceleration disabled")
        
        # Start subprocess
        try:
            self.process = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,  # Enable text mode for easier reading
                bufsize=1,  # Line buffering
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0,
                encoding='utf-8',
                errors='replace'
            )
            print(f"[OverlayClient] Started subprocess PID: {self.process.pid}")
            print(f"[OverlayClient] Args: {' '.join(args)}")
            
            # Start threads to read stdout/stderr
            self._stdout_thread = threading.Thread(target=self._read_output, args=(self.process.stdout, "[Overlay:OUT]"))
            self._stdout_thread.daemon = True
            self._stdout_thread.start()
            
            self._stderr_thread = threading.Thread(target=self._read_output, args=(self.process.stderr, "[Overlay:ERR]"))
            self._stderr_thread.daemon = True
            self._stderr_thread.start()
            
            # Start trying to connect
            self._reconnect_timer.start()
            
        except Exception as e:
            print(f"[OverlayClient] Failed to start subprocess: {e}")
            self.process = None

    def _read_output(self, pipe, prefix):
        """Read output from pipe and print it"""
        try:
            for line in pipe:
                print(f"{prefix} {line.strip()}")
        except Exception:
            pass
    
    def _try_connect(self):
        """Try to connect to overlay IPC server"""
        if self.socket is not None:
            self._reconnect_timer.stop()
            return
        
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.settimeout(5.0)
            self.socket.connect(('127.0.0.1', IPC_PORT))
            self.socket.settimeout(None)
            
            print("[OverlayClient] Connected to overlay server")
            self._reconnect_timer.stop()
            
            # Start receive thread
            self._running = True
            self._receive_thread = threading.Thread(target=self._receive_loop, daemon=True)
            self._receive_thread.start()
            
            # Connect to shared memory
            self._connect_shm()
            
        except Exception as e:
            if self.socket:
                self.socket.close()
                self.socket = None
    
    def _connect_shm(self):
        """Connect to shared memory for frame capture"""
        # First close any existing connection
        if self.shm:
            try:
                self.shm.close()
            except Exception:
                pass
            self.shm = None
        
        try:
            self.shm = shared_memory.SharedMemory(name=SHARED_MEMORY_NAME)
            print("[OverlayClient] Connected to shared memory")
        except Exception as e:
            print(f"[OverlayClient] Shared memory not ready: {e}")
            # Retry after delay
            QTimer.singleShot(500, self._connect_shm)
    
    def _receive_loop(self):
        """Receive events from overlay subprocess"""
        while self._running and self.socket:
            try:
                msg = IPCMessage.read_from_socket(self.socket)
                if not msg:
                    break
                self._handle_event(msg)
            except Exception as e:
                print(f"[OverlayClient] Receive error: {e}")
                break
        
        self._running = False
        print("[OverlayClient] Receive loop ended")
        
        # Emit closed signal on main thread
        self.closed.emit()
    
    def _handle_event(self, msg: IPCMessage):
        """Handle event from overlay"""
        evt = msg.msg_type
        data = msg.data or {}
        
        if evt == EventType.VIDEO_STARTED.value:
            self.video_started.emit(data.get("url", ""))
        elif evt == EventType.RESOLUTION_DETECTED.value:
            self.resolution_detected.emit(data.get("type", "landscape"))
        elif evt == EventType.OVERLAY_CLOSED.value:
            self.closed.emit()
        elif evt == EventType.POSITION_CHANGED.value:
            x, y = data.get("x", 0), data.get("y", 0)
            self._overlay_pos = (x, y)
            self.position_changed.emit(x, y)
        elif evt == EventType.READY.value:
            self.ready.emit()
        elif evt == EventType.PONG.value:
            pass  # Connection alive
    
    def _send_command(self, msg: IPCMessage):
        """Send command to overlay"""
        if self.socket:
            try:
                self.socket.sendall(msg.to_bytes())
            except Exception as e:
                print(f"[OverlayClient] Send error: {e}")
    
    # === Public API matching ChzzkOverlay ===
    
    def set_volume(self, volume: int):
        """Set overlay volume (0-100)"""
        self._send_command(cmd_set_volume(volume))
    
    def set_orientation(self, is_portrait: bool):
        """Set portrait/landscape orientation"""
        self.is_portrait = is_portrait
        self._send_command(cmd_set_orientation(is_portrait))
    
    def toggle_orientation(self):
        """Toggle between portrait and landscape"""
        self.set_orientation(not self.is_portrait)
    
    def set_alignment(self, alignment: str):
        """Set alignment (left/center/right)"""
        self.alignment = alignment
        self._send_command(cmd_set_alignment(alignment))
    
    def simulate_click(self, x: int, y: int):
        """Simulate mouse click at coordinates"""
        self._send_command(cmd_simulate_click(x, y))
    
    def simulate_skip(self):
        """Simulate skip button click"""
        self._send_command(cmd_simulate_skip())
    
    def simulate_key(self, key: str):
        """Simulate keyboard key (home, end, space)"""
        self._send_command(cmd_simulate_key(key))
    
    def refresh_page(self, url: str = "", is_ui: bool = False):
        """Refresh overlay page"""
        self._send_command(cmd_refresh_page(url, is_ui))
    
    def grab(self) -> Optional[QPixmap]:
        """Capture current frame from shared memory"""
        if not self.shm:
            return None
        
        try:
            # Read metadata
            width = struct.unpack('>H', bytes(self.shm.buf[0:2]))[0]
            height = struct.unpack('>H', bytes(self.shm.buf[2:4]))[0]
            
            if width == 0 or height == 0:
                return None
            
            # Read frame data
            frame_size = width * height * FRAME_CHANNELS
            frame_data = bytes(self.shm.buf[8:8+frame_size])
            
            # Create QImage
            image = QImage(frame_data, width, height, width * FRAME_CHANNELS, QImage.Format.Format_RGBA8888)
            return QPixmap.fromImage(image.copy())  # Copy to detach from buffer
            
        except Exception as e:
            return None
    
    def isVisible(self) -> bool:
        """Check if overlay process is running"""
        return self.process is not None and self.process.poll() is None
    
    def close(self):
        """Close the overlay subprocess"""
        self._running = False
        
        # Send close command
        self._send_command(cmd_close())
        
        # Close socket
        if self.socket:
            try:
                self.socket.close()
            except Exception:
                pass
            self.socket = None
        
        # Close shared memory
        if self.shm:
            try:
                self.shm.close()
            except Exception:
                pass
            self.shm = None
        
        # Terminate process
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=2)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
            self.process = None
        
        self._reconnect_timer.stop()
        print("[OverlayClient] Closed")
    
    def show(self):
        """For compatibility - subprocess is already shown on start"""
        pass
    
    def raise_(self):
        """For compatibility"""
        pass
    
    def activateWindow(self):
        """For compatibility"""
        pass
    
    def move(self, x: int, y: int):
        """Move overlay window to specified position"""
        self._overlay_pos = (x, y)
        self._send_command(cmd_move_window(x, y))
    
    def pos(self):
        """Return current overlay window position (cached)"""
        class Point:
            def __init__(self, x, y):
                self._x = x
                self._y = y
            def x(self):
                return self._x
            def y(self):
                return self._y
        return Point(self._overlay_pos[0], self._overlay_pos[1])
    
    def set_taskbar_visible(self, visible: bool):
        """Show or hide overlay from taskbar"""
        self._send_command(cmd_set_taskbar_visible(visible))
    
    def request_position(self):
        """Request current position from overlay (async, result via position_changed signal)"""
        self._send_command(cmd_get_position())

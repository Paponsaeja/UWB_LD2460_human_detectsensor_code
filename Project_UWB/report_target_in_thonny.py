# -*- coding: utf-8 -*-
"""
LD2460 MicroPython Driver (Final with Memory Management)
- รวม driver เวอร์ชันล่าสุด + memory management จากเวอร์ชันแรก
"""

from machine import UART
import struct
import time
import gc

class LD2460:
    """Driver for Hi-Link LD2460 motion detection radar sensor"""
    
    # Frame markers (protocol constants)
    FRAME_HEADER = bytes([0xF4, 0xF3, 0xF2, 0xF1])
    FRAME_TAIL = bytes([0xF8, 0xF7, 0xF6, 0xF5])
    
    # Command frame markers
    CMD_HEADER = bytes([0xFD, 0xFC, 0xFB, 0xFA])
    CMD_TAIL = bytes([0x04, 0x03, 0x02, 0x01])
    
    # Function codes
    FUNC_TARGET_DATA = 0x04  # Radar reports target data
    FUNC_ENABLE_REPORT = 0x06  # Enable/disable auto reporting
    FUNC_SET_INSTALL_PARAMS = 0x07  # Set installation height/angle
    FUNC_GET_INSTALL_PARAMS = 0x08  # Query installation parameters
    FUNC_SET_INSTALL_MODE = 0x09  # Set side/top mount
    FUNC_GET_INSTALL_MODE = 0x0A  # Query installation mode
    FUNC_GET_VERSION = 0x0B  # Get firmware version
    FUNC_RESTART = 0x0D  # Restart radar
    FUNC_SET_BAUDRATE = 0x0E  # Set baud rate
    FUNC_FACTORY_RESET = 0x10  # Factory reset
    FUNC_SET_DETECTION_RANGE = 0x11  # Set detection range
    FUNC_GET_DETECTION_RANGE = 0x12  # Query detection range
    FUNC_SET_SENSITIVITY = 0x13  # Set sensitivity
    FUNC_GET_SENSITIVITY = 0x14  # Query sensitivity
    
    # Installation modes
    MODE_SIDE_MOUNT = 0x01
    MODE_TOP_MOUNT = 0x02
    
    # Baud rates
    BAUD_RATES = {
        0: 9600,
        1: 19200,
        2: 38400,
        3: 57600,
        4: 115200,
        5: 230400,
        6: 256000,
        7: 460800
    }
    
    # Buffer management constants
    MAX_BUFFER_SIZE = 1024  # Maximum buffer size to prevent memory issues
    MAX_FRAME_SIZE = 512    # Maximum expected frame size
    
    def __init__(self, uart_id=2, tx_pin=17, rx_pin=16, baudrate=115200):
        """Initialize the LD2460 sensor"""
        self.uart = UART(uart_id, baudrate=baudrate, tx=tx_pin, rx=rx_pin)
        self.buffer = bytearray()
        self.targets = []  # List to store detected targets
        self.auto_report_enabled = True  # Default is enabled
    
    def _send_command(self, func_code, data=None):
        """Send a command to the sensor"""
        packet = bytearray()
        packet.extend(self.CMD_HEADER)
        packet.append(func_code)
        
        if data is None:
            data = bytes([0x01])  # Default data for queries
        data_length = len(self.CMD_HEADER) + 1 + 2 + len(data) + len(self.CMD_TAIL)
        packet.extend(struct.pack('<H', data_length))  # Little-endian
        packet.extend(data)
        packet.extend(self.CMD_TAIL)
        
        self.uart.write(packet)
        
    def read_data(self, timeout=0.1):
        """
        Read and process data from the sensor 
        with memory management (จากเวอร์ชันแรก)
        """
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            if self.uart.any():
                # จำกัดขนาดการอ่าน ไม่เกิน 256 byte
                bytes_to_read = min(self.uart.any(), 256)
                new_data = self.uart.read(bytes_to_read)
                
                if new_data:
                    # ป้องกัน buffer ล้น
                    if len(self.buffer) + len(new_data) > self.MAX_BUFFER_SIZE:
                        keep_size = min(256, len(self.buffer) // 2)
                        self.buffer = self.buffer[-keep_size:]
                    
                    self.buffer.extend(new_data)
                    
                    frames_processed = 0
                    max_frames_per_read = 5
                    
                    while frames_processed < max_frames_per_read:
                        header_idx = self.buffer.find(self.FRAME_HEADER)
                        
                        if header_idx == -1:
                            if len(self.buffer) > 32:
                                self.buffer = self.buffer[-32:]
                            break
                        
                        if header_idx > 0:
                            self.buffer = self.buffer[header_idx:]
                        
                        if len(self.buffer) < 11:
                            break
                        
                        try:
                            func_code = self.buffer[4]
                            data_length = struct.unpack('<H', self.buffer[5:7])[0]
                            
                            if data_length > self.MAX_FRAME_SIZE or data_length < 11:
                                self.buffer = self.buffer[4:]
                                continue
                            
                            if len(self.buffer) >= data_length:
                                tail_start = data_length - 4
                                if self.buffer[tail_start:data_length] == self.FRAME_TAIL:
                                    data_start = 7
                                    data_end = tail_start
                                    data = self.buffer[data_start:data_end]
                                    
                                    if func_code == self.FUNC_TARGET_DATA:
                                        self.targets = self._parse_target_data(data) 
                                        self.targets = [None] * (len(data) // 4)
                                    
                                    self.buffer = self.buffer[data_length:]
                                    frames_processed += 1
                                    return True
                                else:
                                    self.buffer = self.buffer[4:]
                            else:
                                break
                        except (IndexError, struct.error):
                            self.buffer = self.buffer[4:]
                    
            time.sleep(0.001)
        
        if len(self.buffer) > self.MAX_BUFFER_SIZE // 2:
            self.buffer = bytearray()
        
        return False
        
    def get_targets(self, min_hits=4):
        """
        Return stable targets only
        - ghost target จะถูกตัดออก ถ้าไม่เจอซ้ำหลายครั้ง
        """
        if not hasattr(self, "_target_history"):
            self._target_history = []
        
        # เก็บจำนวน target ที่ตรวจพบ (0,1,2,...)
        self._target_history.append(len(self.targets))
        
        # keep ล่าสุด 5 ค่า
        self._target_history = self._target_history[-5:]
        
        # ถ้าเจอ target count > 0 ซ้ำอย่างน้อย min_hits ครั้ง → ยอมรับว่า "จริง"
        if self._target_history.count(0) < len(self._target_history) - min_hits:
            return self.targets.copy()
        
        return []

        
    def clear_buffer(self):
        self.buffer = bytearray()
    
    def enable_reporting(self, enable=True):
        data = bytes([0x01 if enable else 0x00])
        self._send_command(self.FUNC_ENABLE_REPORT, data)
        self.auto_report_enabled = enable
    
    def set_installation_params(self, height_m, angle_deg):
        height_cm = int(height_m * 100)
        angle_centideg = int(angle_deg * 100)
        data = struct.pack('<HH', height_cm, angle_centideg)
        self._send_command(self.FUNC_SET_INSTALL_PARAMS, data)
        
    def get_installation_params(self):
        self._send_command(self.FUNC_GET_INSTALL_PARAMS)
        time.sleep(0.1)
        if self.read_data(0.5):
            pass
        return None
        
    def set_install_mode(self, mode):
        data = bytes([mode])
        self._send_command(self.FUNC_SET_INSTALL_MODE, data)
        
    def set_detection_range(self, distance_m, start_angle_deg, end_angle_deg):
        distance_dm = int(distance_m * 10)
        start_angle = int(start_angle_deg * 10)
        end_angle = int(end_angle_deg * 10)
        data = struct.pack('<BHH', distance_dm, start_angle, end_angle)
        self._send_command(self.FUNC_SET_DETECTION_RANGE, data)
        
    def restart(self):
        self._send_command(self.FUNC_RESTART)
        time.sleep(2)
        self.clear_buffer()
        
    def factory_reset(self):
        self._send_command(self.FUNC_FACTORY_RESET)
        time.sleep(2)
        self.clear_buffer()
        
    def set_baudrate(self, baudrate):
        baud_idx = None
        for idx, rate in self.BAUD_RATES.items():
            if rate == baudrate:
                baud_idx = idx
                break
                
        if baud_idx is None:
            raise ValueError(f"Unsupported baud rate: {baudrate}")
            
        data = bytes([baud_idx])
        self._send_command(self.FUNC_SET_BAUDRATE, data)

    def calibrate(self):
        print("Starting sensor calibration...")
        
        # Disable auto reporting first
        print("  - Disabling auto reporting...")
        self.enable_reporting(False)
        time.sleep(0.5)
        
        # Clear buffer multiple times
        print("  - Clearing UART buffer...")
        for _ in range(5):
            self.clear_buffer()
            time.sleep(0.2)
        
        # Wait for hardware to stabilize (very important!)
        print("  - Waiting for hardware to stabilize...")
        time.sleep(5)  # Increased from 1s to 3s for better stability
        
        # Read and discard initial noise data
        print("  - Flushing noise data from UART...")
        for _ in range(5):
            self.uart.read()  # Read and discard any data in the UART buffer
            time.sleep(0.1)
        
        # Clear buffer one last time
        self.clear_buffer()
        
        # Enable reporting again
        print("  - Enabling auto reporting...")
        self.enable_reporting(True)
        time.sleep(0.5)
        
        print(" Calibration complete.")

def main():
    radar = LD2460()
    print("Initializing LD2460 Radar...")
    radar.calibrate()
    print("\nReading target data...\n")

    while True:
        if radar.read_data(timeout=0.2):
            targets = radar.targets
            if targets:
                print(f"Detected {len(targets)} target(s):")
                for i, (x, y) in enumerate(targets):
                    print(f"  Target {i+1}: X = {x}, Y = {y}")
            else:
                print("No target detected.")
        else:
            print("Waiting for data...")
        time.sleep(0.5)


# Run when this file executed directly
if __name__ == "__main__":
    main()

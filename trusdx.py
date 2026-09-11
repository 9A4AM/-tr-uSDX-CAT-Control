import sys
import os
import time
import configparser
import serial
import serial.tools.list_ports

from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QThread
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QPushButton,
    QComboBox, QLineEdit, QGroupBox, QGridLayout, QVBoxLayout,
    QHBoxLayout, QPlainTextEdit, QMessageBox, QSlider
)

import pyaudio
import numpy as np

# ============================================================
# CONFIG
# ============================================================

CONFIG_FILE = "trusdx.ini"
DEFAULT_BAUD = 38400

BANDS = {
    "80 m": {"freq": 3675000, "mode": "LSB"},
    "40 m": {"freq": 7123000, "mode": "LSB"},
    "20 m": {"freq": 14100000, "mode": "USB"},
    "15 m": {"freq": 21200000, "mode": "USB"},
    "10 m": {"freq": 28400000, "mode": "USB"}
}

MODES = {
    "LSB": 1,
    "USB": 2,
    "CW": 3,
    "FM": 4,
    "AM": 5
}

DEFAULT_FAVORITES = [
    3675000,
    3725000,
    3735000,
    3738000,
    7123000
]

# ============================================================
# AUDIO LOOPBACK THREAD
# ============================================================

class AudioLoopbackThread(QThread):
    def __init__(self, parent):
        super().__init__()
        self.parent = parent
        self.running = True

    def run(self):
        p = pyaudio.PyAudio()
        chunk = 1024
        FORMAT = pyaudio.paInt16
        CHANNELS = 1
        RATE = 44100

        stream = p.open(format=FORMAT,
                        channels=CHANNELS,
                        rate=RATE,
                        input=True,
                        output=True,
                        frames_per_buffer=chunk)

        while self.running:
            data = stream.read(chunk, exception_on_overflow=False)
            audio = np.frombuffer(data, dtype=np.int16).copy()

            if self.parent.loopback_muted:
                audio[:] = 0
            else:
                audio = (audio * self.parent.loopback_volume).astype(np.int16)

            stream.write(audio.tobytes())

        stream.stop_stream()
        stream.close()
        p.terminate()

    def stop(self):
        self.running = False

# ============================================================
# FREQUENCY DISPLAY WIDGET
# ============================================================

class FrequencyLabel(QLabel):
    wheelChanged = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta > 0:
            self.wheelChanged.emit(1)
        elif delta < 0:
            self.wheelChanged.emit(-1)
        event.accept()

    def mousePressEvent(self, event):
        self.setFocus()
        super().mousePressEvent(event)

# ============================================================
# MAIN WINDOW
# ============================================================

class TruSDXControl(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("(tr)uSDX CAT CONTROL V3.7 - 9A4AM")
        self.resize(1150, 780)

        self.serial = None
        self.frequency = 3675000
        self.current_mode = "LSB"
        self.radio_mode = "LSB"
        self.tuning_step = 1000

        self.band_buttons = {}
        self.favorite_buttons = []
        self.favorite_frequencies = []

        self.sync_in_progress = False

        self.cat_timer = QTimer()
        self.cat_timer.setSingleShot(True)
        self.cat_timer.timeout.connect(self.send_frequency)

        self.poll_timer = None

        self.loopback_thread = None
        self.audio_loopback_enabled = True
        self.loopback_volume = 0.5
        self.loopback_muted = False

        self.load_config()
        self.build_gui()
        self.showMaximized()

        self.refresh_ports()
        self.update_frequency_display()
        self.update_band_buttons()
        self.update_favorite_buttons()
        self.update_radio_status()

        self.setFocusPolicy(Qt.StrongFocus)
        self.installEventFilter(self)


    # ============================================================
    # CLOSE EVENT — stop audio and serial
    # ============================================================

    def closeEvent(self, event):
        try:
            if self.loopback_thread:
                self.loopback_thread.stop()
                self.loopback_thread.wait()
                self.loopback_thread = None

            if self.poll_timer:
                self.poll_timer.stop()
                self.poll_timer = None

            if self.serial and self.serial.is_open:
                self.serial.close()
        except Exception:
            pass

        event.accept()

    # ============================================================
    # LOAD CONFIG
    # ============================================================

    def load_config(self):
        self.config = configparser.ConfigParser()

        if os.path.exists(CONFIG_FILE):
            try:
                self.config.read(CONFIG_FILE)
                self.saved_port = self.config.get("CAT", "port", fallback="")
                self.saved_baud = self.config.getint("CAT", "baud", fallback=DEFAULT_BAUD)
                self.tuning_step = self.config.getint("CAT", "step", fallback=100)
            except Exception:
                self.saved_port = ""
                self.saved_baud = DEFAULT_BAUD
                self.tuning_step = 1000
        else:
            self.saved_port = ""
            self.saved_baud = DEFAULT_BAUD
            self.tuning_step = 1000

        self.favorite_frequencies = []
        for i in range(1, 5 + 1):
            default_value = DEFAULT_FAVORITES[i - 1]
            try:
                value = self.config.getint("FAVORITES", f"fav{i}", fallback=default_value)
            except Exception:
                value = default_value

            if value < 100000 or value > 60000000:
                value = default_value

            self.favorite_frequencies.append(value)

    # ============================================================
    # GUI
    # ============================================================

    def build_gui(self):

        central = QWidget()
        self.setCentralWidget(central)

        main = QVBoxLayout(central)

        self.setStyleSheet("""
            QWidget {
                background-color: #0f0f0f;
                color: #d4af37;
                font-family: 'Segoe UI';
            }
            QGroupBox {
                border: 1px solid #444444;
                margin-top: 10px;
                font-weight: bold;
                color: #d4af37;
            }
            QLabel {
                color: #d4af37;
            }
            QPushButton {
                background-color: #1c1c1c;
                border: 1px solid #444444;
                padding: 6px;
                border-radius: 6px;
                color: #d4af37;
            }
        """)

        main.setSpacing(6)

        title = QLabel("(tr)uSDX CAT CONTROL V3.7 - 9A4AM")
        title.setAlignment(Qt.AlignCenter)
        title.setFont(QFont("Segoe UI", 20, QFont.Bold))
        main.addWidget(title)

        # ====================================================
        # FREQUENCY + MODE (SIDE-BY-SIDE)
        # ====================================================
        freq_mode_group = QGroupBox("RADIO FREQUENCY & MODE")
        freq_mode_layout = QHBoxLayout(freq_mode_group)
        freq_mode_layout.setAlignment(Qt.AlignVCenter)

        self.freq_display = FrequencyLabel()
        self.freq_display.setMinimumHeight(120)
        self.freq_display.setMaximumHeight(260)
        self.freq_display.setFixedWidth(700)
        self.freq_display.setFont(QFont("Segoe UI", 64, QFont.Bold))
        self.freq_display.setStyleSheet("""
            QLabel {
                background-color: #151515;
                color: #00ff00;
                border: 2px solid #444444;
                border-radius: 10px;
                padding: 8px;
            }
        """)
        self.freq_display.setAlignment(Qt.AlignCenter)
        self.freq_display.wheelChanged.connect(self.mouse_tune)
        freq_mode_layout.addWidget(self.freq_display)

        self.mode_display = QLabel("Mode: LSB")
        self.mode_display.setAlignment(Qt.AlignCenter)
        self.mode_display.setFont(QFont("Segoe UI", 20, QFont.Bold))
        self.mode_display.setFixedWidth(200)
        self.mode_display.setMinimumHeight(60)
        self.mode_display.setStyleSheet("""
            QLabel {
                background-color: #1c1c1c;
                color: #00ff00;   /* ZELENA */
                border: 2px solid #444444;
                border-radius: 8px;
                padding: 0px;
            }
        """)

        freq_mode_layout.addWidget(self.mode_display)

        main.addWidget(freq_mode_group)

        # ====================================================
        # FREQUENCY CONTROLS
        # ====================================================
        control_layout = QHBoxLayout()

        self.minus_button = QPushButton("−")
        self.minus_button.setMinimumHeight(40)
        self.minus_button.setFont(QFont("Segoe UI", 16, QFont.Bold))
        self.minus_button.clicked.connect(lambda: self.tune(-self.tuning_step))

        self.plus_button = QPushButton("+")
        self.plus_button.setMinimumHeight(40)
        self.plus_button.setFont(QFont("Segoe UI", 16, QFont.Bold))
        self.plus_button.clicked.connect(lambda: self.tune(self.tuning_step))

        control_layout.addWidget(self.minus_button)
        control_layout.addStretch()

        step_label = QLabel("STEP:")
        step_label.setFont(QFont("Segoe UI", 12))

        self.step_combo = QComboBox()
        self.step_combo.addItems([
            "10 Hz", "50 Hz", "100 Hz", "500 Hz",
            "1 kHz", "5 kHz", "10 kHz"
        ])

        self.step_values = {
            "10 Hz": 10,
            "50 Hz": 50,
            "100 Hz": 100,
            "500 Hz": 500,
            "1 kHz": 1000,
            "5 kHz": 5000,
            "10 kHz": 10000
        }

        current_index = 2
        for i, text in enumerate(self.step_values):
            if self.step_values[text] == self.tuning_step:
                current_index = i

        self.step_combo.setCurrentIndex(current_index)
        self.step_combo.currentTextChanged.connect(self.step_changed)

        control_layout.addWidget(step_label)
        control_layout.addWidget(self.step_combo)
        control_layout.addStretch()
        control_layout.addWidget(self.plus_button)

        main.addLayout(control_layout)

        # FAVORITES
        favorite_group = QGroupBox("FAVORITE FREQUENCIES")
        favorite_layout = QHBoxLayout(favorite_group)

        for i in range(5):
            button = QPushButton()
            button.setMinimumHeight(42)
            button.setFont(QFont("Segoe UI", 11, QFont.Bold))
            button.clicked.connect(lambda checked=False, index=i: self.select_favorite(index))
            self.favorite_buttons.append(button)
            favorite_layout.addWidget(button)

        main.addWidget(favorite_group)

        # BAND SELECT
        band_group = QGroupBox("BAND SELECT")
        band_layout = QHBoxLayout(band_group)

        for band in ["80 m", "40 m", "20 m", "15 m", "10 m"]:
            button = QPushButton(band)
            button.setMinimumHeight(44)
            button.setFont(QFont("Segoe UI", 14, QFont.Bold))
            button.clicked.connect(lambda checked=False, b=band: self.select_band(b))
            self.band_buttons[band] = button
            band_layout.addWidget(button)

        main.addWidget(band_group)

        # RADIO CONTROL
        radio_group = QGroupBox("RADIO CONTROL")
        radio_layout = QGridLayout(radio_group)

        input_label = QLabel("SET MHz:")
        self.freq_input = QLineEdit()
        self.freq_input.setPlaceholderText("npr. 14.195000")
        self.freq_input.returnPressed.connect(self.set_frequency_from_input)

        set_button = QPushButton("SET")
        set_button.clicked.connect(self.set_frequency_from_input)

        radio_layout.addWidget(input_label, 1, 0)
        radio_layout.addWidget(self.freq_input, 1, 1)
        radio_layout.addWidget(set_button, 1, 2)

        main.addWidget(radio_group)

        # AUDIO CONTROLS
        volume_label = QLabel("Volume")
        volume_slider = QSlider(Qt.Horizontal)
        volume_slider.setRange(0, 100)
        volume_slider.setValue(int(self.loopback_volume * 100))
        volume_slider.valueChanged.connect(self.set_loopback_volume)

        main.addWidget(volume_label)
        main.addWidget(volume_slider)

        self.mute_button = QPushButton("MUTE")
        self.mute_button.setCheckable(True)
        self.mute_button.setMinimumHeight(40)
        self.mute_button.setFont(QFont("Segoe UI", 14, QFont.Bold))
        self.mute_button.setStyleSheet("""
            QPushButton {
                background-color: #222222;
                border: 2px solid #444444;
                border-radius: 8px;
                color: #d4af37;
            }
            QPushButton:checked {
                background-color: #aa4444;
                color: white;
                border: 2px solid #ff7777;
            }
        """)
        self.mute_button.clicked.connect(self.toggle_mute)
        main.addWidget(self.mute_button)

        # PTT
        self.ptt_button = QPushButton("PTT")
        self.ptt_button.setCheckable(True)
        self.ptt_button.setMinimumHeight(80)
        self.ptt_button.setMinimumWidth(160)
        self.ptt_button.setFont(QFont("Segoe UI", 22, QFont.Bold))
        self.ptt_button.setStyleSheet("""
            QPushButton {
                background-color: #222222;
                border: 2px solid #aa4444;
                border-radius: 12px;
                color: #d4af37;
                padding: 10px;
            }
            QPushButton:checked {
                background-color: #aa4444;
                color: white;
                border: 2px solid #ff7777;
            }
            QPushButton:hover {
                background-color: #333333;
            }
        """)
        self.ptt_button.clicked.connect(self.ptt_changed)
        radio_layout.addWidget(self.ptt_button, 0, 2)

        # MODE SELECT
        mode_select_group = QGroupBox("MODE SELECT")
        mode_select_layout = QHBoxLayout(mode_select_group)

        for mode in ["LSB", "USB", "CW", "FM", "AM"]:
            btn = QPushButton(mode)
            btn.setMinimumHeight(40)
            btn.setFont(QFont("Segoe UI", 12, QFont.Bold))
            btn.clicked.connect(lambda checked=False, m=mode: self.set_mode_button(m))
            mode_select_layout.addWidget(btn)

        main.addWidget(mode_select_group)

        # CAT CONNECTION
        connection_group = QGroupBox("CAT CONNECTION")
        connection_layout = QGridLayout(connection_group)

        connection_layout.addWidget(QLabel("COM PORT:"), 0, 0)
        self.port_combo = QComboBox()
        connection_layout.addWidget(self.port_combo, 0, 1)

        refresh_button = QPushButton("REFRESH")
        refresh_button.clicked.connect(self.refresh_ports)
        connection_layout.addWidget(refresh_button, 0, 2)

        connection_layout.addWidget(QLabel("BAUD:"), 1, 0)
        self.baud_combo = QComboBox()
        self.baud_combo.addItems(["38400", "115200", "19200", "9600"])
        self.baud_combo.setCurrentText(str(getattr(self, "saved_baud", DEFAULT_BAUD)))
        connection_layout.addWidget(self.baud_combo, 1, 1)

        self.connect_button = QPushButton("CONNECT")
        self.connect_button.setMinimumHeight(36)
        self.connect_button.clicked.connect(self.connect_serial)
        connection_layout.addWidget(self.connect_button, 1, 2)

        main.addWidget(connection_group)

        # CAT MONITOR
        monitor_group = QGroupBox("CAT MONITOR")
        monitor_layout = QVBoxLayout(monitor_group)

        self.monitor = QPlainTextEdit()
        self.monitor.setReadOnly(True)
        self.monitor.setMinimumHeight(95)
        monitor_layout.addWidget(self.monitor)

        main.addWidget(monitor_group)

        # STATUS BAR
        self.status_label = QLabel("Disconnected")
        self.status_label.setStyleSheet("color: #ff5555;")
        main.addWidget(self.status_label)


        central.setFocusPolicy(Qt.StrongFocus)


    # ============================================================
    # SIMPLE HELPERS
    # ============================================================

    def log(self, text):
        if hasattr(self, "monitor"):
            self.monitor.appendPlainText(text)

    def update_frequency_display(self):
        mhz = self.frequency / 1e6
        self.freq_display.setText(f"{mhz:0.6f} MHz")

    def update_radio_status(self):
        if self.current_mode:
            self.mode_display.setText(f"Mode: {self.current_mode}")
        else:
            self.mode_display.setText("Mode: ---")

    def update_band_buttons(self):
        for band, info in BANDS.items():
            btn = self.band_buttons.get(band)
            if not btn:
                continue
            if info["freq"] == self.frequency:
                btn.setStyleSheet("background-color: #333333; color: #d4af37;")
            else:
                btn.setStyleSheet("background-color: #1c1c1c; color: #d4af37;")

    def update_favorite_buttons(self):
        for i, freq in enumerate(self.favorite_frequencies):
            if i < len(self.favorite_buttons):
                mhz = freq / 1e6
                self.favorite_buttons[i].setText(f"{mhz:0.6f}")

    # ============================================================
    # TUNING
    # ============================================================

    def step_changed(self, text):
        self.tuning_step = self.step_values.get(text, 1000)

    def mouse_tune(self, direction):
        self.tune(direction * self.tuning_step)

    def tune(self, delta):
        self.cat_timer.stop()
        self.frequency += delta
        if self.frequency < 100000:
            self.frequency = 100000
        if self.frequency > 60000000:
            self.frequency = 60000000
        self.update_frequency_display()
        self.update_band_buttons()
        self.update_radio_status()
        self.send_frequency()

    def select_favorite(self, index):
        if 0 <= index < len(self.favorite_frequencies):
            self.cat_timer.stop()
            self.frequency = self.favorite_frequencies[index]
            self.update_frequency_display()
            self.update_band_buttons()
            self.update_radio_status()
            self.send_frequency()

    def select_band(self, band):
        self.cat_timer.stop()
        info = BANDS[band]
        self.frequency = info["freq"]
        mode = info["mode"]
        self.current_mode = mode
        self.update_frequency_display()
        self.update_band_buttons()
        self.update_radio_status()
        self.send_frequency()
        mode_code = MODES[mode]
        self.send_command(f"MD{mode_code};")
        self.cat_timer.start(300)

    def set_mode_button(self, mode_name):
        self.current_mode = mode_name
        self.update_radio_status()
        if self.serial and self.serial.is_open:
            mode_code = MODES.get(mode_name, None)
            if mode_code:
                cmd = f"MD{mode_code};"
                self.send_command(cmd)
                self.log(f"MODE -> {mode_name}")

    def set_frequency_from_input(self):
        text = self.freq_input.text().strip()
        try:
            mhz = float(text)
            freq = int(mhz * 1e6)
            if 100000 <= freq <= 60000000:
                self.cat_timer.stop()
                self.frequency = freq
                self.update_frequency_display()
                self.update_band_buttons()
                self.update_radio_status()
                self.send_frequency()
        except Exception:
            QMessageBox.warning(self, "Frequency", "Invalid frequency format.")

    # ============================================================
    # AUDIO
    # ============================================================

    def set_loopback_volume(self, value):
        self.loopback_volume = value / 100.0

    def toggle_mute(self):
        self.loopback_muted = self.mute_button.isChecked()

    # ============================================================
    # PORTS
    # ============================================================

    def refresh_ports(self):
        current = getattr(self, "saved_port", "")

        if hasattr(self, "port_combo"):
            current_gui = self.port_combo.currentText()
            if current_gui:
                current = current_gui

        self.port_combo.clear()
        ports = serial.tools.list_ports.comports()
        for port in ports:
            self.port_combo.addItem(port.device)

        if current:
            index = self.port_combo.findText(current)
            if index >= 0:
                self.port_combo.setCurrentIndex(index)

    # ============================================================
    # CONNECT / DISCONNECT
    # ============================================================

    def connect_serial(self):
        if self.serial and self.serial.is_open:
            try:
                if self.loopback_thread:
                    self.loopback_thread.stop()
                    self.loopback_thread.wait()
                    self.loopback_thread = None
                    self.log("AUDIO LOOPBACK STOPPED (DISCONNECT)")

                if self.poll_timer:
                    self.poll_timer.stop()
                    self.poll_timer = None
                    self.log("CAT POLLING STOPPED")

                self.serial.close()
            except Exception:
                pass

            self.serial = None
            self.connect_button.setText("CONNECT")
            self.status_label.setText("Disconnected")
            self.status_label.setStyleSheet("color: #ff5555;")
            self.log("DISCONNECTED")
            return

        port = self.port_combo.currentText()
        if not port:
            QMessageBox.warning(self, "CAT", "COM port is not selected.")
            return

        try:
            baud = int(self.baud_combo.currentText())
            self.serial = serial.Serial(
                port=port,
                baudrate=baud,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=0.10,
                write_timeout=0.5
            )

            self.serial.dtr = True
            self.serial.rts = False

            time.sleep(0.10)
            self.serial.reset_input_buffer()

            self.connect_button.setText("DISCONNECT")
            self.status_label.setText(f"Connected: {port} @ {baud}")
            self.status_label.setStyleSheet("color: #55ff55;")
            self.log(f"CONNECTED {port} @ {baud} baud")

            if self.audio_loopback_enabled and self.loopback_thread is None:
                self.loopback_thread = AudioLoopbackThread(self)
                self.loopback_thread.start()
                self.log("AUDIO LOOPBACK STARTED (THREAD)")

            if self.poll_timer is None:
                self.poll_timer = QTimer()
                self.poll_timer.timeout.connect(self.poll_radio)
                self.poll_timer.start(200)
                self.log("CAT POLLING STARTED")

            if not hasattr(self, "config"):
                self.config = configparser.ConfigParser()
            if "CAT" not in self.config:
                self.config["CAT"] = {}
            self.config["CAT"]["port"] = port
            self.config["CAT"]["baud"] = str(baud)
            self.config["CAT"]["step"] = str(self.tuning_step)
            try:
                with open(CONFIG_FILE, "w") as f:
                    self.config.write(f)
            except Exception:
                pass

            self.sync_in_progress = True

            self.log("SYNC: READ FREQUENCY + MODE")
            self.send_command("IF;")
            time.sleep(0.05)

            self.log("SYNC: READ MODE")
            self.serial.write(b"MD;")

            self.sync_in_progress = False
            self.log("SYNC: COMPLETE")

            self.send_command("MD1;")
            time.sleep(0.05)
            self.send_command("MD;")

        except Exception as e:
            self.serial = None
            self.connect_button.setText("CONNECT")
            self.status_label.setText("Disconnected")
            self.status_label.setStyleSheet("color: #ff5555;")
            QMessageBox.critical(self, "CAT ERROR", str(e))

    # ============================================================
    # POLL RADIO
    # ============================================================

    def poll_radio(self):
        if not self.serial or not self.serial.is_open:
            return
        if self.sync_in_progress:
            return

        try:
            self.serial.write(b"FA;")
            resp = self.serial.readline().decode().strip()
        except:
            return

        if resp.startswith("FA"):
            try:
                freq = int(resp[2:].replace(";", ""))
            except:
                return

            if freq != self.frequency:
                self.frequency = freq
                self.update_frequency_display()

    # ============================================================
    # CAT SEND COMMAND
    # ============================================================

    def send_command(self, command):
        if not self.serial or not self.serial.is_open:
            return

        try:
            self.serial.reset_input_buffer()
            self.serial.write(command.encode("ascii"))
            self.serial.flush()
            self.log("TX: " + command)

            response = b""
            deadline = time.time() + 1.0

            while time.time() < deadline:
                data = self.serial.read(1)
                if data:
                    response += data
                    if b";" in response:
                        break
                else:
                    time.sleep(0.005)

            if response:
                try:
                    text = response.decode("ascii", errors="replace")
                except Exception:
                    text = repr(response)

                self.log("RX: " + text)
                self.process_response(text)
            else:
                self.log("RX: <no response>")

        except Exception as e:
            self.log(f"CAT ERROR: {e}")

    def send_frequency(self):
        if not self.serial or not self.serial.is_open:
            return
        cmd = f"FA{self.frequency:011d};"
        self.send_command(cmd)

    # ============================================================
    # CAT RESPONSE PROCESSING
    # ============================================================

    def process_response(self, response):
        if self.sync_in_progress and response.startswith("MD"):
            return

        response = response.strip()
        records = response.split(";")

        for record in records:
            record = record.strip()
            if not record:
                continue

            if record.startswith("FA"):
                value = record[2:].strip()
                try:
                    freq = int(value)
                    if 100000 <= freq <= 60000000:
                        self.frequency = freq
                        self.update_frequency_display()
                        self.update_band_buttons()
                        self.update_radio_status()
                        self.log(f"GUI FREQUENCY <- {freq/1e6:.6f} MHz")
                except Exception:
                    self.log(f"Invalid FA response: {record}")
                continue

            if record.startswith("IF"):
                try:
                    value = record[2:13]
                    freq = int(value)
                    if 100000 <= freq <= 60000000:
                        self.frequency = freq
                        self.update_frequency_display()
                        self.update_band_buttons()
                        self.update_radio_status()
                        self.log(f"GUI FREQUENCY (IF) <- {freq/1e6:.6f} MHz")
                except Exception:
                    self.log(f"Invalid IF response: {record}")
                continue

            if record.startswith("MD"):
                try:
                    mode_code = int(record[2:])
                    mode_name = None
                    for name, code in MODES.items():
                        if code == mode_code:
                            mode_name = name
                            break
                    if mode_name:
                        self.current_mode = mode_name
                        self.update_radio_status()
                        self.log(f"GUI MODE <- {mode_name}")
                except Exception:
                    self.log(f"Invalid MD response: {record}")
                continue

            self.log(f"UNHANDLED CAT: {record}")

    # ============================================================
    # PTT
    # ============================================================

    def ptt_changed(self):
        if not self.serial or not self.serial.is_open:
            return

        if self.ptt_button.isChecked():
            self.send_command("TX;")
            self.ptt_button.setStyleSheet("background-color: #aa4444; color: white;")
        else:
            self.send_command("RX;")
            self.ptt_button.setStyleSheet("")


    def eventFilter(self, obj, event):
        # Reset focus after ANY mouse click
        if event.type() == event.MouseButtonPress:
            self.setFocus()
        return super().eventFilter(obj, event)




    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Space:
            self.ptt_button.toggle()
            self.ptt_changed()
            event.accept()
            return
        super().keyPressEvent(event)


    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta > 0:
            self.tune(self.tuning_step)
        elif delta < 0:
            self.tune(-self.tuning_step)
        event.accept()





if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = TruSDXControl()
    window.show()
    sys.exit(app.exec_())

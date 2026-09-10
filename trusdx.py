# ============================================================
# (tr)uSDX CAT CONTROL V2.8
# BY 9A4AM + Copilot assist
#
# V2.5 changes:
# - Fixed layout squeezing issues
# - Massive frequency display
# - Cleaned GUI structure
# - Improved SizePolicy handling
# - Same functionality as V2.4
# ============================================================

import sys
import os
import time
import configparser
import serial
import serial.tools.list_ports

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QPushButton,
    QComboBox, QLineEdit, QGroupBox, QGridLayout, QVBoxLayout,
    QHBoxLayout, QPlainTextEdit, QMessageBox, QSizePolicy,
    QSlider
)



import pyaudio
from PyQt5.QtCore import QThread
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

            import numpy as np
            audio = np.frombuffer(data, dtype=np.int16).copy()   # KLJUČNO: .copy()

            # MUTE
            if self.parent.loopback_muted:
                audio[:] = 0
            else:
                # Apply volume
                if self.parent.loopback_volume != 1.0:
                    audio = (audio * self.parent.loopback_volume).astype(np.int16)

            data = audio.tobytes()
            stream.write(data)

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

        self.setWindowTitle("(tr)uSDX CAT CONTROL V2.8 - 9A4AM")
        self.resize(1150, 780)

        self.serial = None
        self.frequency = 3675000
        self.current_mode = None
        self.radio_mode = "LSB"
        self.tuning_step = 1000

        self.band_buttons = {}
        self.favorite_buttons = []
        self.favorite_frequencies = []

        self.sync_in_progress = False

        self.cat_timer = QTimer()
        self.cat_timer.setSingleShot(True)
        self.cat_timer.timeout.connect(self.send_frequency)

        self.load_config()
        self.build_gui()
        self.showMaximized()


        self.refresh_ports()
        self.update_frequency_display()
        self.update_band_buttons()
        self.update_favorite_buttons()

        self.loopback_thread = None
        self.audio_loopback_enabled = True   # ili False ako želiš kontrolu

        self.loopback_volume = 0.5   # 1.0 = 100%

        self.poll_timer = None

        self.loopback_muted = False









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

        # Load favorites
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
    # GUI
    # ============================================================

    def build_gui(self):

        central = QWidget()
        self.setCentralWidget(central)

        main = QVBoxLayout(central)
        # ====================================================
        # DARK THEME (Gold accents)
        # ====================================================
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

            QGroupBox:title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0px 5px;
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

            QPushButton:hover {
                background-color: #2a2a2a;
            }

            QPushButton:pressed {
                background-color: #333333;
            }

            QComboBox {
                background-color: #1c1c1c;
                border: 1px solid #444444;
                padding: 4px;
                color: #d4af37;
            }

            QComboBox QAbstractItemView {
                background-color: #1c1c1c;
                selection-background-color: #333333;
                color: #d4af37;
            }

            QLineEdit {
                background-color: #1c1c1c;
                border: 1px solid #444444;
                padding: 4px;
                color: #d4af37;
            }

            QPlainTextEdit {
                background-color: #1c1c1c;
                border: 1px solid #444444;
                color: #d4af37;
            }

            QScrollBar:vertical {
                background: #1c1c1c;
                width: 12px;
                margin: 0px;
            }

            QScrollBar::handle:vertical {
                background: #444444;
                min-height: 20px;
            }

            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {
                background: none;
            }
        """)


        main.setSpacing(6)

        # ====================================================
        # TITLE
        # ====================================================
        title = QLabel("(tr)uSDX CAT CONTROL V2.8 - 9A4AM")
        title.setAlignment(Qt.AlignCenter)
        title.setFont(QFont("Segoe UI", 20, QFont.Bold))
        title.setStyleSheet("color: #d4af37;")
        main.addWidget(title)

        # ====================================================
        # FREQUENCY DISPLAY
        # ====================================================
        freq_group = QGroupBox("FREQUENCY")
        freq_layout = QVBoxLayout(freq_group)

        self.freq_display = FrequencyLabel()

        # Massive display
        self.freq_display.setMinimumHeight(180)
        self.freq_display.setMaximumHeight(260)
        self.freq_display.setFont(QFont("Segoe UI", 72, QFont.Bold))
        self.freq_display.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self.freq_display.setStyleSheet("""
            QLabel {
                background-color: #151515;
                color: #d4af37;
                border: 2px solid #444444;
                border-radius: 10px;
                padding: 12px;
            }
        """)

        self.freq_display.wheelChanged.connect(self.mouse_tune)

        freq_layout.addWidget(self.freq_display, stretch=3)

        # ----------------------------------------------------
        # Frequency controls
        # ----------------------------------------------------
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

        freq_layout.addLayout(control_layout, stretch=1)

        main.addWidget(freq_group, stretch=3)

        # ====================================================
        # FAVORITES
        # ====================================================
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

        # ====================================================
        # BAND SELECT
        # ====================================================
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

        # ====================================================
        # RADIO CONTROL
        # ====================================================
        radio_group = QGroupBox("RADIO CONTROL")
        radio_layout = QGridLayout(radio_group)

        # Mode
        mode_label = QLabel("MODE:")
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["LSB", "USB", "CW", "FM", "AM"])
        self.mode_combo.setCurrentIndex(-1)
        self.mode_combo.currentTextChanged.connect(self.mode_changed)

        radio_layout.addWidget(mode_label, 0, 0)
        radio_layout.addWidget(self.mode_combo, 0, 1)

        # Frequency input
        input_label = QLabel("SET MHz:")
        self.freq_input = QLineEdit()
        self.freq_input.setPlaceholderText("npr. 14.195000")
        self.freq_input.returnPressed.connect(self.set_frequency_from_input)

        set_button = QPushButton("SET")
        set_button.clicked.connect(self.set_frequency_from_input)

        radio_layout.addWidget(input_label, 1, 0)
        radio_layout.addWidget(self.freq_input, 1, 1)
        radio_layout.addWidget(set_button, 1, 2)


        # Volume slider
        volume_label = QLabel("Volume")
        volume_slider = QSlider(Qt.Horizontal)
        volume_slider.setRange(0, 100)
        volume_slider.setValue(50)
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

        # BIG SDR STYLE BUTTON
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


        main.addWidget(radio_group)

        # ====================================================
        # CAT CONNECTION
        # ====================================================
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
        self.baud_combo.setCurrentText(str(self.saved_baud))
        connection_layout.addWidget(self.baud_combo, 1, 1)

        self.connect_button = QPushButton("CONNECT")
        self.connect_button.setMinimumHeight(36)
        self.connect_button.clicked.connect(self.connect_serial)
        connection_layout.addWidget(self.connect_button, 1, 2)

        main.addWidget(connection_group)

        # ====================================================
        # RADIO STATUS
        # ====================================================
        status_group = QGroupBox("RADIO STATUS")
        status_layout = QHBoxLayout(status_group)

        self.radio_mode_label = QLabel(f"Mode: {self.current_mode}")
        self.radio_mode_label.setFont(QFont("Segoe UI", 14, QFont.Bold))
        self.radio_mode_label.setMinimumHeight(36)

        status_layout.addWidget(self.radio_mode_label)


        main.addWidget(status_group)

        # ====================================================
        # CAT MONITOR
        # ====================================================
        monitor_group = QGroupBox("CAT MONITOR")
        monitor_layout = QVBoxLayout(monitor_group)

        self.monitor = QPlainTextEdit()
        self.monitor.setReadOnly(True)
        self.monitor.setMinimumHeight(95)
        monitor_layout.addWidget(self.monitor)

        main.addWidget(monitor_group)

        # ====================================================
        # STATUS BAR
        # ====================================================
        self.status_label = QLabel("Disconnected")
        self.status_label.setStyleSheet("color: #ff5555;")
        main.addWidget(self.status_label)


    def set_loopback_volume(self, value):
        self.loopback_volume = value / 100.0


    def toggle_mute(self):
        self.loopback_muted = self.mute_button.isChecked()


    # ============================================================
    # PORT REFRESH
    # ============================================================

    def refresh_ports(self):
        current = self.saved_port

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

        # -----------------------------
        # DISCONNECT
        # -----------------------------
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

        # -----------------------------
        # CONNECT
        # -----------------------------
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

            # Required for (tr)uSDX
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



            # START POLLING TIMER
            if self.poll_timer is None:
                self.poll_timer = QTimer()
                self.poll_timer.timeout.connect(self.poll_radio)
                self.poll_timer.start(200)
                self.log("CAT POLLING STARTED")

            self.save_config()

            # -----------------------------
            # SAFE INITIAL SYNC
            # -----------------------------
            self.sync_in_progress = True

            self.log("SYNC: READ FREQUENCY + MODE")
            self.send_command("IF;")
            time.sleep(0.05)

            self.log("SYNC: READ MODE")
            self.serial.write(b"MD;")


            self.sync_in_progress = False
            self.log("SYNC: COMPLETE")
            # nakon SYNC: COMPLETE
            self.mode_combo.setCurrentText("LSB")
            self.mode_changed("LSB")





        except Exception as e:
            self.serial = None
            self.connect_button.setText("CONNECT")
            self.status_label.setText("Disconnected")
            self.status_label.setStyleSheet("color: #ff5555;")
            QMessageBox.critical(self, "CAT ERROR", str(e))

    # ============================================================
    # CAT SEND COMMAND
    # ============================================================

    def send_command(self, command):

        if not self.serial or not self.serial.is_open:
            return

        try:
            # Clear stale data
            self.serial.reset_input_buffer()

            self.serial.write(command.encode("ascii"))
            self.serial.flush()

            self.log("TX: " + command)

            # Read until semicolon
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

            # ----------------------------------------------------
            # FA — Frequency
            # ----------------------------------------------------
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

            # ----------------------------------------------------
            # IF — Frequency inside IF response
            # ----------------------------------------------------
            if record.startswith("IF"):
                try:
                    # First 11 digits after IF = frequency
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

            # ----------------------------------------------------
            # MD — Mode
            # ----------------------------------------------------
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
                        self.mode_combo.setCurrentText(mode_name)
                        self.update_radio_status()

                        self.log(f"GUI MODE <- {mode_name}")

                except Exception:
                    self.log(f"Invalid MD response: {record}")

                continue

            # Unknown CAT record
            self.log(f"UNHANDLED CAT: {record}")


        # ============================================================
    # MODE CHANGED (GUI → RADIO)
    # ============================================================

    def mode_changed(self, mode_name):
        self.current_mode = mode_name

        # Update radio status label
        self.update_radio_status()

        # Send CAT command only if radio is connected
        if self.serial and self.serial.is_open:
            try:
                mode_code = MODES.get(mode_name, None)
                if mode_code:
                    cmd = f"MD{mode_code};"
                    self.send_command(cmd)
                    self.log(f"MODE -> {mode_name}")
            except Exception as e:
                self.log(f"MODE ERROR: {e}")


    # ============================================================
    # LOGGING
    # ============================================================

    def log(self, text):
        self.monitor.appendPlainText(text)
    # ============================================================
    # UPDATE FREQUENCY DISPLAY
    # ============================================================

    def update_frequency_display(self):
        mhz = self.frequency / 1e6
        self.freq_display.setText(f"{mhz:.6f} MHz")

    # ============================================================
    # UPDATE RADIO STATUS
    # ============================================================

    def update_radio_status(self):
        self.radio_mode_label.setText(f"Mode: {self.current_mode}")


    # ============================================================
    # UPDATE BAND BUTTONS
    # ============================================================

    def update_band_buttons(self):
        for band, button in self.band_buttons.items():
            band_freq = BANDS[band]["freq"]
            if abs(self.frequency - band_freq) < 200000:
                button.setStyleSheet("background-color: #444444; color: #d4af37;")
            else:
                button.setStyleSheet("")

    # ============================================================
    # UPDATE FAVORITE BUTTONS
    # ============================================================

    def update_favorite_buttons(self):
        for i, freq in enumerate(self.favorite_frequencies):
            mhz = freq / 1e6
            self.favorite_buttons[i].setText(f"{mhz:.3f}")

    # ============================================================
    # TUNING
    # ============================================================

    def tune(self, delta):
        self.frequency += delta

        if self.frequency < 100000:
            self.frequency = 100000
        if self.frequency > 60000000:
            self.frequency = 60000000

        self.update_frequency_display()
        self.update_band_buttons()
        self.update_radio_status()

        if not self.sync_in_progress:
            self.cat_timer.start(120)

    def mouse_tune(self, direction):
        self.tune(direction * self.tuning_step)

    def send_frequency(self):
        if not self.serial or not self.serial.is_open:
            return

        cmd = f"FA{self.frequency:011d};"
        self.send_command(cmd)

    # ============================================================
    # STEP CHANGE
    # ============================================================

    def step_changed(self, text):
        self.tuning_step = self.step_values[text]
        self.save_config()

    # ============================================================
    # SET FREQUENCY FROM INPUT
    # ============================================================

    def set_frequency_from_input(self):
        try:
            mhz = float(self.freq_input.text())
            freq = int(mhz * 1e6)

            if 100000 <= freq <= 60000000:
                self.frequency = freq
                self.update_frequency_display()
                self.update_band_buttons()
                self.update_radio_status()
                self.send_frequency()
            else:
                QMessageBox.warning(self, "FREQ", "Incorrect frequency.")
        except Exception:
            QMessageBox.warning(self, "FREQ", "Incorrect frequency format.")

    # ============================================================
    # SELECT BAND
    # ============================================================

    def select_band(self, band):
        info = BANDS[band]
        self.frequency = info["freq"]


        # AUTO MODE PER BAND
        if band in ["80 m", "40 m"]:
            self.mode_combo.setCurrentText("LSB")
            self.mode_changed("LSB")   # pošalje MD1;
        else:
            self.mode_combo.setCurrentText("USB")
            self.mode_changed("USB")   # pošalje MD2;


        self.update_frequency_display()
        self.update_band_buttons()
        self.update_radio_status()
        self.send_frequency()

    # ============================================================
    # FAVORITES
    # ============================================================

    def select_favorite(self, index):
        freq = self.favorite_frequencies[index]
        self.frequency = freq

        self.update_frequency_display()
        self.update_band_buttons()
        self.update_radio_status()
        self.send_frequency()

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

    # ============================================================
    # SAVE CONFIG
    # ============================================================

    def save_config(self):
        try:
            self.config["CAT"] = {
                "port": self.port_combo.currentText(),
                "baud": self.baud_combo.currentText(),
                "step": str(self.tuning_step)
            }

            self.config["FAVORITES"] = {}
            for i, freq in enumerate(self.favorite_frequencies, start=1):
                self.config["FAVORITES"][f"fav{i}"] = str(freq)

            with open(CONFIG_FILE, "w") as f:
                self.config.write(f)

        except Exception:
            pass


    def closeEvent(self, event):
        # STOP AUDIO LOOPBACK IF RUNNING
        if self.loopback_thread:
            self.loopback_thread.stop()
            self.loopback_thread.wait()
            self.loopback_thread = None
            self.log("AUDIO LOOPBACK STOPPED (APP EXIT)")

        # OPTIONAL: zatvori CAT ako je otvoren
        if self.serial and self.serial.is_open:
            try:
                self.serial.close()
                self.log("CAT CLOSED (APP EXIT)")
            except Exception:
                pass

        event.accept()


# ============================================================
# MAIN
# ============================================================

def main():
    app = QApplication(sys.argv)
    win = TruSDXControl()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()

import queue
import re
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass

import serial
import serial.tools.list_ports
from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


BAUDRATES = [150, 300, 600, 1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200]
DATA_BITS = {"7": serial.SEVENBITS, "8": serial.EIGHTBITS}
PARITIES = {"N": serial.PARITY_NONE, "E": serial.PARITY_EVEN, "O": serial.PARITY_ODD}
STOP_BITS = {"1": serial.STOPBITS_ONE, "2": serial.STOPBITS_TWO}
TERMINATORS = {"brak": b"", "CR": b"\r", "LF": b"\n", "CR-LF": b"\r\n", "wlasny HEX": None}


def hex_view(data: bytes) -> str:
    return " ".join(f"{byte:02X}" for byte in data)


def clean_hex(text: str) -> bytes:
    normalized = re.sub(r"[^0-9A-Fa-f]", "", text)
    if len(normalized) % 2:
        raise ValueError("Liczba cyfr HEX musi byc parzysta.")
    return bytes.fromhex(normalized)


def modbus_lrc(payload: bytes) -> int:
    return (-sum(payload)) & 0xFF


def build_modbus_ascii(address: int, function: int, data: bytes = b"") -> bytes:
    payload = bytes([address, function]) + data
    raw = payload + bytes([modbus_lrc(payload)])
    return b":" + raw.hex().upper().encode("ascii") + b"\r\n"


@dataclass
class ModbusFrame:
    address: int
    function: int
    data: bytes
    lrc: int
    raw: bytes
    ascii_line: bytes


def parse_modbus_ascii(line: bytes) -> ModbusFrame:
    stripped = line.strip()
    if not stripped.startswith(b":"):
        raise ValueError("Ramka ASCII musi zaczynac sie znakiem ':'.")
    hex_part = stripped[1:]
    if len(hex_part) < 6 or len(hex_part) % 2:
        raise ValueError("Niepoprawna dlugosc ramki MODBUS ASCII.")
    try:
        raw = bytes.fromhex(hex_part.decode("ascii"))
    except ValueError as exc:
        raise ValueError("Ramka zawiera znaki spoza kodu HEX.") from exc
    if sum(raw) & 0xFF:
        raise ValueError("Niepoprawna suma kontrolna LRC.")
    return ModbusFrame(raw[0], raw[1], raw[2:-1], raw[-1], raw, stripped)


class SerialSignals(QObject):
    data_received = Signal(object)
    closed = Signal(str)


class SerialController:
    def __init__(self, signals: SerialSignals):
        self.signals = signals
        self.serial = None
        self.reader = None
        self.stop_event = threading.Event()

    @property
    def is_open(self) -> bool:
        return bool(self.serial and self.serial.is_open)

    def open(self, port, baudrate, bytesize, parity, stopbits, flow):
        self.close()
        self.stop_event.clear()
        self.serial = serial.serial_for_url(
            port,
            baudrate=baudrate,
            bytesize=bytesize,
            parity=parity,
            stopbits=stopbits,
            timeout=0.05,
            write_timeout=2,
            xonxoff=flow == "XON/XOFF",
            rtscts=flow == "RTS/CTS",
            dsrdtr=flow == "DTR/DSR",
        )
        self.reader = threading.Thread(target=self._read_loop, daemon=True)
        self.reader.start()

    def close(self):
        self.stop_event.set()
        if self.serial:
            try:
                self.serial.close()
            except serial.SerialException:
                pass
        self.serial = None

    def write(self, data: bytes):
        if not self.is_open:
            raise RuntimeError("Port nie jest otwarty.")
        self.serial.write(data)
        self.serial.flush()

    def set_dtr(self, enabled: bool):
        if self.is_open:
            self.serial.dtr = enabled

    def set_rts(self, enabled: bool):
        if self.is_open:
            self.serial.rts = enabled

    def modem_status(self):
        if not self.is_open:
            return None
        return {
            "CTS": self.serial.cts,
            "DSR": self.serial.dsr,
            "RI": self.serial.ri,
            "CD": self.serial.cd,
        }

    def _read_loop(self):
        try:
            while not self.stop_event.is_set() and self.serial and self.serial.is_open:
                data = self.serial.read(self.serial.in_waiting or 1)
                if data:
                    self.signals.data_received.emit(data)
        except serial.SerialException as exc:
            self.signals.closed.emit(str(exc))


class MainWindow(QMainWindow):
    status_from_thread = Signal(str)
    log_from_thread = Signal(str)
    error_from_thread = Signal(str, str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("IwSK cw.1 - RS-232 i MODBUS ASCII")
        self.resize(1160, 780)
        self.setMinimumSize(980, 660)

        self.signals = SerialSignals()
        self.serial = SerialController(self.signals)
        self.signals.data_received.connect(self.handle_rx)
        self.signals.closed.connect(self.handle_closed)
        self.status_from_thread.connect(self.set_status)
        self.log_from_thread.connect(self.log_modbus)
        self.error_from_thread.connect(self.show_error)

        self.modbus_buffer = bytearray()
        self.modbus_last_byte_at = None
        self.master_responses = queue.Queue()
        self.pending_pings = {}
        self.ignore_master_echo = deque(maxlen=8)
        self.ignore_slave_echo = deque(maxlen=8)
        self.rx_bytes_total = 0
        self.last_rx_at = None

        self._build_ui()
        self.refresh_ports()

        self.line_timer = QTimer(self)
        self.line_timer.timeout.connect(self.refresh_modem_lines)
        self.line_timer.start(300)

    def _build_ui(self):
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.addWidget(self._serial_bar())

        tabs = QTabWidget()
        tabs.addTab(self._terminal_tab(), "Terminal RS-232")
        tabs.addTab(self._modbus_tab(), "MODBUS ASCII")
        tabs.addTab(self._cable_tab(), "Test kabla i piny")
        layout.addWidget(tabs, 1)
        self.setCentralWidget(root)

    def _serial_bar(self):
        box = QGroupBox("Polaczenie szeregowe")
        grid = QGridLayout(box)

        self.port_combo = QComboBox()
        self.port_combo.setEditable(True)
        self.refresh_button = QPushButton("Odswiez")
        self.refresh_button.clicked.connect(self.refresh_ports)

        self.baud_combo = QComboBox()
        self.baud_combo.addItems([str(value) for value in BAUDRATES])
        self.baud_combo.setCurrentText("9600")
        self.data_bits_combo = QComboBox()
        self.data_bits_combo.addItems(DATA_BITS.keys())
        self.data_bits_combo.setCurrentText("8")
        self.parity_combo = QComboBox()
        self.parity_combo.addItems(PARITIES.keys())
        self.stop_bits_combo = QComboBox()
        self.stop_bits_combo.addItems(STOP_BITS.keys())
        self.flow_combo = QComboBox()
        self.flow_combo.addItems(["brak", "RTS/CTS", "DTR/DSR", "XON/XOFF"])

        self.connect_button = QPushButton("Polacz")
        self.connect_button.clicked.connect(self.toggle_connection)
        self.dtr_check = QCheckBox("DTR")
        self.rts_check = QCheckBox("RTS")
        self.dtr_check.toggled.connect(self.serial.set_dtr)
        self.rts_check.toggled.connect(self.serial.set_rts)
        self.status_label = QLabel("Rozlaczony")
        self.status_label.setStyleSheet("font-weight: 600")
        self.lines_label = QLabel("CTS: -   DSR: -   RI: -   CD: -")

        labels = ["Port", "", "Predkosc", "Dane", "Parzystosc", "Stop", "Kontrola przeplywu"]
        widgets = [
            self.port_combo,
            self.refresh_button,
            self.baud_combo,
            self.data_bits_combo,
            self.parity_combo,
            self.stop_bits_combo,
            self.flow_combo,
        ]
        for column, text in enumerate(labels):
            if text:
                grid.addWidget(QLabel(text), 0, column)
            grid.addWidget(widgets[column], 1, column)

        grid.addWidget(self.connect_button, 1, 7)
        grid.addWidget(self.dtr_check, 1, 8)
        grid.addWidget(self.rts_check, 1, 9)
        grid.addWidget(self.status_label, 0, 10)
        grid.addWidget(self.lines_label, 1, 10)
        grid.setColumnStretch(0, 2)
        grid.setColumnStretch(10, 2)
        return box

    def _terminal_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        controls = QHBoxLayout()
        self.encoding_combo = QComboBox()
        self.encoding_combo.addItems(["utf-8", "cp1250", "ascii", "latin-1"])
        self.term_combo = QComboBox()
        self.term_combo.addItems(TERMINATORS.keys())
        self.term_combo.setCurrentText("CR-LF")
        self.custom_term_edit = QLineEdit()
        self.custom_term_edit.setPlaceholderText("terminator HEX")
        self.transaction_timeout_spin = QSpinBox()
        self.transaction_timeout_spin.setRange(0, 10000)
        self.transaction_timeout_spin.setValue(1000)
        self.auto_ping_check = QCheckBox("Auto echo PING")
        self.auto_ping_check.setChecked(True)

        for label, widget in [
            ("Kodowanie", self.encoding_combo),
            ("Terminator", self.term_combo),
            ("", self.custom_term_edit),
            ("Timeout ms", self.transaction_timeout_spin),
            ("", self.auto_ping_check),
        ]:
            if label:
                controls.addWidget(QLabel(label))
            controls.addWidget(widget)
        controls.addStretch(1)
        layout.addLayout(controls)

        panes = QHBoxLayout()
        tx_box = QGroupBox("Nadawanie")
        tx_layout = QVBoxLayout(tx_box)
        self.tx_text = QTextEdit()
        tx_layout.addWidget(self.tx_text, 1)
        tx_buttons = QHBoxLayout()
        for text, slot in [
            ("Wyslij tekst", self.send_text),
            ("Transakcja", self.send_transaction),
            ("PING", self.send_ping),
            ("Wyczysc", self.tx_text.clear),
        ]:
            button = QPushButton(text)
            button.clicked.connect(slot)
            tx_buttons.addWidget(button)
        tx_buttons.addStretch(1)
        tx_layout.addLayout(tx_buttons)

        rx_box = QGroupBox("Odbior")
        rx_layout = QVBoxLayout(rx_box)
        self.rx_text = QTextEdit()
        self.rx_text.setReadOnly(True)
        rx_layout.addWidget(self.rx_text, 1)
        clear_rx = QPushButton("Wyczysc")
        clear_rx.clicked.connect(self.rx_text.clear)
        rx_layout.addWidget(clear_rx, alignment=Qt.AlignLeft)

        panes.addWidget(tx_box, 1)
        panes.addWidget(rx_box, 1)
        layout.addLayout(panes, 1)

        hex_box = QGroupBox("Tryb binarny HEX")
        hex_layout = QGridLayout(hex_box)
        self.hex_tx_edit = QLineEdit()
        self.hex_rx_label = QLabel("Odebrane HEX:")
        send_hex = QPushButton("Wyslij HEX")
        send_hex.clicked.connect(self.send_hex)
        hex_layout.addWidget(self.hex_tx_edit, 0, 0)
        hex_layout.addWidget(send_hex, 0, 1)
        hex_layout.addWidget(self.hex_rx_label, 1, 0, 1, 2)
        hex_layout.setColumnStretch(0, 1)
        layout.addWidget(hex_box)
        return tab

    def _modbus_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        top = QHBoxLayout()

        master_box = QGroupBox("Master")
        master = QGridLayout(master_box)
        self.master_address_spin = QSpinBox()
        self.master_address_spin.setRange(0, 247)
        self.master_address_spin.setValue(1)
        self.master_function_combo = QComboBox()
        self.master_function_combo.addItems(["1 - wyslij tekst", "2 - odczytaj tekst"])
        self.master_data_edit = QLineEdit("Test MODBUS")
        self.master_timeout_spin = QSpinBox()
        self.master_timeout_spin.setRange(0, 10000)
        self.master_timeout_spin.setValue(1000)
        self.master_retries_spin = QSpinBox()
        self.master_retries_spin.setRange(0, 5)
        self.master_retries_spin.setValue(1)
        self.master_interchar_spin = QSpinBox()
        self.master_interchar_spin.setRange(0, 1000)
        self.master_interchar_spin.setSingleStep(10)
        self.master_interchar_spin.setValue(100)
        master_send = QPushButton("Wyslij ramke")
        master_send.clicked.connect(self.start_master_transaction)

        master.addWidget(QLabel("Adres slave (0 broadcast)"), 0, 0)
        master.addWidget(self.master_address_spin, 1, 0)
        master.addWidget(QLabel("Rozkaz"), 0, 1)
        master.addWidget(self.master_function_combo, 1, 1)
        master.addWidget(QLabel("Dane"), 0, 2)
        master.addWidget(self.master_data_edit, 1, 2)
        master.addWidget(QLabel("Timeout ms"), 2, 0)
        master.addWidget(self.master_timeout_spin, 3, 0)
        master.addWidget(QLabel("Retransmisje"), 2, 1)
        master.addWidget(self.master_retries_spin, 3, 1)
        master.addWidget(QLabel("Odstep znakow ms"), 2, 2)
        master.addWidget(self.master_interchar_spin, 3, 2)
        master.addWidget(master_send, 3, 3)
        master.setColumnStretch(2, 1)

        slave_box = QGroupBox("Slave")
        slave = QGridLayout(slave_box)
        self.slave_enabled_check = QCheckBox("Aktywny")
        self.slave_enabled_check.setChecked(True)
        self.slave_address_spin = QSpinBox()
        self.slave_address_spin.setRange(1, 247)
        self.slave_address_spin.setValue(1)
        self.slave_interchar_spin = QSpinBox()
        self.slave_interchar_spin.setRange(0, 1000)
        self.slave_interchar_spin.setSingleStep(10)
        self.slave_interchar_spin.setValue(100)
        self.slave_text_edit = QLineEdit("Tekst ze stacji slave")
        self.slave_received_edit = QLineEdit()
        self.slave_received_edit.setReadOnly(True)
        slave.addWidget(self.slave_enabled_check, 0, 0)
        slave.addWidget(QLabel("Adres stacji"), 0, 1)
        slave.addWidget(self.slave_address_spin, 1, 1)
        slave.addWidget(QLabel("Odstep znakow ms"), 0, 2)
        slave.addWidget(self.slave_interchar_spin, 1, 2)
        slave.addWidget(QLabel("Tekst do odczytu"), 2, 0, 1, 3)
        slave.addWidget(self.slave_text_edit, 3, 0, 1, 3)
        slave.addWidget(QLabel("Tekst odebrany"), 4, 0, 1, 3)
        slave.addWidget(self.slave_received_edit, 5, 0, 1, 3)
        slave.setColumnStretch(0, 1)

        top.addWidget(master_box, 1)
        top.addWidget(slave_box, 1)
        layout.addLayout(top)

        log_box = QGroupBox("Podglad ramek")
        log_layout = QVBoxLayout(log_box)
        self.modbus_log = QTextEdit()
        self.modbus_log.setReadOnly(True)
        log_layout.addWidget(self.modbus_log, 1)
        clear_log = QPushButton("Wyczysc")
        clear_log.clicked.connect(self.modbus_log.clear)
        log_layout.addWidget(clear_log, alignment=Qt.AlignLeft)
        layout.addWidget(log_box, 1)
        return tab

    def _cable_tab(self):
        tab = QWidget()
        layout = QHBoxLayout(tab)

        map_box = QGroupBox("Mapa pinow DB9 null-modem")
        map_layout = QVBoxLayout(map_box)
        pin_map = QTextEdit()
        pin_map.setReadOnly(True)
        pin_map.setStyleSheet("font-family: Consolas, monospace")
        pin_map.setPlainText(
            "Minimalne polaczenie wymagane do transmisji w dwie strony:\n\n"
            "Komputer A DB9              Komputer B DB9\n"
            "pin 2  RXD   <-----------   pin 3  TXD\n"
            "pin 3  TXD   ----------->   pin 2  RXD\n"
            "pin 5  GND   <---------->   pin 5  GND\n\n"
            "Linie sterujace zgodne z instrukcja cwiczenia:\n\n"
            "pin 7  RTS   ----------->   pin 8  CTS\n"
            "pin 8  CTS   <-----------   pin 7  RTS\n"
            "pin 4  DTR   ----------->   pin 6  DSR\n"
            "pin 6  DSR   <-----------   pin 4  DTR\n"
            "pin 1  CD    opcjonalnie / zalezy od kabla\n"
            "pin 9  RI    opcjonalnie / zwykle nieuzywany\n"
        )
        map_layout.addWidget(pin_map, 1)

        verify_box = QGroupBox("Weryfikacja z programu")
        verify = QGridLayout(verify_box)
        self.pin_status_labels = {}
        rows = [
            ("Wejscie pin 8 CTS", "CTS"),
            ("Wejscie pin 6 DSR", "DSR"),
            ("Wejscie pin 9 RI", "RI"),
            ("Wejscie pin 1 CD", "CD"),
            ("Wyjscie pin 7 RTS", "RTS"),
            ("Wyjscie pin 4 DTR", "DTR"),
        ]
        for row, (label, key) in enumerate(rows):
            verify.addWidget(QLabel(label), row, 0)
            value = QLabel("-")
            value.setStyleSheet("font-weight: 600")
            self.pin_status_labels[key] = value
            verify.addWidget(value, row, 1)

        self.rx_count_label = QLabel("0 bajtow")
        self.last_rx_label = QLabel("-")
        self.last_test_label = QLabel("-")
        verify.addWidget(QLabel("Odebrane dane RXD pin 2"), 6, 0)
        verify.addWidget(self.rx_count_label, 6, 1)
        verify.addWidget(QLabel("Ostatni odbior"), 7, 0)
        verify.addWidget(self.last_rx_label, 7, 1)
        verify.addWidget(QLabel("Ostatni test kierunku"), 8, 0)
        verify.addWidget(self.last_test_label, 8, 1)

        send_ab = QPushButton("Wyslij test A->B")
        send_ba = QPushButton("Wyslij test B->A")
        clear = QPushButton("Wyczysc wynik")
        send_ab.clicked.connect(lambda: self.send_cable_test("A->B"))
        send_ba.clicked.connect(lambda: self.send_cable_test("B->A"))
        clear.clicked.connect(self.clear_cable_result)
        verify.addWidget(send_ab, 9, 0)
        verify.addWidget(send_ba, 9, 1)
        verify.addWidget(clear, 10, 0)
        verify.setColumnStretch(1, 1)

        note = QTextEdit()
        note.setReadOnly(True)
        note.setMaximumHeight(130)
        note.setPlainText(
            "TXD/RXD nie da sie potwierdzic samym odczytem stanu pinu. "
            "Program weryfikuje je testem transmisji: jeden komputer wysyla znacznik, "
            "drugi musi go odebrac. Linie CTS/DSR/RI/CD sa odczytywane na zywo, "
            "a DTR/RTS ustawiaja checkboxy w gornej belce."
        )
        verify.addWidget(note, 11, 0, 1, 2)

        layout.addWidget(map_box, 1)
        layout.addWidget(verify_box, 1)
        return tab

    def refresh_ports(self):
        current = self.port_combo.currentText()
        ports = [port.device for port in serial.tools.list_ports.comports()]
        self.port_combo.clear()
        self.port_combo.addItems(ports + ["loop://"])
        if current:
            self.port_combo.setCurrentText(current)
        elif ports:
            self.port_combo.setCurrentText(ports[0])
        else:
            self.port_combo.setCurrentText("loop://")

    def toggle_connection(self):
        if self.serial.is_open:
            self.serial.close()
            self.connect_button.setText("Polacz")
            self.set_status("Rozlaczony")
            return
        try:
            self.serial.open(
                self.port_combo.currentText().strip(),
                int(self.baud_combo.currentText()),
                DATA_BITS[self.data_bits_combo.currentText()],
                PARITIES[self.parity_combo.currentText()],
                STOP_BITS[self.stop_bits_combo.currentText()],
                self.flow_combo.currentText(),
            )
            self.serial.set_dtr(self.dtr_check.isChecked())
            self.serial.set_rts(self.rts_check.isChecked())
            self.connect_button.setText("Rozlacz")
            self.set_status(f"Polaczony: {self.port_combo.currentText().strip()}")
        except Exception as exc:
            self.show_error("Blad polaczenia", str(exc))

    def get_terminator(self) -> bytes:
        selected = self.term_combo.currentText()
        term = TERMINATORS[selected]
        if term is not None:
            return term
        return clean_hex(self.custom_term_edit.text()) if self.custom_term_edit.text().strip() else b""

    def send_bytes(self, data: bytes, label="TX"):
        try:
            self.serial.write(data)
            self.log_modbus(f"{label} bytes: {hex_view(data)}")
        except Exception as exc:
            self.show_error("Blad nadawania", str(exc))

    def send_text(self):
        try:
            payload = self.tx_text.toPlainText().encode(self.encoding_combo.currentText(), errors="replace")
            self.send_bytes(payload + self.get_terminator(), "TX terminal")
        except ValueError as exc:
            self.show_error("Blad terminatora", str(exc))

    def send_hex(self):
        try:
            self.send_bytes(clean_hex(self.hex_tx_edit.text()) + self.get_terminator(), "TX HEX")
        except ValueError as exc:
            self.show_error("Blad HEX", str(exc))

    def send_transaction(self):
        try:
            payload = self.tx_text.toPlainText().encode(self.encoding_combo.currentText(), errors="replace")
            self.send_bytes(payload + self.get_terminator(), "TX transakcja")
            self.set_status(f"Transakcja wyslana, oczekiwanie {self.transaction_timeout_spin.value()} ms")
        except ValueError as exc:
            self.show_error("Blad transakcji", str(exc))

    def send_ping(self):
        try:
            token = f"PING:{time.monotonic_ns()}"
            self.pending_pings[token] = time.perf_counter()
            self.send_bytes(token.encode("ascii") + self.get_terminator(), "TX PING")
            self.set_status("PING wyslany")
        except ValueError as exc:
            self.show_error("Blad PING", str(exc))

    def send_cable_test(self, direction: str):
        try:
            token = f"TEST_KABLA_{direction}:{time.strftime('%H:%M:%S')}"
            self.send_bytes(token.encode("ascii") + self.get_terminator(), f"TX test kabla {direction}")
            self.last_test_label.setText(f"Wyslano {direction}: {token}")
        except ValueError as exc:
            self.show_error("Blad testu kabla", str(exc))

    def clear_cable_result(self):
        self.rx_bytes_total = 0
        self.last_rx_at = None
        self.rx_count_label.setText("0 bajtow")
        self.last_rx_label.setText("-")
        self.last_test_label.setText("-")

    def start_master_transaction(self):
        if not self.serial.is_open:
            self.show_error("Port", "Najpierw otworz port szeregowy.")
            return
        address = self.master_address_spin.value()
        function = int(self.master_function_combo.currentText()[0])
        data = b"" if function == 2 else self.master_data_edit.text().encode(self.encoding_combo.currentText(), errors="replace")
        while not self.master_responses.empty():
            try:
                self.master_responses.get_nowait()
            except queue.Empty:
                break
        thread = threading.Thread(
            target=self._master_worker,
            args=(
                address,
                function,
                data,
                self.master_timeout_spin.value() / 1000,
                self.master_retries_spin.value(),
            ),
            daemon=True,
        )
        thread.start()

    def _master_worker(self, address, function, data, timeout, retries):
        frame = build_modbus_ascii(address, function, data)
        broadcast = address == 0
        for attempt in range(retries + 1):
            try:
                self.ignore_master_echo.append(frame.strip())
                self.serial.write(frame)
            except Exception as exc:
                self.error_from_thread.emit("Blad MODBUS", str(exc))
                return
            self.log_from_thread.emit(f"MASTER TX proba {attempt + 1}: {frame.decode('ascii').strip()} | HEX: {hex_view(frame)}")
            if broadcast:
                self.status_from_thread.emit("Ramka rozgloszeniowa wyslana bez oczekiwania na odpowiedz.")
                return
            deadline = time.perf_counter() + timeout
            while time.perf_counter() < deadline:
                remaining = max(0.01, deadline - time.perf_counter())
                try:
                    response = self.master_responses.get(timeout=min(0.05, remaining))
                except queue.Empty:
                    continue
                if response.address != address:
                    continue
                if response.function == (function | 0x80):
                    code = response.data[0] if response.data else 0
                    self.status_from_thread.emit(f"MODBUS wyjatek: kod {code}")
                    return
                if response.function == function:
                    if function == 2:
                        text = response.data.decode(self.encoding_combo.currentText(), errors="replace")
                        self.status_from_thread.emit(f"Odczytano tekst slave: {text}")
                    else:
                        self.status_from_thread.emit("Transakcja MODBUS zakonczona poprawnie.")
                    return
            self.log_from_thread.emit(f"MASTER timeout po {int(timeout * 1000)} ms")
        self.status_from_thread.emit("MODBUS: brak odpowiedzi po retransmisjach.")

    def handle_rx(self, data: bytes):
        decoded = data.decode(self.encoding_combo.currentText(), errors="replace")
        self.rx_bytes_total += len(data)
        self.last_rx_at = time.strftime("%H:%M:%S")
        self.rx_count_label.setText(f"{self.rx_bytes_total} bajtow")
        self.last_rx_label.setText(f"{self.last_rx_at} | HEX: {hex_view(data[-32:])}")
        self.update_cable_test_result(decoded)
        self.rx_text.moveCursor(self.rx_text.textCursor().MoveOperation.End)
        self.rx_text.insertPlainText(decoded)
        self.rx_text.moveCursor(self.rx_text.textCursor().MoveOperation.End)
        self.hex_rx_label.setText(f"Odebrane HEX: {hex_view(data[-96:])}")
        self.feed_modbus(data)
        self.handle_ping(decoded)

    def update_cable_test_result(self, decoded: str):
        match = re.search(r"TEST_KABLA_(A->B|B->A):\d{2}:\d{2}:\d{2}", decoded)
        if match:
            self.last_test_label.setText(f"Odebrano {match.group(0)}")

    def handle_ping(self, decoded: str):
        for token in re.findall(r"PING:\d+", decoded):
            started = self.pending_pings.pop(token, None)
            if started is not None:
                delay_ms = (time.perf_counter() - started) * 1000
                self.set_status(f"PING round trip delay: {delay_ms:.1f} ms")
            elif self.auto_ping_check.isChecked() and self.serial.is_open:
                try:
                    self.serial.write(token.encode("ascii") + self.get_terminator())
                except Exception:
                    pass

    def feed_modbus(self, data: bytes):
        now = time.perf_counter()
        limit_ms = self.current_interchar_limit()
        if self.modbus_buffer and limit_ms and self.modbus_last_byte_at:
            gap_ms = (now - self.modbus_last_byte_at) * 1000
            if gap_ms > limit_ms:
                self.log_modbus(f"RX przerwana ramka: odstep {gap_ms:.1f} ms > {limit_ms} ms")
                self.modbus_buffer.clear()

        for byte in data:
            if byte == ord(":"):
                self.modbus_buffer.clear()
            if self.modbus_buffer or byte == ord(":"):
                self.modbus_buffer.append(byte)
            if byte == 0x0A and self.modbus_buffer:
                line = bytes(self.modbus_buffer)
                self.modbus_buffer.clear()
                self.process_modbus_line(line)
        self.modbus_last_byte_at = now

    def current_interchar_limit(self) -> int:
        if self.slave_enabled_check.isChecked():
            return self.slave_interchar_spin.value()
        return self.master_interchar_spin.value()

    def process_modbus_line(self, line: bytes):
        try:
            frame = parse_modbus_ascii(line)
        except ValueError as exc:
            self.log_modbus(f"RX blad ramki: {exc} | {line!r}")
            return

        self.log_modbus(f"RX MODBUS: {frame.ascii_line.decode('ascii')} | HEX: {hex_view(frame.raw)}")
        master_echo = frame.ascii_line in self.ignore_master_echo
        if master_echo:
            self.ignore_master_echo.remove(frame.ascii_line)
        else:
            self.master_responses.put(frame)

        slave_echo = frame.ascii_line in self.ignore_slave_echo
        if slave_echo:
            self.ignore_slave_echo.remove(frame.ascii_line)
            return
        if self.slave_enabled_check.isChecked():
            self.handle_slave_frame(frame)

    def handle_slave_frame(self, frame: ModbusFrame):
        own_address = self.slave_address_spin.value()
        if frame.address not in (own_address, 0):
            return
        broadcast = frame.address == 0
        if frame.function == 1:
            text = frame.data.decode(self.encoding_combo.currentText(), errors="replace")
            self.slave_received_edit.setText(text)
            self.log_modbus(f"SLAVE rozkaz 1: zapisano tekst '{text}'")
            if not broadcast:
                self.send_slave_response(frame.address, frame.function, frame.data)
        elif frame.function == 2:
            if broadcast:
                self.log_modbus("SLAVE rozkaz 2 z adresem 0 pominiety: odczyt nie jest rozgloszeniowy.")
                return
            data = self.slave_text_edit.text().encode(self.encoding_combo.currentText(), errors="replace")
            self.send_slave_response(frame.address, frame.function, data)
        else:
            if not broadcast:
                self.send_slave_response(frame.address, frame.function | 0x80, b"\x01")

    def send_slave_response(self, address: int, function: int, data: bytes):
        response = build_modbus_ascii(address, function, data)
        try:
            self.ignore_slave_echo.append(response.strip())
            self.serial.write(response)
            self.log_modbus(f"SLAVE TX: {response.decode('ascii').strip()} | HEX: {hex_view(response)}")
        except Exception as exc:
            self.log_modbus(f"SLAVE TX blad: {exc}")

    def refresh_modem_lines(self):
        status = self.serial.modem_status()
        if status:
            self.lines_label.setText("   ".join(f"{key}: {'1' if value else '0'}" for key, value in status.items()))
            for key, value in status.items():
                if key in self.pin_status_labels:
                    self.pin_status_labels[key].setText("1" if value else "0")
        else:
            self.lines_label.setText("CTS: -   DSR: -   RI: -   CD: -")
            for key in ("CTS", "DSR", "RI", "CD"):
                self.pin_status_labels[key].setText("-")
        self.pin_status_labels["RTS"].setText("1" if self.rts_check.isChecked() else "0")
        self.pin_status_labels["DTR"].setText("1" if self.dtr_check.isChecked() else "0")

    def set_status(self, text: str):
        self.status_label.setText(text)

    def log_modbus(self, text: str):
        timestamp = time.strftime("%H:%M:%S")
        self.modbus_log.append(f"[{timestamp}] {text}")

    def show_error(self, title: str, text: str):
        QMessageBox.critical(self, title, text)

    def handle_closed(self, reason: str):
        self.connect_button.setText("Polacz")
        self.set_status(f"Port zamkniety: {reason}")

    def closeEvent(self, event):
        self.serial.close()
        event.accept()


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

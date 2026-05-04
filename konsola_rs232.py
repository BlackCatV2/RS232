import argparse
import threading
import time

import serial
import serial.tools.list_ports


TERMINATORS = {
    "none": b"",
    "cr": b"\r",
    "lf": b"\n",
    "crlf": b"\r\n",
}


def hex_view(data: bytes) -> str:
    return " ".join(f"{byte:02X}" for byte in data)


def parse_hex(text: str) -> bytes:
    cleaned = "".join(char for char in text if char not in " \t\r\n,-_:")
    if len(cleaned) % 2:
        raise ValueError("Nieparzysta liczba cyfr HEX.")
    return bytes.fromhex(cleaned)


def list_ports() -> list[str]:
    ports = [port.device for port in serial.tools.list_ports.comports()]
    return ports + ["loop://"]


def choose_port(default: str | None) -> str:
    if default:
        return default

    ports = list_ports()
    print("Dostepne porty:")
    for index, port in enumerate(ports, start=1):
        print(f"  {index}. {port}")

    selected = input("Wybierz numer portu albo wpisz nazwe COM: ").strip()
    if selected.isdigit():
        index = int(selected)
        if 1 <= index <= len(ports):
            return ports[index - 1]
    return selected


def read_loop(ser: serial.Serial, stop_event: threading.Event, encoding: str) -> None:
    while not stop_event.is_set():
        try:
            data = ser.read(ser.in_waiting or 1)
        except serial.SerialException as exc:
            print(f"\n[BLAD RX] {exc}", flush=True)
            stop_event.set()
            return

        if data:
            text = data.decode(encoding, errors="replace")
            print(f"\n[RX tekst] {text}", end="", flush=True)
            print(f"\n[RX hex]   {hex_view(data)}", flush=True)


def print_help() -> None:
    print(
        "\nKomendy:\n"
        "  zwykly tekst       wysyla tekst + terminator\n"
        "  /hex 01 02 0D 0A   wysyla bajty HEX\n"
        "  /term none|cr|lf|crlf  zmienia terminator\n"
        "  /status            pokazuje stan portu\n"
        "  /help              pokazuje pomoc\n"
        "  /quit              konczy program\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Prosty terminal konsolowy RS232.")
    parser.add_argument("--port", help="Port, np. COM3 albo loop://")
    parser.add_argument("--baud", type=int, default=9600, help="Predkosc transmisji")
    parser.add_argument("--encoding", default="utf-8", help="Kodowanie tekstu")
    parser.add_argument("--term", choices=TERMINATORS.keys(), default="crlf", help="Terminator tekstu")
    args = parser.parse_args()

    port = choose_port(args.port)
    stop_event = threading.Event()
    terminator = TERMINATORS[args.term]

    try:
        with serial.serial_for_url(
            port,
            baudrate=args.baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.05,
            write_timeout=2,
        ) as ser:
            print(f"Polaczono z {port}, {args.baud} 8N1.")
            print(f"Terminator: {args.term}. Wpisz /help, aby zobaczyc komendy.")

            reader = threading.Thread(target=read_loop, args=(ser, stop_event, args.encoding), daemon=True)
            reader.start()

            while not stop_event.is_set():
                line = input("> ")
                command = line.strip()

                if command == "/quit":
                    break
                if command == "/help":
                    print_help()
                    continue
                if command == "/status":
                    print(
                        f"Port={ser.port} otwarty={ser.is_open} "
                        f"CTS={int(ser.cts)} DSR={int(ser.dsr)} RI={int(ser.ri)} CD={int(ser.cd)}"
                    )
                    continue
                if command.startswith("/term "):
                    value = command.split(maxsplit=1)[1].lower()
                    if value not in TERMINATORS:
                        print("Nieznany terminator. Uzyj: none, cr, lf, crlf.")
                        continue
                    terminator = TERMINATORS[value]
                    print(f"Terminator ustawiony na: {value}")
                    continue
                if command.startswith("/hex "):
                    try:
                        data = parse_hex(command.split(maxsplit=1)[1])
                    except ValueError as exc:
                        print(f"Blad HEX: {exc}")
                        continue
                else:
                    data = line.encode(args.encoding, errors="replace") + terminator

                try:
                    ser.write(data)
                    ser.flush()
                    print(f"[TX hex] {hex_view(data)}")
                except serial.SerialException as exc:
                    print(f"[BLAD TX] {exc}")

    except (OSError, serial.SerialException) as exc:
        print(f"Nie mozna otworzyc portu: {exc}")
    finally:
        stop_event.set()
        time.sleep(0.1)


if __name__ == "__main__":
    main()

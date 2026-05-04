# Projekt IwSK cw.1: RS-232 i MODBUS ASCII

Program realizuje projekt z instrukcji cwiczenia 1:

- konfiguracja portu COM i sprawdzenie dostepnych portow,
- predkosc 150..115200 bit/s, 7/8 bitow danych, parzystosc N/E/O, 1/2 bity stopu,
- kontrola przeplywu: brak, RTS/CTS, DTR/DSR, XON/XOFF,
- reczne ustawianie DTR/RTS oraz podglad CTS/DSR/RI/CD,
- terminatory: brak, CR, LF, CR-LF oraz terminator wlasny HEX,
- nadawanie i odbior tekstu,
- nadawanie bajtow w trybie HEX,
- PING z pomiarem round trip delay,
- MODBUS ASCII: stacja Master i Slave,
- niestandardowe rozkazy MODBUS:
  - `1` - zapis tekstu ze stacji Master do Slave,
  - `2` - odczyt tekstu ze stacji Slave do Master,
- automatyczne wyznaczanie LRC,
- podglad ramek wyslanych i odebranych w ASCII oraz HEX,
- timeout transakcji, retransmisje i kontrola odstepu miedzy znakami ramki.

## Uruchomienie

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

Do testu na jednym komputerze mozna uzyc pary wirtualnych portow COM z emulatora
null-modem albo wpisac `loop://` w pole portu, aby wykonac prosty test petli zwrotnej
obslugiwany przez `pyserial`.

## Test na dwoch komputerach

1. Polacz komputery kablem null-modem albo przez dwa konwertery USB/RS-232.
2. Na obu komputerach uruchom program.
3. Wybierz odpowiednie porty COM i te same parametry transmisji.
4. W zakladce terminala wyslij tekst oraz wykonaj `PING`.
5. W zakladce MODBUS ustaw jedna aplikacje jako Slave aktywny, a z drugiej wysylaj
   ramki Master rozkazem `1` lub `2`.

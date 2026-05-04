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
- zakladka testu kabla: mapa pinow DB9, stan linii CTS/DSR/RI/CD, stan wyjsc DTR/RTS
  oraz test kierunku transmisji A->B i B->A,
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

## Prosty terminal konsolowy

Najprostszy wariant bez GUI jest w pliku `konsola_rs232.py`:

```powershell
.\.venv\Scripts\python.exe konsola_rs232.py
```

Mozna tez podac port od razu:

```powershell
.\.venv\Scripts\python.exe konsola_rs232.py --port COM3 --baud 9600
```

Po uruchomieniu zwykly wpisany tekst jest wysylany przez RS-232, a odbior dziala w tle.
Dostepne komendy:

```text
/hex 01 02 0D 0A
/term none|cr|lf|crlf
/status
/help
/quit
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

## Weryfikacja kabla i pinow

W zakladce `Test kabla i piny` program pokazuje wymagane polaczenia DB9 dla kabla
null-modem. Do transmisji w obie strony musza dzialac minimum:

```text
Komputer A pin 2 RXD <--- Komputer B pin 3 TXD
Komputer A pin 3 TXD ---> Komputer B pin 2 RXD
Komputer A pin 5 GND <---> Komputer B pin 5 GND
```

Program moze odczytac stany wejsc sterujacych `CTS`, `DSR`, `RI`, `CD` oraz ustawic
wyjscia `RTS` i `DTR`. Linii `TXD` i `RXD` nie da sie potwierdzic samym statusem
portu, dlatego ich weryfikacja odbywa sie przez test transmisji:

1. Na komputerze A kliknij `Wyslij test A->B`.
2. Na komputerze B sprawdz, czy pojawil sie wynik `Odebrano TEST_KABLA_A->B`.
3. Na komputerze B kliknij `Wyslij test B->A`.
4. Na komputerze A sprawdz, czy pojawil sie wynik `Odebrano TEST_KABLA_B->A`.

Jesli dziala tylko jeden z tych kierunkow, kabel albo adapter ma sprawne tylko jedno
polaczenie `TXD -> RXD` albo pomylone/niepolaczone piny 2 i 3.

#!/usr/bin/env python3

"""
Program za vodenje robota EV3 po seznamu točk na poligonu.
[Robo liga FRI 2019: Sadovnjak]
"""

from ev3dev.ev3 import TouchSensor, Button, LargeMotor, MediumMotor, Sound
import sys
import math
from time import time, sleep
from collections import deque

from io import BytesIO
import pycurl
import ujson

# razredi
# from tmk.classes.State import State
from enum import Enum


class State(Enum):
    """
    Stanja robota.
    """

    def __str__(self):
        return str(self.name)

    GET_APPLE = 0
    GET_TURN = 1
    GET_STRAIGHT = 2
    HOME = 3
    HOME_TURN = 4
    HOME_STRAIGHT = 5
    BACK_OFF = 6


# from tmk.classes.Pid import PID


class PID:
    def __init__(
            self,
            setpoint: float,
            kp: float,
            ki: float = None,
            kd: float = None,
            integral_limit: float = None):
        """
        Ustvarimo nov regulator PID s pripadajočimi parametri.

        Argumenti:
        setpoint: ciljna vrednost regulirane spremenljivke
        kp: ojačitev proporcionalnega dela regulatorja.
            Visoke vrednosti pomenijo hitrejši odziv sistema,
            vendar previsoke vrednosti povzročijo oscilacije in nestabilnost.
        ki: ojačitev integralnega člena regulatorja.
            Izniči napako v ustaljenem stanju. Zmanjša odzivnost.
        kd: ojačitev odvoda napake.
            Zmanjša čas umirjanja in poveča odzivnost.
        integral_limit: najvišja vrednost integrala
        """
        self._setpoint = setpoint
        self._kp = kp
        self._ki = ki
        self._kd = kd
        self._integral_limit = integral_limit
        self._error = None
        self._time = None
        self._integral = None
        self._value = None

    def reset(
            self,
            setpoint: float = None,
            kp: float = None,
            ki: float = None,
            kd: float = None,
            integral_limit: float = None):
        """
        Ponastavitev regulatorja.
        Lahko mu tudi spremenimo katero od vrednosti parametrov.
        Napaka, integral napake in čas se ponastavijo.
        """
        if setpoint is not None:
            self._setpoint = setpoint
        if kp is not None:
            self._kp = kp
        if ki is not None:
            self._ki = ki
        if kd is not None:
            self._kd = kd
        if integral_limit is not None:
            self._integral_limit = integral_limit
        self._error = None
        self._time = None
        self._integral = None
        self._value = None

    def update(self, measurement: float) -> float:
        """
        Izračunamo vrednost izhoda regulatorja (regulirna veličina)
        glede na izmerjeno vrednost regulirane veličine (measurement)
        in ciljno vrednost (setpoint).

        Argumenti:
        measurement: s tipali izmerjena vrednost regulirane veličine

        Izhodna vrednost:
        regulirna veličina, s katero želimo popraviti delovanje sistema
        (regulirano veličino), da bo dosegel ciljno vrednost
        """
        if self._value is None:
            # Na začetku še nimamo zgodovine meritev, zato inicializiramo
            # integral in vrnemo samo proporcionalni člen.
            self._value = measurement
            # Zapomnimo si začetni čas.
            self._time = time()
            # Ponastavimo integral napake.
            self._integral = 0
            # Napaka = ciljna vrednost - izmerjena vrednost regulirane veličine.
            self._error = self._setpoint - measurement
            return self._kp * self._error
        else:
            # Sprememba časa
            time_now = time()
            delta_time = time_now - self._time
            self._time = time_now
            # Izmerjena vrednost regulirane veličine.
            self._value = measurement
            # Napaka = ciljna vrednost - izmerjena vrednost regulirane veličine.
            error = self._setpoint - self._value

            # Proporcionalni del
            p = self._kp * error

            # Integralni in odvodni člen sta opcijska.
            if self._ki is None:
                i = 0
            else:
                # Integral se poveča za (sprememba napake) / (sprememba časa).
                self._integral += error * delta_time
                # Ojačitev integralnega dela.
                i = self._ki * self._integral
                if self._integral_limit is not None:
                    i = max(min(i, self._integral_limit),
                            (-1) * self._integral_limit)

            if self._kd is None:
                d = 0
            else:
                # Odvod napake z ojačitvijo.
                d = self._kd * (error - self._error) / delta_time
            # Posodobimo napako.
            self._error = error
            # Vrnemo regulirno veličino, sestavljeno iz proporcionalnega,
            # integralnega in odvodnega člena.
            return p + i + d


# from tmk.classes.Point import Point


class Point:
    """
    Točka na poligonu.
    """

    def __init__(self, position):
        self.x = position[0]
        self.y = position[1]

    def __str__(self):
        return '(' + str(self.x) + ', ' + str(self.y) + ')'


# from tmk.classes.Connection import Connection


class Connection:
    """
    Objekt za vzpostavljanje povezave s strežnikom.
    """

    def __init__(self, url: str):
        """
        Inicializacija nove povezave.

        Argumenti:
        url: pot do datoteke na strežniku (URL)
        """
        self._url = url
        self._buffer = BytesIO()
        self._pycurlObj = pycurl.Curl()
        self._pycurlObj.setopt(self._pycurlObj.URL, self._url)
        self._pycurlObj.setopt(self._pycurlObj.CONNECTTIMEOUT, 10)
        self._pycurlObj.setopt(self._pycurlObj.WRITEDATA, self._buffer)

    def request(self, debug=False):
        """
        Nalaganje podatkov s strežnika.
        """
        # Počistimo pomnilnik za shranjevanje sporočila
        self._buffer.seek(0, 0)
        self._buffer.truncate()
        # Pošljemo zahtevek na strežnik
        self._pycurlObj.perform()
        # Dekodiramo sporočilo
        msg = self._buffer.getvalue().decode()
        # Izluščimo podatke iz JSON
        try:
            return ujson.loads(msg)
        except ValueError as err:
            if debug:
                print('Napaka pri razclenjevanju datoteke JSON: ' + str(err))
                print('Sporocilo streznika:')
                print(msg)
            return -1

    def test_delay(self, num_iters: int = 10):
        """
        Merjenje zakasnitve pri pridobivanju podatkov o tekmi s strežnika.
        Zgolj informativno.
        """
        sum_time = 0
        for i in range(num_iters):
            start_time = time()
            if self.request() == -1:
                robot_die()
            elapsed_time = time() - start_time
            sum_time += elapsed_time
        return sum_time / num_iters


# ID robota. Spremenite, da ustreza številki označbe, ki je določena vaši ekipi.
ROBOT_ID = 35
# Konfiguracija povezave na strežnik. LASPP strežnik ima naslov "192.168.0.3".
SERVER_IP = "192.168.0.113"
# Datoteka na strežniku s podatki o tekmi.
GAME_STATE_FILE = "game.json"

# Priklop motorjev na izhode.
MOTOR_LEFT_PORT = 'outA'
MOTOR_RIGHT_PORT = 'outD'
MOTOR_GRAB_PORT = 'outC'

# Najvišja dovoljena hitrost motorjev.
SPEED_MAX = 800
# Najvišja dovoljena nazivna hitrost motorjev pri vožnji naravnost.
# Naj bo manjša kot SPEED_MAX, da ima robot še možnost zavijati.
SPEED_BASE_MAX = 700

# Parametri za PID
# Obračanje na mestu
PID_TURN_KP = 1.4
PID_TURN_KI = 0.0
PID_TURN_KD = 0.53
PID_TURN_INT_MAX = 100
# Nazivna hitrost pri vožnji naravnost.
PID_FRWD_KP = 1.0  # 1.0
PID_FRWD_KI = 0.0
PID_FRWD_KD = 0.1  # 0.1
PID_FRWD_INT_MAX = 100
# Zavijanje med vožnjo naravnost
PID_FRWD_TURN_KP = 6.0
PID_FRWD_TURN_KI = 0.0
PID_FRWD_TURN_KD = 2.0
PID_FRWD_TURN_INT_MAX = 100
# Obračanje na mestu S KOCKO
PID_TURN_APPLE_KP = 4.0
PID_TURN_APPLE_KI = 0.0
PID_TURN_APPLE_KD = 0.0
PID_TURN_APPLE_INT_MAX = 100
# Nazivna hitrost pri vožnji naravnost S KOCKO
PID_FRWD_APPLE_KP = 1.0
PID_FRWD_APPLE_KI = 0.0
PID_FRWD_APPLE_KD = 0.0
PID_FRWD_APPLE_INT_MAX = 100
# Zavijanje med vožnjo naravnost
PID_FRWD_TURN_APPLE_KP = 15.0
PID_FRWD_TURN_APPLE_KI = 0.0
PID_FRWD_TURN_APPLE_KD = 0.0
PID_FRWD_TURN_APPLE_INT_MAX = 100

# Dolžina FIFO vrste za hranjenje meritev (oddaljenost in kot do cilja).
HIST_QUEUE_LENGTH = 3

# Razdalje - tolerance
# Dovoljena napaka v oddaljenosti do cilja [mm].
DIST_EPS = 100
# Dovoljena napaka pri obračanju [stopinje].
DIR_EPS = 5
# Bližina cilja [mm].
DIST_NEAR = 100
# Koliko sekund je robot lahko stanju vožnje naravnost v bližini cilja
# (oddaljen manj kot DIST_NEAR), preden sprožimo varnostni mehanizem
# in ga damo v stanje obračanja na mestu.
TIMER_NEAR_TARGET = 3


def get_angle(p1, a1, p2) -> float:
    """
    Izračunaj kot, za katerega se mora zavrteti robot, da bo obrnjen proti točki p2.
    Robot se nahaja v točki p1 in ima smer (kot) a1.
    """
    a = math.degrees(math.atan2(p2.y - p1.y, p2.x - p1.x))
    a_rel = a - a1
    if abs(a_rel) > 180:
        if a_rel > 0:
            a_rel = a_rel - 360
        else:
            a_rel = a_rel + 360

    return a_rel


def get_distance(p1: Point, p2: Point) -> float:
    """
    Evklidska razdalja med dvema točkama na poligonu.
    """
    return math.sqrt((p2.x - p1.x) ** 2 + (p2.y - p1.y) ** 2)


def init_large_motor(port: str) -> LargeMotor:
    """
    Preveri, ali je motor priklopljen na izhod `port`.
    Vrne objekt za motor (LargeMotor).
    """
    motor = LargeMotor(port)
    while not motor.connected:
        print('\nPriklopi motor na izhod ' + port +
              ' in pritisni + spusti gumb DOL.')
        wait_for_button('down')
        motor = LargeMotor(port)
    return motor


def init_medium_motor(port: str) -> MediumMotor:
    """
    Preveri, ali je motor priklopljen na izhod `port`.
    Vrne objekt za motor (LargeMotor).
    """
    motor = MediumMotor(port)
    while not motor.connected:
        print('\nPriklopi motor na izhod ' + port +
              ' in pritisni + spusti gumb DOL.')
        wait_for_button('down')
        motor = MediumMotor(port)
    return motor


def init_sensor_touch() -> TouchSensor:
    """
    Preveri, ali je tipalo za dotik priklopljeno na katerikoli vhod.
    Vrne objekt za tipalo.
    """
    sensor = TouchSensor()
    while not sensor.connected:
        print('\nPriklopi tipalo za dotik in pritisni + spusti gumb DOL.')
        wait_for_button('down')
        sensor = TouchSensor()
    return sensor


def wait_for_button(btn_name: str = 'down'):
    """
    Čakaj v zanki dokler ni gumb z imenom `btn_name` pritisnjen in nato sproščen.
    """
    while not getattr(btn, btn_name):
        pass
    flag = False
    while getattr(btn, btn_name):
        if not flag:
            flag = True


def beep(duration=1000, freq=440):
    """
    Potrobi s frekvenco `freq` za čas `duration`. Klic ne blokira.
    """
    Sound.tone(freq, duration)
    # Če želimo, da blokira, dokler se pisk ne konča.
    # Sound.tone(freq, duration).wait()


def robot_die():
    """
    Končaj s programom na robotu. Ustavi motorje.
    """
    print('KONEC')
    motor_left.stop(stop_action='brake')
    motor_right.stop(stop_action='brake')
    motor_grab.stop(stop_action='brake')
    Sound.play_song((
        ('D4', 'e'),
        ('C4', 'e'),
        ('A3', 'h')))
    sys.exit(0)


def get_closest_good_apple(game_state_arg, robot_pos_arg, closest_id):
    """
    Funkcija vrne najbližje zdravo jabolko
    """
    min_apple = None
    min_dist = float("inf")
    for apple in game_state_arg['apples']:
        if apple['type'] == "appleGood" and apple['id'] != closest_id:
            atm_dist = get_distance(robot_pos_arg, Point(apple['position'][0:2]))
            if atm_dist < min_dist:
                min_dist = atm_dist
                min_apple = apple
    return min_apple


def get_closest_bad_apple(game_state_arg, robot_pos_arg, closest_id):
    """
    Funkcija vrne najbližje gnilo jabolko
    """
    min_apple = None
    min_dist = float("inf")
    for apple in game_state_arg['apples']:
        if apple['type'] == "appleBad" and apple['id'] != closest_id:
            atm_dist = get_distance(robot_pos_arg, Point(apple['position'][0:2]))
            if atm_dist < min_dist:
                min_dist = atm_dist
                min_apple = apple
    return min_apple


def get_robot_pos(game_state_arg, robot_id) -> Point:
    """
    Funkcija vrne trenutno pozicijo robota
    """
    for robot_data_iter in game_state_arg['robots']:
        if robot_data_iter['id'] == robot_id:
            return Point(robot_data['position'][0:2])


def claws_open():
    motor_grab.run_forever(speed_sp=500)
    sleep(0.5)
    motor_grab.stop(stop_action='brake')


def claws_close():
    motor_grab.run_forever(speed_sp=-500)
    sleep(0.5)
    motor_grab.stop(stop_action='brake')


def in_team_one(position):
    x = position.x
    y = position.y
    if 1 < x < 520:
        if 1525 > y > 520:
            return True

    return False


def in_team_two(position):
    x = position.x
    y = position.y
    if 3046 < x < 3559:
        if 1525 > y > 520:
            return True

    return False


def point_transpose(curr: Point, direction):
    if direction < 0:
        direction = -direction
    else:
        direction = 360 - direction

    curr.x += (math.cos(math.radians(direction))) * 150
    curr.y += (math.sin(math.radians(direction))) * 150
    return curr


def check_apple(robot_position: Point, robot_direction):
    new_point = point_transpose(robot_position, robot_direction)
    #DOLOČI MEJE



# -----------------------------------------------------------------------------
# NASTAVITVE TIPAL, MOTORJEV IN POVEZAVE S STREŽNIKOM
# -----------------------------------------------------------------------------
# Nastavimo tipala in gumbe.
print('Priprava tipal ... ', end='', flush=True)
btn = Button()
# sensor_touch = init_sensor_touch()
print('OK!')

# Nastavimo velika motorja. Priklopljena naj bosta na izhoda A in D.
print('Priprava motorjev ... ', end='')
motor_left = init_large_motor(MOTOR_LEFT_PORT)
motor_right = init_large_motor(MOTOR_RIGHT_PORT)
motor_grab = init_medium_motor(MOTOR_GRAB_PORT)
print('OK!')

claws_close()
claws_open()

# Nastavimo povezavo s strežnikom.
url = SERVER_IP + '/' + GAME_STATE_FILE
print('Vspostavljanje povezave z naslovom ' + url + ' ... ', end='', flush=True)
conn = Connection(url)
print('OK!')

# Izmerimo zakasnitev pri pridobivanju podatkov (povprečje num_iters meritev)
print('Zakasnitev v komunikaciji s streznikom ... ', end='', flush=True)
print('%.4f s' % (conn.test_delay(num_iters=10)))

# -----------------------------------------------------------------------------
# PRIPRAVA NA TEKMO
# -----------------------------------------------------------------------------
# Pridobimo podatke o tekmi.
game_state = conn.request()
# Ali naš robot sploh tekmuje? Če tekmuje, ali je team1 ali team2?
team_my_tag = 'undefined'
team_op_tag = 'undefined'

if ROBOT_ID == game_state['team1']['id']:
    team_my_tag = 'team1'
    team_op_tag = 'team2'
elif ROBOT_ID == game_state['team2']['id']:
    team_my_tag = 'team2'
    team_op_tag = 'team1'
else:
    print('Robot ne tekmuje.')
    robot_die()
print('Robot tekmuje in ima interno oznako "' + team_my_tag + '"')

#  Nastavi točko za domov
home = Point(game_state['field']['baskets'][team_my_tag]['topLeft'])
home.x += 270
home.y -= 515

enemyHome = Point(game_state['field']['baskets'][team_op_tag]['topLeft'])
enemyHome.x += 270
enemyHome.y -= 515

# -----------------------------------------------------------------------------
# GLAVNA ZANKA
# -----------------------------------------------------------------------------
print('Izvajam glavno zanko. Prekini jo s pritiskon na tipko DOL.')
print('Cakam na zacetek tekme ...')

# Regulator PID za obračanje na mestu.
# setpoint=0 pomeni, da naj bo kot med robotom in ciljem (target_angle) enak 0.
# Naša regulirana veličina je torej kar napaka kota, ki mora biti 0.
# To velja tudi za regulacijo vožnje naravnost.
PID_turn = PID(
    setpoint=0,
    kp=PID_TURN_KP,
    ki=PID_TURN_KI,
    kd=PID_TURN_KD,
    integral_limit=PID_TURN_INT_MAX)

# PID za vožnjo naravnost - regulira nazivno hitrost za oba motorja,
# ki je odvisna od oddaljenosti od cilja.
# setpoint=0 pomeni, da mora biti razdalja med robotom in ciljem enaka 0.
PID_frwd_base = PID(
    setpoint=0,
    kp=PID_FRWD_KP,
    ki=PID_FRWD_KI,
    kd=PID_FRWD_KD,
    integral_limit=PID_FRWD_INT_MAX)

# PID za obračanje med vožnjo naravnost.
# setpoint=0 pomeni, da naj bo kot med robotom in ciljem (target_angle) enak 0.
PID_frwd_turn = PID(
    setpoint=0,
    kp=PID_FRWD_TURN_KP,
    ki=PID_FRWD_TURN_KI,
    kd=PID_FRWD_TURN_KD,
    integral_limit=PID_FRWD_TURN_INT_MAX)

# PID za obračanje na mestu s jabolkom
PID_turn_apple = PID(
    setpoint=0,
    kp=PID_TURN_APPLE_KP,
    ki=PID_TURN_APPLE_KI,
    kd=PID_TURN_APPLE_KD,
    integral_limit=PID_TURN_APPLE_INT_MAX)

# PID za obračanje na mestu s jabolkom
PID_frwd_base_apple = PID(
    setpoint=0,
    kp=PID_FRWD_APPLE_KP,
    ki=PID_FRWD_APPLE_KI,
    kd=PID_FRWD_APPLE_KD,
    integral_limit=PID_FRWD_APPLE_INT_MAX)

pid_frwd_base_apple_multiplier = 1

# PID za obračanje na mestu s jabolkom
PID_frwd_turn_apple = PID(
    setpoint=0,
    kp=PID_FRWD_TURN_APPLE_KP,
    ki=PID_FRWD_TURN_APPLE_KI,
    kd=PID_FRWD_TURN_APPLE_KD,
    integral_limit=PID_FRWD_TURN_APPLE_INT_MAX)

# Hitrost na obeh motorjih.
speed_right = 0
speed_left = 0

# Zgodovina (okno) zadnjih nekaj vrednosti meritev,
# implementirana kot vrsta FIFO.
robot_dir_hist = deque([180.0] * HIST_QUEUE_LENGTH)
robot_dist_hist = deque([math.inf] * HIST_QUEUE_LENGTH)

# Meritve direction
robot_dir_data_id = 0

# Merimo čas obhoda zanke. Za visoko odzivnost robota je zelo pomembno,
# da je ta čas čim krajši.
t_old = time()

# Začetno stanje.
state = State.GET_APPLE
# Prejšnje stanje.
state_old = -1
# Prejšnje najbližje jabolko
closest_apple_old = Point([1750, 750])
# Id prejšnjega najbližjega jabolka
closest_apple_id_old = -1
# Trenutni target
target = None
# Razdalja med robotom in ciljem.
target_dist = 0
# Kot med robotom in ciljem.
target_angle = 0

file = open('pid_data' + str(robot_dir_data_id) + '.txt', 'w')

do_main_loop = True
while do_main_loop and not btn.down:

    time_now = time()
    loop_time = time_now - t_old
    t_old = time_now

    # Zaznaj spremembo stanja.
    if state != state_old:
        state_changed = True
    else:
        state_changed = False
    state_old = state

    # Osveži stanje tekme.
    game_state = conn.request()
    if game_state == -1:
        print('Napaka v paketu, ponovni poskus ...')
    else:
        game_on = game_state['gameOn']
        time_left = game_state['timeLeft']

        # Pridobi pozicijo in orientacijo svojega robota;
        # najprej pa ga poišči v tabeli vseh robotov na poligonu.
        robot_pos = None
        robot_dir = None
        for robot_data in game_state['robots']:
            if robot_data['id'] == ROBOT_ID:
                robot_pos = Point(robot_data['position'][0:2])
                robot_dir = robot_data['direction']
                # Popravki za TAG
                # robot_pos.x += 30 * math.cos(robot_dir)
                # robot_pos.y += 30 * math.sin(robot_dir)
        # Ali so podatki o robotu veljavni? Če niso, je zelo verjetno,
        # da sistem ne zazna oznake na robotu.
        robot_alive = (robot_pos is not None) and (robot_dir is not None)

        # Če tekma poteka in je oznaka robota vidna na kameri,
        # potem izračunamo novo hitrost na motorjih.
        # Sicer motorje ustavimo.
        if game_on and robot_alive:

            # Spremljaj zgodovino meritev kota in oddaljenosti.
            # Odstrani najstarejši element in dodaj novega - princip FIFO.
            robot_dir_hist.popleft()
            robot_dir_hist.append(target_angle)
            robot_dist_hist.popleft()
            robot_dist_hist.append(target_dist)

            if state == State.GET_APPLE:
                # Target closest apple
                print("State GET_APPLE")

                closest_apple = get_closest_good_apple(game_state, robot_pos, closest_apple_id_old)
                closest_apple_id_old = closest_apple['id']
                target = Point(closest_apple['position'])
                print(str(target.x) + " " + str(target.y))

                target_dist = get_distance(robot_pos, target)
                target_angle = get_angle(robot_pos, robot_dir, target)

                speed_right = 0
                speed_left = 0

                # Preverimo, ali je robot na ciljni točki.
                # Če ni, ga tja pošljemo.
                if target_dist > DIST_EPS:
                    state = State.GET_TURN
                    robot_near_target_old = False
                else:
                    state = State.HOME

            elif state == State.HOME:
                print("State HOME")
                state_old_target = State.HOME

                target = home

                target_dist = get_distance(robot_pos, target)
                target_angle = get_angle(robot_pos, robot_dir, target)

                print(str(target.x) + " " + str(target.y))

                # zakaj tle ni speed_right = 0, speed_left = 0, v GET_APPLE pa je?
                speed_right = 0
                speed_left = 0
                # Preverimo, ali je robot na ciljni točki.
                # Če ni, ga tja pošljemo.
                #
                if target_dist > DIST_EPS:
                    state = State.HOME_TURN
                    robot_near_target_old = False
                else:
                    state = State.GET_APPLE

                # state = State.HOME_TURN

            elif state == State.GET_TURN:
                # Obračanje robota na mestu, da bo obrnjen proti cilju.
                print("State GET_TURN")

                closest_apple = get_closest_good_apple(game_state, robot_pos, closest_apple_id_old)
                closest_apple_old = closest_apple

                # Če se pozicija najbližjega jabolka ni spremenila
                # ali pa smo namenjeni domov (aka. že imamo jabolko)
                # if closest_apple == closest_apple_old or state_old_target == State.HOME:

                target_dist = get_distance(robot_pos, target)
                target_angle = get_angle(robot_pos, robot_dir, target)

                # beleženje za izris grafa
                file.write(str(target_angle) + ',' + str(time_now) + '\n')

                if state_changed:
                    # Če smo ravno prišli v to stanje, najprej ponastavimo PID.
                    PID_turn.reset()

                # Ali smo že dosegli ciljni kot?
                # Zadnjih nekaj obhodov zanke mora biti absolutna vrednost
                # napake kota manjša od DIR_EPS.
                err = [abs(a) > DIR_EPS for a in robot_dir_hist]

                if sum(err) == 0:
                    # Vse vrednosti so znotraj tolerance, zamenjamo stanje.
                    speed_right = 0
                    speed_left = 0
                    state = State.GET_STRAIGHT

                    # Naslednjič naredimo nov file
                    robot_dir_data_id += 1

                else:
                    # Reguliramo obračanje.
                    # Ker se v regulatorju trenutna napaka izračuna kot:
                    #   error = setpoint - measurement,
                    # dobimo negativno vrednost, ko se moramo zavrteti
                    # v pozitivno smer.
                    # Primer:
                    #   Robot ima smer 90 stopinj (obrnjen je proti "severu").
                    #   Cilj se nahaja na njegovi levi in da ga doseže,
                    #   se mora obrniti za 90 stopinj.
                    #       setpoint=0
                    #       target_angle = measurement = 90
                    #       error = setpoint - measurement = -90
                    #       u = funkcija, odvisna od error in parametrov PID.
                    #   Če imamo denimo kp = 1, ki = kd = 0, potem bo u = -90.
                    #   Robot se mora zavrteti v pozitivno smer,
                    #   torej z desnim kolesom naprej in levim nazaj.
                    #   Zato:
                    #   speed_right = -u
                    #   speed_left = u
                    #   Lahko bi tudi naredili droben trik in bi rekli:
                    #       measurement= -target_angle.
                    #   V tem primeru bi bolj intuitivno nastavili
                    #   speed_right = u in speed_left = -u.
                    u = PID_turn.update(measurement=target_angle)
                    speed_right = -u
                    speed_left = u
                # else:
                # probat je treba dve stvari
                # state = State.GET_APPLE

            elif state == State.GET_STRAIGHT:
                # Vožnja robota naravnost proti ciljni točki.
                print("State GET_STRAIGHT")

                closest_apple = get_closest_good_apple(game_state, robot_pos, closest_apple_id_old)
                closest_apple_old = closest_apple

                # if closest_apple == closest_apple_old or state_old_target == State.HOME:

                target_dist = get_distance(robot_pos, target)
                target_angle = get_angle(robot_pos, robot_dir, target)

                # beleženje za izris grafa
                # file.write(str(target_angle) + ',' + str(time_now) + '\n')

                # Vmes bi radi tudi zavijali, zato uporabimo dva regulatorja.
                if state_changed:
                    # Ponastavi regulatorja PID.
                    PID_frwd_base.reset()
                    PID_frwd_turn.reset()
                    timer_near_target = TIMER_NEAR_TARGET

                # Ali smo blizu cilja?
                robot_near_target = target_dist < DIST_NEAR
                if not robot_near_target_old and robot_near_target:
                    # Vstopili smo v bližino cilja.
                    # Začnimo odštevati varnostno budilko.
                    timer_near_target = TIMER_NEAR_TARGET
                if robot_near_target:
                    timer_near_target = timer_near_target - loop_time
                robot_near_target_old = robot_near_target

                # Ali smo že na cilju?
                # Zadnjih nekaj obhodov zanke mora biti razdalja do cilja
                # manjša ali enaka DIST_EPS.
                err_eps = [d > DIST_EPS for d in robot_dist_hist]
                if sum(err_eps) == 0:
                    # Razdalja do cilja je znotraj tolerance, zamenjamo stanje.
                    speed_right = 0
                    speed_left = 0
                    # Zato da lahko grabamo
                    motor_right.run_forever(speed_sp=-speed_right)
                    motor_left.run_forever(speed_sp=-speed_left)
                    claws_close()
                    print("Pobrali smo jabolko")
                    state = State.HOME

                elif timer_near_target < 0:
                    # Smo morda blizu cilja, in je varnostna budilka potekla?
                    speed_right = 0
                    speed_left = 0
                    state = State.GET_TURN

                else:
                    u_turn = PID_frwd_turn.update(measurement=target_angle)
                    # Ker je napaka izračunana kot setpoint - measurement in
                    # smo nastavili setpoint na 0, bomo v primeru u_base dobili
                    # negativne vrednosti takrat, ko se bo robot moral premikati
                    # naprej. Zato dodamo minus pri izračunu hitrosti motorjev.
                    u_base = PID_frwd_base.update(measurement=target_dist)
                    # Omejimo nazivno hitrost, ki je enaka za obe kolesi,
                    # da imamo še manevrski prostor za zavijanje.
                    u_base = min(max(u_base, -SPEED_BASE_MAX), SPEED_BASE_MAX)
                    speed_right = -u_base - u_turn
                    speed_left = -u_base + u_turn

                # else:
                # probat je treba dve stvari
                # state = State.GET_APPLE

            elif state == State.HOME_TURN:
                # Obračanje robota na mestu, da bo obrnjen proti cilju.
                print("State HOME_TURN")

                closest_apple = get_closest_good_apple(game_state, robot_pos, closest_apple_id_old)
                closest_apple_old = closest_apple

                # Če se pozicija najbližjega jabolka ni spremenila
                # ali pa smo namenjeni domov (aka. že imamo jabolko)
                # if closest_apple == closest_apple_old or state_old_target == State.HOME:

                target_dist = get_distance(robot_pos, target)
                target_angle = get_angle(robot_pos, robot_dir, target)

                if state_changed:
                    # Če smo ravno prišli v to stanje, najprej ponastavimo PID.
                    PID_turn_apple.reset()

                # Ali smo že dosegli ciljni kot?
                # Zadnjih nekaj obhodov zanke mora biti absolutna vrednost
                # napake kota manjša od DIR_EPS.
                err = [abs(a) > DIR_EPS for a in robot_dir_hist]

                if sum(err) == 0:
                    # Vse vrednosti so znotraj tolerance, zamenjamo stanje.
                    speed_right = 0
                    speed_left = 0
                    state = State.HOME_STRAIGHT
                else:
                    # Reguliramo obračanje.
                    # Ker se v regulatorju trenutna napaka izračuna kot:
                    #   error = setpoint - measurement,
                    # dobimo negativno vrednost, ko se moramo zavrteti
                    # v pozitivno smer.
                    # Primer:
                    #   Robot ima smer 90 stopinj (obrnjen je proti "severu").
                    #   Cilj se nahaja na njegovi levi in da ga doseže,
                    #   se mora obrniti za 90 stopinj.
                    #       setpoint=0
                    #       target_angle = measurement = 90
                    #       error = setpoint - measurement = -90
                    #       u = funkcija, odvisna od error in parametrov PID.
                    #   Če imamo denimo kp = 1, ki = kd = 0, potem bo u = -90.
                    #   Robot se mora zavrteti v pozitivno smer,
                    #   torej z desnim kolesom naprej in levim nazaj.
                    #   Zato:
                    #   speed_right = -u
                    #   speed_left = u
                    #   Lahko bi tudi naredili droben trik in bi rekli:
                    #       measurement= -target_angle.
                    #   V tem primeru bi bolj intuitivno nastavili
                    #   speed_right = u in speed_left = -u.
                    u = PID_turn_apple.update(measurement=target_angle)
                    speed_right = -u
                    speed_left = u
                # else:
                # probat je treba dve stvari
                # state = State.GET_APPLE

            elif state == State.HOME_STRAIGHT:
                # Vožnja robota naravnost proti ciljni točki.
                print("State HOME_STRAIGHT")

                closest_apple = get_closest_good_apple(game_state, robot_pos, closest_apple_id_old)
                closest_apple_old = closest_apple

                # if closest_apple == closest_apple_old or state_old_target == State.HOME:
                smoDoma = False
                if team_my_tag == "team1":
                    smoDoma = in_team_one(robot_pos)
                else:
                    smoDoma = in_team_two(robot_pos)

                target_dist = get_distance(robot_pos, target)
                target_angle = get_angle(robot_pos, robot_dir, target)

                # Vmes bi radi tudi zavijali, zato uporabimo dva regulatorja.
                if state_changed:
                    # Ponastavi regulatorja PID.
                    PID_frwd_base_apple.reset()
                    PID_frwd_turn_apple.reset()
                    timer_near_target = TIMER_NEAR_TARGET
                    pid_frwd_base_apple_multiplier = 1

                # Ali smo blizu cilja?
                robot_near_target = target_dist < DIST_NEAR
                if not robot_near_target_old and robot_near_target:
                    # Vstopili smo v bližino cilja.
                    # Začnimo odštevati varnostno budilko.
                    pid_frwd_base_apple_multiplier = 0.1
                    timer_near_target = TIMER_NEAR_TARGET
                if robot_near_target:
                    timer_near_target = timer_near_target - loop_time
                robot_near_target_old = robot_near_target

                # Ali smo že na cilju?
                # Zadnjih nekaj obhodov zanke mora biti razdalja do cilja
                # manjša ali enaka DIST_EPS.
                err_eps = [d > DIST_EPS for d in robot_dist_hist]
                if sum(err_eps) == 0 or smoDoma:
                    # Razdalja do cilja je znotraj tolerance, zamenjamo stanje.
                    speed_right = 0
                    speed_left = 0
                    motor_right.run_forever(speed_sp=-speed_right)
                    motor_left.run_forever(speed_sp=-speed_left)
                    claws_open()
                    print("Prišli smo domov")
                    state = State.BACK_OFF
                    smoDoma = False

                elif timer_near_target < 0:
                    # Smo morda blizu cilja, in je varnostna budilka potekla?
                    speed_right = 0
                    speed_left = 0
                    state = State.HOME_TURN

                else:
                    u_turn = PID_frwd_turn_apple.update(measurement=target_angle) * pid_frwd_base_apple_multiplier
                    # Ker je napaka izračunana kot setpoint - measurement in
                    # smo nastavili setpoint na 0, bomo v primeru u_base dobili
                    # negativne vrednosti takrat, ko se bo robot moral premikati
                    # naprej. Zato dodamo minus pri izračunu hitrosti motorjev.
                    u_base = PID_frwd_base_apple.update(measurement=target_dist) * pid_frwd_base_apple_multiplier
                    # Omejimo nazivno hitrost, ki je enaka za obe kolesi,
                    # da imamo še manevrski prostor za zavijanje.
                    u_base = min(max(u_base, -SPEED_BASE_MAX), SPEED_BASE_MAX)
                    speed_right = -u_base - u_turn
                    speed_left = -u_base + u_turn

                # else:
                # probat je treba dve stvari
                # state = State.GET_APPLE

            elif state == State.BACK_OFF:
                print("State BACK_OFF")
                motor_right.run_forever(speed_sp=200)
                motor_left.run_forever(speed_sp=200)
                sleep(1)
                motor_left.stop(stop_action='brake')
                motor_right.stop(stop_action='brake')
                state = State.GET_APPLE

            # Omejimo vrednosti za hitrosti na motorjih.
            speed_right = round(
                min(
                    max(speed_right, -SPEED_MAX),
                    SPEED_MAX)
            )
            speed_left = round(
                min(
                    max(speed_left, -SPEED_MAX),
                    SPEED_MAX)
            )

            # Vrtimo motorje.
            motor_right.run_forever(speed_sp=-speed_right)
            motor_left.run_forever(speed_sp=-speed_left)

        else:
            # Robot bodisi ni viden na kameri bodisi tema ne teče.
            motor_left.stop(stop_action='brake')
            motor_right.stop(stop_action='brake')

# Konec programa
robot_die()

#!/usr/bin/env python3
"""Mystery Profile — a multiplayer clue-guessing party game to practice English.

Zero dependencies: runs on the Python 3 that ships with macOS.
    python3 server.py            # then open http://<this-computer-ip>:8000 on the phones
"""
import json
import os
import random
import secrets
import socket
import sys
import threading
import time
import unicodedata
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ROOT = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(ROOT, "static")
PORT = int(os.environ.get("PORT", "8000"))

with open(os.path.join(ROOT, "cards.json"), encoding="utf-8") as f:
    CARDS = json.load(f)["cards"]
CATEGORIES = sorted({c["category"] for c in CARDS})

CLUES_PER_CARD = 10
READER_BONUS = 2
MAX_PLAYERS = 8
MIN_PLAYERS = 2
POLL_TIMEOUT = 25
ROOM_TTL = 6 * 3600
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ"

lock = threading.RLock()
changed = threading.Condition(lock)
rooms = {}


class GameError(Exception):
    pass


# ---------------------------------------------------------------- answer check

def normalize(text):
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9 ]", " ", text.lower())
    words = [w for w in text.split() if w not in ("the", "a", "an")]
    return " ".join(words)


def distance(a, b):
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def is_correct(guess, card):
    g = normalize(guess)
    if not g:
        return False
    for option in [card["answer"]] + card.get("aliases", []):
        o = normalize(option)
        tolerance = 2 if len(o) >= 8 else 1 if len(o) >= 5 else 0
        if distance(g, o) <= tolerance or g.replace(" ", "") == o.replace(" ", ""):
            return True
    return False


# ---------------------------------------------------------------- room model

def new_code():
    while True:
        code = "".join(random.choice(CODE_ALPHABET) for _ in range(4))
        if code not in rooms:
            return code


def new_room(host_name):
    code = new_code()
    room = {
        "code": code,
        "players": [],
        "hostId": None,
        "phase": "lobby",  # lobby | playing | cardEnd | gameOver
        "settings": {"target": 40, "categories": list(CATEGORIES)},
        "deck": [],
        "readerIdx": -1,
        "card": None,
        "revealed": [],
        "guessers": [],
        "turn": 0,
        "step": "pick",  # pick | guess
        "lastGuess": None,
        "result": None,
        "log": [],
        "round": 0,
        "version": 1,
        "touched": time.time(),
    }
    rooms[code] = room
    player = add_player(room, host_name)
    room["hostId"] = player["id"]
    return room, player


def add_player(room, name):
    name = (name or "").strip()[:16]
    if not name:
        raise GameError("Please type your name.")
    if len(room["players"]) >= MAX_PLAYERS:
        raise GameError("This game is full (8 players max).")
    if any(p["name"].lower() == name.lower() for p in room["players"]):
        raise GameError("Someone already has that name. Try another one.")
    player = {"id": secrets.token_urlsafe(9), "name": name, "score": 0,
              "seen": time.time(), "polls": 0, "cards": 0}
    room["players"].append(player)
    return player


def get_player(room, pid):
    for p in room["players"]:
        if p["id"] == pid:
            return p
    raise GameError("You are not in this game.")


def is_online(p):
    return p["polls"] > 0 or time.time() - p["seen"] < 8


def bump(room, message=None):
    if message:
        room["log"] = (room["log"] + [message])[-8:]
    room["version"] += 1
    room["touched"] = time.time()
    changed.notify_all()


def reader(room):
    return room["players"][room["readerIdx"] % len(room["players"])]


def current_guesser(room):
    if not room["guessers"]:
        return None
    return get_player(room, room["guessers"][room["turn"] % len(room["guessers"])])


def points_now(room):
    """Points a correct guess is worth right now (counting the clue about to be picked)."""
    used = len(room["revealed"]) + (1 if room["step"] == "pick" else 0)
    return max(1, CLUES_PER_CARD + 1 - used)


# ---------------------------------------------------------------- game flow

def build_deck(room):
    cats = room["settings"]["categories"] or CATEGORIES
    deck = [i for i, c in enumerate(CARDS) if c["category"] in cats]
    random.shuffle(deck)
    room["deck"] = deck


def start_card(room):
    if not room["deck"]:
        build_deck(room)
    base = CARDS[room["deck"].pop()]
    clues = base["clues"][:CLUES_PER_CARD]
    random.shuffle(clues)
    room["card"] = dict(base, clues=clues)
    room["readerIdx"] += 1
    rd = reader(room)
    n = len(room["players"])
    start = room["readerIdx"] % n
    room["guessers"] = [room["players"][(start + k) % n]["id"] for k in range(1, n)]
    room["turn"] = 0
    skip_offline(room)
    room["revealed"] = []
    room["step"] = "pick"
    room["lastGuess"] = None
    room["result"] = None
    room["round"] += 1
    room["phase"] = "playing"
    bump(room, "New card! %s is the reader." % rd["name"])


def skip_offline(room):
    for _ in range(len(room["guessers"])):
        if is_online(current_guesser(room)):
            return
        room["turn"] += 1


def next_turn(room):
    if len(room["revealed"]) >= CLUES_PER_CARD:
        end_card(room, None)
        return
    room["turn"] += 1
    skip_offline(room)
    room["step"] = "pick"


def end_card(room, winner, clues_used=None):
    rd = reader(room)
    result = {"answer": room["card"]["answer"], "category": room["card"]["category"],
              "winnerId": None, "points": 0, "readerId": rd["id"], "readerPoints": 0,
              "cluesUsed": clues_used or len(room["revealed"]),
              "clues": room["card"]["clues"], "fact": room["card"].get("fact", "")}
    if winner:
        pts = CLUES_PER_CARD + 1 - max(1, result["cluesUsed"])
        winner["score"] += pts
        winner["cards"] += 1
        rd["score"] += READER_BONUS
        result.update(winnerId=winner["id"], points=pts, readerPoints=READER_BONUS)
    room["result"] = result
    room["phase"] = "cardEnd"
    if winner:
        bump(room, "%s got it: %s! +%d" % (winner["name"], result["answer"], result["points"]))
    else:
        bump(room, "Nobody got it. It was %s." % result["answer"])


def finish_or_next(room):
    target = room["settings"]["target"]
    if any(p["score"] >= target for p in room["players"]):
        room["phase"] = "gameOver"
        bump(room, "Game over!")
    else:
        start_card(room)


def act(room, player, data):
    kind = data.get("type")
    is_host = player["id"] == room["hostId"]
    phase = room["phase"]

    if kind == "settings":
        if not is_host or phase != "lobby":
            raise GameError("Only the host can change settings.")
        target = int(data.get("target", room["settings"]["target"]))
        cats = [c for c in data.get("categories", room["settings"]["categories"]) if c in CATEGORIES]
        room["settings"] = {"target": max(10, min(200, target)), "categories": cats or list(CATEGORIES)}
        bump(room)

    elif kind == "start":
        if not is_host:
            raise GameError("Only the host can start.")
        if len(room["players"]) < MIN_PLAYERS:
            raise GameError("You need at least 2 players.")
        for p in room["players"]:
            p["score"] = 0
            p["cards"] = 0
        room["readerIdx"] = random.randrange(len(room["players"])) - 1
        room["round"] = 0
        build_deck(room)
        start_card(room)

    elif kind == "pick":
        if phase != "playing" or room["step"] != "pick" or current_guesser(room) is not player:
            raise GameError("It's not your turn to pick.")
        n = int(data.get("n", 0))
        if n < 1 or n > CLUES_PER_CARD or n in room["revealed"]:
            raise GameError("Pick another number.")
        room["revealed"].append(n)
        room["step"] = "guess"
        room["lastGuess"] = None
        bump(room, "%s opened clue #%d." % (player["name"], n))

    elif kind == "guess":
        if phase != "playing" or room["step"] != "guess" or current_guesser(room) is not player:
            raise GameError("It's not your turn to guess.")
        text = (data.get("text") or "").strip()[:60]
        if not text:
            raise GameError("Type a guess or pass.")
        ok = is_correct(text, room["card"])
        room["lastGuess"] = {"playerId": player["id"], "text": text, "correct": ok,
                             "cluesUsed": len(room["revealed"])}
        if ok:
            end_card(room, player)
        else:
            bump(room, '%s guessed "%s" — wrong.' % (player["name"], text))
            next_turn(room)
            bump(room)

    elif kind == "pass":
        if phase != "playing" or room["step"] != "guess" or current_guesser(room) is not player:
            raise GameError("It's not your turn.")
        room["lastGuess"] = None
        bump(room, "%s passed." % player["name"])
        next_turn(room)
        bump(room)

    elif kind == "judge":  # reader heard the answer out loud
        if phase != "playing" or reader(room) is not player:
            raise GameError("Only the reader can judge.")
        g = current_guesser(room)
        if room["step"] != "guess":
            raise GameError("Wait for a clue to be opened.")
        if data.get("correct"):
            end_card(room, g)
        else:
            bump(room, "%s's answer was wrong." % g["name"])
            next_turn(room)
            bump(room)

    elif kind == "accept":  # reader overrides a typed guess the checker rejected
        lg = room["lastGuess"]
        if reader(room) is not player or not lg or lg["correct"]:
            raise GameError("Nothing to accept.")
        if phase == "cardEnd" and room["result"]["winnerId"]:
            raise GameError("This card is already over.")
        if len(room["revealed"]) != lg["cluesUsed"] and phase == "playing":
            raise GameError("Too late — another clue was opened.")
        lg["correct"] = True
        end_card(room, get_player(room, lg["playerId"]), lg["cluesUsed"])

    elif kind == "skipTurn":
        if phase != "playing" or not (is_host or reader(room) is player):
            raise GameError("Only the reader or host can skip a turn.")
        bump(room, "%s's turn was skipped." % current_guesser(room)["name"])
        if room["step"] == "pick":
            room["turn"] += 1
            skip_offline(room)
        else:
            next_turn(room)
        bump(room)

    elif kind == "skipCard":
        if phase != "playing" or not (is_host or reader(room) is player):
            raise GameError("Only the reader or host can skip the card.")
        end_card(room, None)

    elif kind == "next":
        if phase != "cardEnd" or not (is_host or reader(room) is player):
            raise GameError("Only the reader or host can go on.")
        finish_or_next(room)

    elif kind == "restart":
        if not is_host:
            raise GameError("Only the host can restart.")
        room["phase"] = "lobby"
        room["log"] = []
        for p in room["players"]:
            p["score"] = 0
            p["cards"] = 0
        bump(room)

    elif kind == "leave":
        remove_player(room, player)

    else:
        raise GameError("Unknown action.")


def remove_player(room, player):
    pid = player["id"]
    in_game = room["phase"] in ("playing", "cardEnd")
    was_reader = in_game and reader(room) is player
    was_current = room["phase"] == "playing" and current_guesser(room) is player
    idx = room["players"].index(player)
    rpos = room["readerIdx"] % len(room["players"])
    room["players"].remove(player)
    if not room["players"]:
        rooms.pop(room["code"], None)
        changed.notify_all()
        return
    if room["hostId"] == pid:
        room["hostId"] = room["players"][0]["id"]
    # keep the same reader (or, if the reader left, let the next player read)
    room["readerIdx"] = rpos - 1 if idx <= rpos else rpos
    if pid in room["guessers"]:
        pos = room["guessers"].index(pid)
        cur = room["turn"] % len(room["guessers"])
        room["guessers"].remove(pid)
        room["turn"] = cur - 1 if pos < cur else cur
    bump(room, "%s left the game." % player["name"])
    if in_game and len(room["players"]) < MIN_PLAYERS:
        room["phase"] = "lobby"
        bump(room)
    elif room["phase"] == "playing" and was_reader:
        start_card(room)
    elif was_current:
        room["step"] = "pick"
        skip_offline(room)
        bump(room)


# ---------------------------------------------------------------- views

def view(room, me):
    players = [{"id": p["id"], "name": p["name"], "score": p["score"], "cards": p["cards"],
                "online": is_online(p)} for p in room["players"]]
    state = {"version": room["version"], "code": room["code"], "you": me["id"],
             "hostId": room["hostId"], "phase": room["phase"], "settings": room["settings"],
             "allCategories": CATEGORIES, "players": players, "log": room["log"],
             "cluesPerCard": CLUES_PER_CARD, "readerBonus": READER_BONUS, "lanUrl": LAN_URL,
             "minPlayers": MIN_PLAYERS, "maxPlayers": MAX_PLAYERS}
    if room["phase"] in ("playing", "cardEnd") and room["card"]:
        card = room["card"]
        rd = reader(room)
        cg = current_guesser(room)
        g = {"round": room["round"], "category": card["category"], "readerId": rd["id"],
             "currentId": cg["id"] if cg else None, "step": room["step"],
             "revealed": [{"n": n, "text": card["clues"][n - 1]} for n in room["revealed"]],
             "pointsNow": points_now(room), "lastGuess": room["lastGuess"],
             "guessers": room["guessers"]}
        if rd is me:
            g["secret"] = {"answer": card["answer"],
                           "clues": [{"n": i + 1, "text": t} for i, t in enumerate(card["clues"])]}
        if room["phase"] == "cardEnd":
            g["result"] = room["result"]
        state["game"] = g
    return state


# ---------------------------------------------------------------- http

MIME = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
        ".json": "application/json", ".webmanifest": "application/manifest+json"}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if "--verbose" in sys.argv:
            super().log_message(fmt, *args)

    def send_json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/api/state":
            return self.poll(parse_qs(url.query))
        path = "/index.html" if url.path == "/" else url.path
        file = os.path.normpath(os.path.join(STATIC, path.lstrip("/")))
        if not file.startswith(STATIC) or not os.path.isfile(file):
            self.send_error(404)
            return
        with open(file, "rb") as fh:
            body = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(os.path.splitext(file)[1], "application/octet-stream"))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def poll(self, q):
        code = (q.get("room", [""])[0]).upper()
        pid = q.get("player", [""])[0]
        since = int(q.get("v", ["0"])[0] or 0)
        with lock:
            room = rooms.get(code)
            try:
                if not room:
                    raise GameError("Game not found.")
                me = get_player(room, pid)
            except GameError as e:
                return self.send_json({"error": str(e)}, 404)
            came_back = not is_online(me)
            me["polls"] += 1
            me["seen"] = time.time()
            if came_back:
                bump(room)
            deadline = time.time() + POLL_TIMEOUT
            try:
                while room["version"] <= since and rooms.get(code) is room and me in room["players"]:
                    left = deadline - time.time()
                    if left <= 0:
                        break
                    changed.wait(left)
            finally:
                me["polls"] -= 1
                me["seen"] = time.time()
            if rooms.get(code) is not room or me not in room["players"]:
                return self.send_json({"error": "You are no longer in this game."}, 404)
            state = view(room, me)
        self.send_json(state)

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self.send_json({"error": "Bad request."}, 400)
        path = urlparse(self.path).path
        with lock:
            try:
                if path == "/api/create":
                    room, player = new_room(data.get("name"))
                    bump(room)
                    return self.send_json({"room": room["code"], "player": player["id"]})
                if path == "/api/join":
                    room = rooms.get((data.get("room") or "").strip().upper())
                    if not room:
                        raise GameError("Game not found. Check the code.")
                    player = add_player(room, data.get("name"))
                    if room["phase"] == "playing":
                        room["guessers"].append(player["id"])
                    bump(room, "%s joined the game." % player["name"])
                    return self.send_json({"room": room["code"], "player": player["id"]})
                if path == "/api/action":
                    room = rooms.get((data.get("room") or "").upper())
                    if not room:
                        raise GameError("Game not found.")
                    act(room, get_player(room, data.get("player")), data)
                    return self.send_json({"ok": True})
            except GameError as e:
                return self.send_json({"error": str(e)}, 400)
        self.send_error(404)


def janitor():
    while True:
        time.sleep(600)
        with lock:
            for code in [c for c, r in rooms.items() if time.time() - r["touched"] > ROOM_TTL]:
                rooms.pop(code, None)


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "localhost"


LAN_URL = "http://%s:%d" % (lan_ip(), PORT)

if __name__ == "__main__":
    threading.Thread(target=janitor, daemon=True).start()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.daemon_threads = True
    print("\n  Mystery Profile is running!", flush=True)
    print("  On this computer:  http://localhost:%d" % PORT)
    print("  On phones (same Wi-Fi):  %s\n" % LAN_URL)
    print("  %d cards loaded. Press Ctrl+C to stop.\n" % len(CARDS), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass

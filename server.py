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
LEVEL_ORDER = ["A1", "A2", "B1", "B2", "C1"]
LEVELS = sorted({c.get("level", "B1") for c in CARDS},
                key=lambda l: LEVEL_ORDER.index(l) if l in LEVEL_ORDER else 99)
DEFAULT_LEVELS = ["B1"] if "B1" in LEVELS else LEVELS[:1]
CARD_COUNTS = {}
for _c in CARDS:
    _k = _c.get("level", "B1")
    CARD_COUNTS[_k] = CARD_COUNTS.get(_k, 0) + 1
    CARD_COUNTS[_k + "/" + _c["category"]] = CARD_COUNTS.get(_k + "/" + _c["category"], 0) + 1

CLUES_PER_CARD = 10
OWN_LEVEL_SHARE = 0.7   # how often a card follows the level of the player who guesses first
CARD_POINTS = 10        # split between the guesser and the reader on every card
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


def singular(word):
    """Rough singular, so 'boots' and 'boot' count as the same guess."""
    for end, cut in (("ies", 3), ("ses", 2), ("xes", 2), ("hes", 2), ("s", 1)):
        if word.endswith(end) and not word.endswith("ss") and len(word) - cut >= 3:
            return word[:-cut] + ("y" if end == "ies" else "")
    return word


def stem_words(text):
    return " ".join(singular(w) for w in text.split())


PHONETIC_SUBS = [("ough", "f"), ("augh", "af"), ("ph", "f"), ("sch", "sk"), ("sh", "x"),
                 ("ch", "x"), ("th", "t"), ("wh", "w"), ("qu", "kw"), ("ck", "k"),
                 ("kn", "n"), ("gn", "n"), ("wr", "r"), ("mb", "m"), ("ce", "se"),
                 ("ci", "si"), ("cy", "sy"), ("c", "k"), ("z", "s"), ("x", "ks"), ("y", "i")]


def phonetic(text):
    """A rough 'how it sounds' key: 'scissors' and 'sizors' collapse to the same string."""
    out = []
    for word in text.split():
        w = word
        for a, b in PHONETIC_SUBS:
            w = w.replace(a, b)
        w = w[:1] + re.sub(r"[aeiou]", "", w[1:])     # vowels are the usual doubt
        w = re.sub(r"(.)\1+", r"\1", w)               # double letters sound the same
        out.append(w or word[:1])
    return " ".join(out)


def tolerance_for(text):
    """How many typos to forgive: about one per four letters, never more than three."""
    letters = len(text.replace(" ", ""))
    return 0 if letters < 4 else min(3, max(1, letters // 4))


def forms(text):
    n = normalize(text)
    s = stem_words(n)
    return {n, s, n.replace(" ", ""), s.replace(" ", "")}


def load_words():
    """The English used in the deck itself: the words a player might actually mean.
    A guess that is one of these is a different word, not a misspelling."""
    words = set()
    for card in CARDS:
        for clue in card["clues"]:
            for w in re.findall(r"[a-z]+", clue.lower()):
                if len(w) >= 3:
                    words.add(w)
                    words.add(singular(w))
    return words


WORDS = load_words()


def real_word(text):
    return all(w in WORDS for w in text.split())


# every answer and alias in the deck, so a typo is never "corrected" into another card
ALL_ANSWERS = set()
for _c in CARDS:
    for _name in [_c["answer"]] + _c.get("aliases", []):
        ALL_ANSWERS |= forms(_name)


def is_correct(guess, card):
    g = normalize(guess)
    if not g:
        return False
    options = [card["answer"]] + card.get("aliases", [])
    mine = set()
    for option in options:
        mine |= forms(option)
    if forms(g) & mine:                  # same word apart from case, accents, plural or spacing
        return True
    if any(ch.isdigit() for ch in "".join(options)):
        return False                     # years must be exact: 1888 is not 1889
    if forms(g) & ALL_ANSWERS:
        return False                     # exactly another card's answer, so not a typo of this one
    gs, gp = stem_words(g), phonetic(stem_words(g))
    if real_word(gs) and len(gs.replace(" ", "")) < 8:
        return False                     # a short, real English word is a different answer, not a typo
    for option in options:
        o = stem_words(normalize(option))
        if distance(gs, o) <= tolerance_for(o):
            return True
        if len(o.replace(" ", "")) >= 6 and gp == phonetic(o):
            return True                  # spelled differently, sounds the same
        ow, gw = o.split(), gs.split()   # several words: forgive a typo inside each one
        if len(ow) > 1 and len(ow) == len(gw) and \
                all(distance(a, b) <= tolerance_for(b) for a, b in zip(gw, ow)):
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
        "settings": {"target": 40, "categories": list(CATEGORIES), "levels": list(DEFAULT_LEVELS)},
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
        "roundNo": 0,
        "roundCategory": None,
        "catCycle": [],
        "overtime": 0,
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
    levels = room["settings"].get("levels") or DEFAULT_LEVELS
    player = {"id": secrets.token_urlsafe(9), "name": name, "score": 0, "level": levels[0],
              "levelSet": False,   # follows the room until the player picks a level
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


def clues_now(room):
    """Clues that will have been used if the card is guessed right now."""
    return max(1, len(room["revealed"]) + (1 if room["step"] == "pick" else 0))


def split_points(clues_used):
    """The card is worth CARD_POINTS: the fewer clues it took, the bigger the guesser's share,
    and the reader keeps the rest — the longer the card ran, the more the reader earns."""
    used = min(CLUES_PER_CARD, max(1, clues_used))
    guesser = CLUES_PER_CARD + 1 - used
    return guesser, CARD_POINTS - guesser


def points_now(room):
    return split_points(clues_now(room))[0]


# ---------------------------------------------------------------- game flow

def room_levels(room):
    levels = list(room["settings"].get("levels") or DEFAULT_LEVELS)
    for p in room["players"]:                     # a player may pick a level outside the host's list
        if p.get("level") and p["level"] not in levels:
            levels.append(p["level"])
    return levels


def eligible(room):
    cats = room["settings"]["categories"] or CATEGORIES
    levels = room_levels(room)
    deck = [i for i, c in enumerate(CARDS)
            if c["category"] in cats and c.get("level", "B1") in levels]
    return deck or list(range(len(CARDS)))  # no card matches: fall back to everything


def build_deck(room):
    deck = eligible(room)
    random.shuffle(deck)
    room["deck"] = deck


def next_category(room):
    """One category per round: everybody plays the same one, and it changes next round."""
    available = sorted({CARDS[i]["category"] for i in eligible(room)})
    pool = [c for c in room.get("catCycle", []) if c in available]
    if not pool:
        pool = available[:]
        random.shuffle(pool)
        if len(pool) > 1 and pool[-1] == room.get("roundCategory"):
            pool[0], pool[-1] = pool[-1], pool[0]   # don't repeat the category we just played
    room["roundCategory"] = pool.pop()
    room["catCycle"] = pool


def nearest_levels(level):
    """The wanted level first, then the closest ones: A1 -> A2 -> B1 -> B2."""
    if level not in LEVEL_ORDER:
        return [level] + [l for l in LEVELS if l != level]
    here = LEVEL_ORDER.index(level)
    return sorted(LEVELS, key=lambda l: abs(LEVEL_ORDER.index(l) - here)
                  if l in LEVEL_ORDER else 99)


def take_from_deck(room, level):
    for _ in range(2):
        for pos in range(len(room["deck"]) - 1, -1, -1):
            c = CARDS[room["deck"][pos]]
            if c["category"] == room["roundCategory"] and \
                    (level is None or c.get("level", "B1") == level):
                return CARDS[room["deck"].pop(pos)]
        build_deck(room)
    return None


def draw_card(room, level=None):
    """Next card of the round's category. When that level has no card in this category
    (Famous and Year only exist in B1/B2), fall back to the closest level."""
    for want in (nearest_levels(level) if level else []):
        card = take_from_deck(room, want)
        if card:
            return card
    return take_from_deck(room, None) or CARDS[room["deck"].pop()]


def card_level_for(room, focus):
    """70% of the cards follow the level of the player who guesses first;
    the rest are drawn from the levels the host allowed."""
    allowed = list(room["settings"].get("levels") or DEFAULT_LEVELS)
    own = focus.get("level") if focus else None
    if own and random.random() < OWN_LEVEL_SHARE:
        return own, True
    return random.choice(allowed or [own or "B1"]), False


def start_card(room):
    if not room["deck"]:
        build_deck(room)
    n = len(room["players"])
    new_round = room["round"] % n == 0        # everyone has read the same number of cards
    if new_round:
        room["roundNo"] = room.get("roundNo", 0) + 1
        next_category(room)
    # who guesses first on this card: the card is chosen for that player's level
    start = (room["readerIdx"] + 1) % n
    order = [room["players"][(start + k) % n] for k in range(1, n)]
    focus = next((p for p in order if is_online(p)), order[0] if order else None)
    level, aimed = card_level_for(room, focus)
    base = draw_card(room, level)
    room["focusId"] = focus["id"] if focus else None
    # true only when the card really came out at that player's own level
    room["focusOwnLevel"] = bool(aimed and focus and base.get("level") == focus.get("level"))
    pt = base.get("pt") or {}
    pairs = list(zip(base["clues"][:CLUES_PER_CARD],
                     (pt.get("clues") or [None] * CLUES_PER_CARD)[:CLUES_PER_CARD]))
    random.shuffle(pairs)
    room["card"] = dict(base, clues=[t for t, _ in pairs], cluesPt=[t for _, t in pairs],
                        answerPt=pt.get("answer"))
    room["readerIdx"] += 1
    rd = reader(room)
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
    if new_round:
        bump(room, "Round %d — the category is %s! %s reads first."
             % (room["roundNo"], room["roundCategory"], rd["name"]))
    else:
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
              "clues": room["card"]["clues"], "cluesPt": room["card"]["cluesPt"],
              "answerPt": room["card"].get("answerPt"), "fact": room["card"].get("fact", "")}
    if winner:
        pts, reader_pts = split_points(result["cluesUsed"])
        winner["score"] += pts
        winner["cards"] += 1
        rd["score"] += reader_pts
        result.update(winnerId=winner["id"], points=pts, readerPoints=reader_pts)
    else:
        rd["score"] += CARD_POINTS          # nobody guessed: the reader keeps the whole card
        result.update(readerPoints=CARD_POINTS)
    room["result"] = result
    room["phase"] = "cardEnd"
    if winner:
        bump(room, "%s got it: %s! +%d (reader +%d)"
             % (winner["name"], result["answer"], result["points"], result["readerPoints"]))
    else:
        bump(room, "Nobody got it. It was %s — reader %s takes all %d points."
             % (result["answer"], rd["name"], CARD_POINTS))


def standings(room):
    """Everyone gets the same number of turns: the game can only end when the
    rotation is complete, someone reached the target and a single player leads."""
    n = max(1, len(room["players"]))
    top = max((p["score"] for p in room["players"]), default=0)
    leaders = [p for p in room["players"] if p["score"] == top]
    return {"reached": top >= room["settings"]["target"],
            "cardsLeft": (-room["round"]) % n,          # cards until everyone has read equally
            "tie": len(leaders) > 1,
            "leaders": [p["id"] for p in leaders]}


MAX_OVERTIME = 2  # extra rounds allowed to break a tie before the win is shared


def finish_or_next(room):
    s = standings(room)
    if s["reached"] and not s["cardsLeft"]:
        if not s["tie"]:
            room["phase"] = "gameOver"
            bump(room, "Game over!")
            return
        room["overtime"] = room.get("overtime", 0) + 1
        if room["overtime"] > MAX_OVERTIME:
            room["phase"] = "gameOver"
            bump(room, "Still tied — the win is shared!")
            return
    if s["reached"] and s["cardsLeft"]:
        bump(room, "Target reached! %d card(s) left so everyone plays the same number of turns."
             % s["cardsLeft"])
    elif s["reached"] and s["tie"]:
        bump(room, "It's a tie at the top — one more round to decide!")
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
        levels = [l for l in data.get("levels", room["settings"]["levels"]) if l in LEVELS]
        room["settings"] = {"target": max(10, min(200, target)),
                            "categories": cats or list(CATEGORIES),
                            "levels": levels or list(DEFAULT_LEVELS)}
        for p in room["players"]:      # players who never picked a level follow the room
            if not p.get("levelSet") and p.get("level") not in room["settings"]["levels"]:
                p["level"] = room["settings"]["levels"][0]
        bump(room)

    elif kind == "myLevel":
        level = data.get("level")
        if level not in LEVELS:
            raise GameError("Unknown level.")
        player["level"] = level
        player["levelSet"] = True
        bump(room, "%s is playing at level %s." % (player["name"], level))

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
        room["roundNo"] = 0
        room["overtime"] = 0
        room["catCycle"] = []
        room["roundCategory"] = None
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
                "level": p.get("level"), "levelSet": p.get("levelSet", False),
                "online": is_online(p)} for p in room["players"]]
    state = {"version": room["version"], "code": room["code"], "you": me["id"],
             "hostId": room["hostId"], "phase": room["phase"], "settings": room["settings"],
             "allCategories": CATEGORIES, "allLevels": LEVELS,
             "cardCounts": CARD_COUNTS, "players": players, "log": room["log"],
             "standings": standings(room) if room["phase"] in ("playing", "cardEnd") else None,
             "cluesPerCard": CLUES_PER_CARD, "cardPoints": CARD_POINTS, "lanUrl": LAN_URL,
             "minPlayers": MIN_PLAYERS, "maxPlayers": MAX_PLAYERS}
    if room["phase"] in ("playing", "cardEnd") and room["card"]:
        card = room["card"]
        rd = reader(room)
        cg = current_guesser(room)
        n = max(1, len(room["players"]))
        g = {"round": room["round"], "category": card["category"],
             "roundNo": room.get("roundNo", 1),
             "focusId": room.get("focusId"), "focusOwnLevel": room.get("focusOwnLevel", False),
             "cardInRound": (room["round"] - 1) % n + 1, "cardsPerRound": n,
             "level": card.get("level", "B1"), "readerId": rd["id"],
             "currentId": cg["id"] if cg else None, "step": room["step"],
             "revealed": [{"n": n, "text": card["clues"][n - 1], "pt": card["cluesPt"][n - 1]}
                          for n in room["revealed"]],
             "hasPt": any(card["cluesPt"]),
             "pointsNow": points_now(room),
             "readerPointsNow": split_points(clues_now(room))[1],
             "lastGuess": room["lastGuess"],
             "guessers": room["guessers"]}
        if rd is me:
            g["secret"] = {"answer": card["answer"], "answerPt": card.get("answerPt"),
                           "clues": [{"n": i + 1, "text": t, "pt": card["cluesPt"][i]}
                                     for i, t in enumerate(card["clues"])]}
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

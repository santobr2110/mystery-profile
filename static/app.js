"use strict";

const $app = document.getElementById("app");
const $toast = document.getElementById("toast");
const params = new URLSearchParams(location.search);
const KEY = "mysteryProfile.session" + (params.get("slot") || "");
const COLORS = ["#ef6f5e", "#2a9d9f", "#e69a1c", "#5aa13c", "#7b61c9", "#d65a9a", "#3b82c4", "#8a6d3b"];
const CAT_EMOJI = { Person: "🧑", Place: "🌍", Thing: "📦", Animal: "🐾", Food: "🍎", Famous: "🌟", Year: "📅" };
const LEVEL_NAME = { A1: "Beginner", A2: "Elementary", B1: "Intermediate", B2: "Advanced" };

let session = read(KEY);            // { room, player }
let state = null;
let version = 0;
let pollGen = 0;
let ui = { showAnswer: false, busy: false, pt: read("mysteryProfile.pt") === true };
let prevTurnKey = "";

// ------------------------------------------------------------ helpers

function read(key) {
  try { return JSON.parse(localStorage.getItem(key)); } catch (e) { return null; }
}
function write(key, value) {
  try { value == null ? localStorage.removeItem(key) : localStorage.setItem(key, JSON.stringify(value)); } catch (e) {}
}
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function toast(msg) {
  $toast.textContent = msg;
  $toast.classList.add("show");
  clearTimeout(toast.t);
  toast.t = setTimeout(() => $toast.classList.remove("show"), 2600);
}
function player(id) { return state.players.find(p => p.id === id); }
function nameOf(id) { const p = state && player(id); return p ? p.name : "?"; }
function colorOf(id) { const i = state.players.findIndex(p => p.id === id); return COLORS[(i < 0 ? 0 : i) % COLORS.length]; }
function avatar(id) { return `<span class="avatar" style="background:${colorOf(id)}">${esc(nameOf(id).slice(0, 1).toUpperCase())}</span>`; }
function isMe(id) { return state && id === state.you; }

let voice = null;
function pickVoice() {
  const vs = speechSynthesis.getVoices().filter(v => /^en[-_]/i.test(v.lang));
  voice = vs.find(v => /en[-_]US/i.test(v.lang) && /samantha|google|natural/i.test(v.name)) || vs.find(v => /en[-_]US/i.test(v.lang)) || vs[0] || null;
}
if ("speechSynthesis" in window) { pickVoice(); speechSynthesis.onvoiceschanged = pickVoice; }
function speak(text) {
  if (!("speechSynthesis" in window)) return toast("Your browser can't read aloud.");
  speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  u.lang = "en-US"; u.rate = 0.9;
  if (voice) u.voice = voice;
  speechSynthesis.speak(u);
}
function sayBtn(text) { return `<button class="icon" data-say="${esc(text)}" aria-label="Listen">🔊</button>`; }
function ptLine(text) {
  return ui.pt && text ? `<div class="pt">${esc(text)}</div>` : "";
}
function ptToggle() {
  if (!state.game || !state.game.hasPt) return "";
  return `<button class="pill ptbtn ${ui.pt ? "on" : ""}" data-act="toggle-pt" title="Tradução">🇧🇷 PT</button>`;
}

// ------------------------------------------------------------ network

async function api(path, body) {
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await res.json().catch(() => ({ error: "Connection problem." }));
  if (!res.ok) throw new Error(data.error || "Something went wrong.");
  return data;
}

async function action(type, extra) {
  if (ui.busy) return;
  ui.busy = true;
  try {
    await api("/api/action", Object.assign({ room: session.room, player: session.player, type }, extra));
  } catch (e) {
    toast(e.message);
  } finally {
    ui.busy = false;
  }
}

async function poll() {
  const gen = ++pollGen;
  let failures = 0;
  while (gen === pollGen && session) {
    try {
      const q = `room=${encodeURIComponent(session.room)}&player=${encodeURIComponent(session.player)}&v=${version}`;
      const res = await fetch("/api/state?" + q, { cache: "no-store" });
      const data = await res.json();
      if (gen !== pollGen) return;
      if (res.status === 404) {
        toast(data.error || "Game not found.");
        return leave(false);
      }
      failures = 0;
      version = data.version;
      state = data;
      render();
    } catch (e) {
      failures++;
      if (failures === 3) toast("Reconnecting…");
      await new Promise(r => setTimeout(r, Math.min(4000, 800 * failures)));
    }
  }
}

function enter(room, player) {
  session = { room, player };
  write(KEY, session);
  version = 0;
  history.replaceState(null, "", location.pathname + (params.get("slot") ? "?slot=" + params.get("slot") : ""));
  poll();
}

function leave(tell = true) {
  if (tell && session) action("leave");
  pollGen++;
  session = null; state = null; version = 0;
  write(KEY, null);
  render();
}

// Wake up immediately when the phone screen comes back on.
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && session) { version = 0; poll(); }
});

// ------------------------------------------------------------ rendering

function render() {
  // keep focus + typed text in inputs across re-renders
  const active = document.activeElement;
  const keep = {};
  $app.querySelectorAll("input[id]").forEach(i => { keep[i.id] = i.value; });
  const focusId = active && active.id;
  const sel = active && active.selectionStart != null ? [active.selectionStart, active.selectionEnd] : null;

  let html = !session || !state ? homeView()
    : state.phase === "lobby" ? lobbyView()
    : state.phase === "gameOver" ? gameOverView()
    : state.phase === "cardEnd" ? cardEndView()
    : gameView();
  // only animate when the screen really changed, not on every update
  const g = state && state.game;
  const screen = state ? [state.phase, g && g.round, g && g.step, g && g.revealed.length, g && g.currentId].join("|") : "home";
  if (screen === render.screen) html = html.replace(/ anim-pop/g, "");
  render.screen = screen;
  $app.innerHTML = html;

  for (const id in keep) { const el = document.getElementById(id); if (el && !el.dataset.fresh) el.value = keep[id]; }
  if (focusId) {
    const el = document.getElementById(focusId);
    if (el) { el.focus({ preventScroll: true }); if (sel && el.setSelectionRange) el.setSelectionRange(sel[0], sel[1]); }
  }
  notifyTurn();
}

function notifyTurn() {
  if (!state || !state.game || state.phase !== "playing") return;
  const g = state.game;
  const key = `${g.round}:${g.currentId}:${g.revealed.length}`;
  if (key !== prevTurnKey) {
    const mine = g.currentId === state.you && g.step === "pick";
    if (prevTurnKey && mine && navigator.vibrate) navigator.vibrate([80, 60, 80]);
    prevTurnKey = key;
  }
}

function rulesBlock() {
  return `
  <details class="rules card flat">
    <summary>How to play</summary>
    <ol class="small">
      <li>A round has one card per player: everybody reads once, and the whole round uses the <b>same category</b>. The category changes in the next round.</li>
      <li>Each player sets their <b>own level</b> in the lobby. About 7 out of 10 cards are chosen at the level of the player who guesses first.</li>
      <li>On each card one player is the <b>reader</b>. Only the reader sees the secret answer and its 10 clues.</li>
      <li>The other players take turns. On your turn, <b>pick a number</b> from 1 to 10.</li>
      <li>The reader <b>reads that clue aloud</b> in English. Everyone also sees it on screen.</li>
      <li>Then you can <b>make one guess</b> — type it, or say it out loud — or pass.</li>
      <li>The fewer clues used, the more points: clue 1 = 10 pts, clue 10 = 1 pt. The reader gets +2 when someone gets it.</li>
      <li>The first player to reach the target score wins — but the game only ends when <b>everyone has had the same number of turns</b>, so the last players always get their chance.</li>
    </ol>
  </details>`;
}

function homeView() {
  const code = (params.get("room") || "").toUpperCase();
  const name = read("mysteryProfile.name") || "";
  return `
  <div class="stack">
    <div class="logo">
      <div class="mark">?</div>
      <h1>Mystery<br>Profile</h1>
      <p class="muted">Guess who, where or what — in English!</p>
    </div>
    <div class="card stack">
      <div>
        <label for="name">Your name</label>
        <input id="name" type="text" maxlength="16" autocomplete="nickname" placeholder="e.g. Anna" value="${esc(name)}">
      </div>
      ${code ? `
        <button class="accent" data-act="join">Join game ${esc(code)}</button>
        <input id="code" type="hidden" value="${esc(code)}">
        <button class="ghost" data-act="forget-code" style="width:100%">Create a new game instead</button>
      ` : `
        <button class="accent" data-act="create">Create a new game</button>
        <div class="divider">or join one</div>
        <div class="row">
          <input id="code" class="code grow" type="text" maxlength="4" autocomplete="off" autocapitalize="characters" placeholder="CODE">
          <button data-act="join" style="width:auto">Join</button>
        </div>
      `}
    </div>
    ${rulesBlock()}
  </div>`;
}

function lobbyView() {
  const s = state;
  const host = s.hostId === s.you;
  const me = player(s.you) || {};
  const local = /^(localhost|127\.0\.0\.1|\[::1\])$/.test(location.hostname);
  const base = local && s.lanUrl ? s.lanUrl : location.origin;
  const link = `${base}/?room=${s.code}`;
  const cards = deckSize(s);
  const enough = s.players.length >= s.minPlayers && cards > 0;
  return `
  <div class="stack">
    ${topbar("Lobby")}
    <div class="card center">
      <h3>Game code</h3>
      <div class="room-code">${esc(s.code)}</div>
      <p class="muted small">Friends open <b>${esc(base.replace(/^https?:\/\//, ""))}</b> and type this code.</p>
      <div style="margin-top:12px"><button class="secondary" data-act="share" data-link="${esc(link)}">📨 Share invite link</button></div>
    </div>
    <div class="card">
      <div class="row" style="margin-bottom:6px"><h3 class="grow">Players</h3><span class="muted small">${s.players.length}/${s.maxPlayers}</span></div>
      <ul class="players">
        ${s.players.map(p => `
          <li>${avatar(p.id)}<b class="grow" style="flex:1">${esc(p.name)}${isMe(p.id) ? ' <span class="muted small">(you)</span>' : ""}</b>
          ${p.level ? `<span class="pill">${esc(p.level)}</span>` : ""}
          ${p.id === s.hostId ? '<span class="pill">👑</span>' : ""}<span class="dot ${p.online ? "" : "off"}"></span></li>`).join("")}
      </ul>
    </div>
    <div class="card stack">
      <div>
        <h3>Your English level</h3>
        <p class="muted small" style="margin:4px 0 8px">Most of the cards you guess first will be at this level.</p>
        <div class="chips">
          ${s.allLevels.map(l => `<button class="chip ${me.level === l ? "on" : ""}" data-act="my-level" data-v="${l}">${l} · ${LEVEL_NAME[l] || ""}</button>`).join("")}
        </div>
      </div>
    </div>
    <div class="card stack">
      <div>
        <h3>Play until</h3>
        <div class="chips" style="margin-top:8px">
          ${[25, 40, 60, 80].map(t => `<button class="chip ${s.settings.target === t ? "on" : ""}" data-act="target" data-v="${t}" ${host ? "" : "disabled"}>${t} points</button>`).join("")}
        </div>
        <p class="muted small" style="margin-top:8px">The game ends only after everyone has played the same number of turns.</p>
      </div>
      <div>
        <h3>Levels of the room</h3>
        <p class="muted small" style="margin:4px 0 8px">Used for the other cards, and for players who keep the default.</p>
        <div class="chips">
          ${s.allLevels.map(l => `<button class="chip ${s.settings.levels.includes(l) ? "on" : ""}" data-act="level" data-v="${l}" ${host ? "" : "disabled"}>${l} · ${LEVEL_NAME[l] || ""}</button>`).join("")}
        </div>
      </div>
      <div>
        <h3>Categories</h3>
        <div class="chips" style="margin-top:8px">
          ${s.allCategories.map(c => `<button class="chip ${s.settings.categories.includes(c) ? "on" : ""}" data-act="cat" data-v="${c}" ${host ? "" : "disabled"}>${CAT_EMOJI[c] || ""} ${c}</button>`).join("")}
        </div>
      </div>
      <p class="${cards ? "muted small center" : "notice bad small center"}">${
        cards ? `${cards} cards in this deck` : "No cards match this level and category — change one of them."}</p>
    </div>
    ${host
      ? `<button class="accent" data-act="start" ${enough ? "" : "disabled"}>${cards === 0 ? "Pick a level and category with cards" : enough ? "Start the game ▶" : "Waiting for more players…"}</button>`
      : `<p class="center muted">Waiting for <b>${esc(nameOf(s.hostId))}</b> to start the game…</p>`}
    ${rulesBlock()}
  </div>`;
}

function topbar(title, extra = "") {
  return `<div class="topbar"><div class="title">${esc(title)}</div>${extra}<button class="ghost" data-act="leave">Leave</button></div>`;
}

function gameHeader(g) {
  return topbar(`Round ${g.roundNo || g.round}`,
    `${ptToggle()}<span class="pill">${esc(g.level || "")}</span>
     <span class="pill cat cat-${g.category}">${CAT_EMOJI[g.category] || ""} ${g.category}</span>`);
}

function finalNotice() {
  const st = state.standings;
  if (!st || !st.reached) return "";
  if (st.cardsLeft) return `<div class="notice">🏁 Target reached! ${st.cardsLeft} more card${st.cardsLeft > 1 ? "s" : ""} — everyone gets the same number of turns.</div>`;
  if (st.tie) return `<div class="notice">🏁 It's a tie at the top — one more round decides the winner!</div>`;
  return "";
}

function deckSize(s) {
  let n = 0;
  for (const l of s.settings.levels) for (const c of s.settings.categories) n += s.cardCounts[l + "/" + c] || 0;
  return n;
}

function worthBar(g) {
  const used = g.revealed.length;
  return `
  <div>
    <div class="row small" style="margin-bottom:6px">
      <span class="grow muted"><b>${used}</b> of ${state.cluesPerCard} clues opened${
        g.cardsPerRound > 1 ? ` · card ${g.cardInRound}/${g.cardsPerRound} of this round` : ""}</span>
      <span class="worth">Worth ${g.pointsNow} pts</span>
    </div>
    <div class="meter"><i style="width:${(used / state.cluesPerCard) * 100}%"></i></div>
    ${g.focusId ? `<p class="muted small" style="margin-top:6px">${g.focusOwnLevel
      ? `🎯 Card at ${isMe(g.focusId) ? "your" : esc(nameOf(g.focusId)) + "'s"} level (${esc(g.level)})`
      : `🎲 Mixed card (${esc(g.level)})`}</p>` : ""}
  </div>`;
}

function gameView() {
  const g = state.game;
  const me = state.you;
  const amReader = g.readerId === me;
  const amCurrent = g.currentId === me;
  const current = nameOf(g.currentId);
  const latest = g.revealed[g.revealed.length - 1];
  let html = gameHeader(g);

  if (amReader) {
    html += `
    <div class="stack">
      ${finalNotice()}
      <div class="role reader"><span class="emoji">🎙️</span><div>You are the reader.<div class="small" style="font-weight:600;opacity:.8">Don't show your screen!</div></div></div>
      <div class="card secret">
        <h3>The answer is</h3>
        <div class="answer ${ui.showAnswer ? "" : "hidden"}">${esc(g.secret.answer)}</div>
        <button class="ghost" data-act="toggle-answer">${ui.showAnswer ? "🙈 Hide" : "👁️ Tap to see the answer"}</button>
      </div>
      ${readerActions(g, current, latest)}
      <div class="card">
        <h3 style="margin-bottom:6px">Your clues</h3>
        <ul class="clues">
          ${g.secret.clues.map(c => {
            const read = g.revealed.some(r => r.n === c.n);
            const now = latest && latest.n === c.n && g.step === "guess";
            return `<li class="${now ? "now" : read ? "read" : ""}"><span class="n">${c.n}</span><span class="t">${esc(c.text)}${ptLine(c.pt)}</span>${now ? sayBtn(c.text) : ""}</li>`;
          }).join("")}
        </ul>
      </div>
      ${worthBar(g)}
      ${scoreboard()}
      ${logBlock()}
      <div class="row"><button class="secondary" data-act="skipTurn">Skip ${esc(current)}'s turn</button><button class="secondary" data-act="skipCard">Skip card</button></div>
    </div>`;
    return html;
  }

  html += `<div class="stack">` + finalNotice();
  if (amCurrent && g.step === "pick") {
    html += `
      <div class="role me anim-pop"><span class="emoji">👉</span><div>Your turn! Pick a clue number.</div></div>
      <div class="card">
        <div class="grid">
          ${Array.from({ length: state.cluesPerCard }, (_, i) => i + 1).map(n => {
            const used = g.revealed.some(r => r.n === n);
            return `<button class="tile ${used ? "used" : ""}" data-act="pick" data-v="${n}" ${used ? "disabled" : ""}>${used ? "✓" : n}</button>`;
          }).join("")}
        </div>
      </div>`;
  } else if (amCurrent && g.step === "guess") {
    html += `
      <div class="role me"><span class="emoji">💡</span><div>Listen to ${esc(nameOf(g.readerId))}, then guess!</div></div>
      <div class="card bigclue anim-pop">
        <div class="num">Clue #${latest.n}</div>
        <div class="text">${esc(latest.text)}</div>
        ${ptLine(latest.pt)}
        ${sayBtn(latest.text)}
      </div>
      <form class="card stack" data-form="guess">
        <label for="guess">${g.category === "Famous" ? "Who is it?" : g.category === "Year" ? "Which year is it?" : `What ${g.category.toLowerCase()} is it?`}</label>
        <input id="guess" type="text" maxlength="60" autocomplete="off" autocapitalize="words" enterkeyhint="send" placeholder="Type your guess…">
        <div class="row"><button type="submit" class="accent grow">Guess for ${g.pointsNow} pts</button><button type="button" class="secondary" style="width:auto" data-act="pass">Pass</button></div>
        <p class="muted small center">You can also say it out loud — the reader can mark it correct.</p>
      </form>`;
  } else {
    const waiting = g.step === "pick"
      ? `<b>${esc(current)}</b> is choosing a clue…`
      : `<b>${esc(current)}</b> is guessing…`;
    html += `<div class="role"><span class="emoji">⏳</span><div>${waiting}<div class="small muted" style="font-weight:600">Reader: ${esc(nameOf(g.readerId))}</div></div></div>`;
    if (latest && g.step === "guess") {
      html += `
      <div class="card bigclue anim-pop">
        <div class="num">Clue #${latest.n}</div>
        <div class="text">${esc(latest.text)}</div>
        ${ptLine(latest.pt)}
        ${sayBtn(latest.text)}
      </div>`;
    }
  }
  if (g.lastGuess && !g.lastGuess.correct) {
    html += `<div class="notice bad">${esc(nameOf(g.lastGuess.playerId))} guessed “${esc(g.lastGuess.text)}” — not quite!</div>`;
  }
  html += worthBar(g);
  html += revealedList(g);
  html += scoreboard();
  html += logBlock();
  html += `</div>`;
  return html;
}

function readerActions(g, current, latest) {
  let html = "";
  if (g.step === "pick") {
    html += `<div class="notice">⏳ Waiting for <b>${esc(current)}</b> to pick a number…</div>`;
  } else {
    html += `
    <div class="card stack anim-pop">
      <p><b>${esc(current)}</b> picked <b>clue #${latest.n}</b>. Read it aloud:</p>
      <p class="display" style="font-size:21px">“${esc(latest.text)}”</p>
      ${ptLine(latest.pt)}
      <p class="muted small">Did ${esc(current)} say the right answer?</p>
      <div class="row"><button class="good" data-act="judge" data-v="1">✓ Correct</button><button class="bad" data-act="judge" data-v="0">✗ Wrong</button></div>
    </div>`;
  }
  if (g.lastGuess && !g.lastGuess.correct) {
    html += `
    <div class="notice bad stack">
      <p>${esc(nameOf(g.lastGuess.playerId))} typed “${esc(g.lastGuess.text)}”.</p>
      ${g.revealed.length === g.lastGuess.cluesUsed ? `<button class="secondary" data-act="accept">Actually, that's right — accept it</button>` : ""}
    </div>`;
  }
  return html;
}

function revealedList(g) {
  if (!g.revealed.length) return `<div class="card flat center muted small">No clues opened yet.</div>`;
  const list = g.revealed.slice().reverse();
  return `
  <div class="card">
    <h3 style="margin-bottom:6px">Clues so far</h3>
    <ul class="clues">
      ${list.map(c => `<li><span class="n">${c.n}</span><span class="t">${esc(c.text)}${ptLine(c.pt)}</span>${sayBtn(c.text)}</li>`).join("")}
    </ul>
  </div>`;
}

function ranking() {
  return state.players.slice().sort((a, b) => b.score - a.score || a.name.localeCompare(b.name));
}

function scoreboard() {
  const target = state.settings.target;
  const g = state.game;
  let prev = null, rank = 0;
  return `
  <div class="card">
    <div class="row" style="margin-bottom:4px"><h3 class="grow">Ranking</h3><span class="muted small">First to ${target} · equal turns</span></div>
    <ul class="board">
      ${ranking().map((p, i) => {
        if (p.score !== prev) { rank = i + 1; prev = p.score; }
        const tag = g && g.readerId === p.id ? "🎙️" : g && g.currentId === p.id && state.phase === "playing" ? "👉" : "";
        return `<li class="${isMe(p.id) ? "me" : ""}">
          <span class="rank">${rank}</span>${avatar(p.id)}
          <span class="name">${esc(p.name)} ${tag}${p.online ? "" : '<small>offline</small>'}</span>
          <span class="pts">${p.score}</span>
          <div class="meter bar"><i style="width:${Math.min(100, (p.score / target) * 100)}%;background:${colorOf(p.id)}"></i></div>
        </li>`;
      }).join("")}
    </ul>
  </div>`;
}

function logBlock() {
  if (!state.log.length) return "";
  return `<div class="card flat"><ul class="log">${state.log.slice(-4).map(l => `<li>${esc(l)}</li>`).join("")}</ul></div>`;
}

function cardEndView() {
  const g = state.game;
  const r = g.result;
  const canNext = g.readerId === state.you || state.hostId === state.you;
  const lg = g.lastGuess;
  const wonMe = r.winnerId === state.you;
  return `
  <div class="stack">
    ${gameHeader(g)}
    <div class="card reveal anim-pop">
      <div class="burst">${r.winnerId ? (wonMe ? "🎉" : "✅") : "🤷"}</div>
      <h3 style="margin-top:8px">It was…</h3>
      <div class="answer">${esc(r.answer)}</div>
      ${r.answerPt ? `<div class="pt center" style="font-size:17px">${esc(r.answerPt)}</div>` : ""}
      ${sayBtn(r.answer)}
      <p style="margin-top:12px">${r.winnerId
        ? `<b>${wonMe ? "You" : esc(nameOf(r.winnerId))}</b> got it with ${r.cluesUsed} clue${r.cluesUsed > 1 ? "s" : ""}! <b>+${r.points}</b><br><span class="muted small">Reader ${esc(nameOf(r.readerId))} +${r.readerPoints}</span>`
        : `Nobody got it this time.`}</p>
    </div>
    ${!r.winnerId && lg && !lg.correct && g.readerId === state.you
      ? `<div class="notice bad stack"><p>Last guess: “${esc(lg.text)}” by ${esc(nameOf(lg.playerId))}.</p><button class="secondary" data-act="accept">Actually, that's right — accept it</button></div>` : ""}
    ${finalNotice()}
    ${canNext ? `<button class="accent" data-act="next">Next card ▶</button>` : `<p class="center muted">Waiting for ${esc(nameOf(g.readerId))} to show the next card…</p>`}
    ${scoreboard()}
    <div class="card">
      <div class="row" style="margin-bottom:6px"><h3 class="grow">Review the clues 📚</h3>
        ${(r.cluesPt || []).some(Boolean) ? `<button class="pill ptbtn ${ui.pt ? "on" : ""}" data-act="toggle-pt">🇧🇷 PT</button>` : ""}</div>
      <p class="muted small">Tap 🔊 to hear the pronunciation.</p>
      <ul class="clues">
        ${r.clues.map((t, i) => `<li><span class="n">${i + 1}</span><span class="t">${esc(t)}${ptLine((r.cluesPt || [])[i])}</span>${sayBtn(t)}</li>`).join("")}
      </ul>
    </div>
  </div>`;
}

function gameOverView() {
  const rk = ranking();
  const top = rk[0];
  const winners = rk.filter(p => p.score === top.score);
  const host = state.hostId === state.you;
  const podium = [rk[1], rk[0], rk[2]].map((p, i) => p ? `
    <div class="step p${[2, 1, 3][i]}">
      <div class="who">${esc(p.name)}</div>
      <div class="block">${p.score}</div>
    </div>` : "").join("");
  return `
  <div class="stack">
    ${topbar("Game over")}
    <div class="card reveal anim-pop">
      <div class="burst">🏆</div>
      <h3 style="margin-top:8px">${winners.length > 1 ? "It's a tie!" : "The winner is"}</h3>
      <div class="answer">${winners.map(w => esc(w.name)).join(" & ")}</div>
      <div class="podium">${podium}</div>
    </div>
    ${scoreboard()}
    ${host ? `<button class="accent" data-act="restart">Play again ↺</button>` : `<p class="center muted">Waiting for ${esc(nameOf(state.hostId))} to start a new game…</p>`}
  </div>`;
}

// ------------------------------------------------------------ events

$app.addEventListener("click", async e => {
  const say = e.target.closest("[data-say]");
  if (say) return speak(say.dataset.say);
  const el = e.target.closest("[data-act]");
  if (!el || el.disabled) return;
  const act = el.dataset.act;
  const v = el.dataset.v;

  if (act === "create" || act === "join") {
    const name = (document.getElementById("name").value || "").trim();
    const code = ((document.getElementById("code") || {}).value || "").trim().toUpperCase();
    if (!name) return toast("Please type your name.");
    if (act === "join" && code.length !== 4) return toast("Type the 4-letter game code.");
    write("mysteryProfile.name", name);
    try {
      const res = await api(act === "create" ? "/api/create" : "/api/join", { name, room: code });
      enter(res.room, res.player);
    } catch (err) { toast(err.message); }
    return;
  }
  if (act === "forget-code") {
    params.delete("room");
    history.replaceState(null, "", location.pathname);
    return render();
  }
  if (act === "leave") {
    if (confirm("Leave this game?")) leave(true);
    return;
  }
  if (act === "share") {
    const link = el.dataset.link;
    if (navigator.share) {
      navigator.share({ title: "Mystery Profile", text: `Join my English game! Code: ${state.code}`, url: link }).catch(() => {});
    } else {
      try { await navigator.clipboard.writeText(link); toast("Link copied!"); } catch (err) { prompt("Copy this link:", link); }
    }
    return;
  }
  if (act === "toggle-answer") { ui.showAnswer = !ui.showAnswer; return render(); }
  if (act === "toggle-pt") { ui.pt = !ui.pt; write("mysteryProfile.pt", ui.pt); return render(); }
  if (act === "my-level") return action("myLevel", { level: v });
  if (act === "target") return action("settings", { target: Number(v) });
  if (act === "level") {
    const levels = new Set(state.settings.levels);
    levels.has(v) ? levels.delete(v) : levels.add(v);
    if (!levels.size) return toast("Keep at least one level.");
    return action("settings", { levels: [...levels] });
  }
  if (act === "cat") {
    const cats = new Set(state.settings.categories);
    cats.has(v) ? cats.delete(v) : cats.add(v);
    if (!cats.size) return toast("Keep at least one category.");
    return action("settings", { categories: [...cats] });
  }
  if (act === "start") { ui.showAnswer = false; return action("start"); }
  if (act === "pick") return action("pick", { n: Number(v) });
  if (act === "pass") return action("pass");
  if (act === "judge") return action("judge", { correct: v === "1" });
  if (act === "accept") return action("accept");
  if (act === "skipTurn") return action("skipTurn");
  if (act === "skipCard") { if (confirm("Skip this card? Nobody scores.")) action("skipCard"); return; }
  if (act === "next") { ui.showAnswer = false; return action("next"); }
  if (act === "restart") return action("restart");
});

$app.addEventListener("submit", e => {
  e.preventDefault();
  if (e.target.dataset.form === "guess") submitGuess();
});

$app.addEventListener("keydown", e => {
  if (e.key !== "Enter" || !(e.target.id === "code" || e.target.id === "name")) return;
  const code = (document.getElementById("code") || {}).value;
  const btn = $app.querySelector(code ? '[data-act="join"]' : '[data-act="create"]') || $app.querySelector('[data-act="join"]');
  if (btn) btn.click();
});

function submitGuess() {
  const input = document.getElementById("guess");
  const text = (input && input.value || "").trim();
  if (!text) return toast("Type a guess — or pass.");
  input.value = "";
  action("guess", { text });
}

render();
if (session) poll();

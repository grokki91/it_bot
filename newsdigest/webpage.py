# -*- coding: utf-8 -*-
"""Сама страница: один файл, без внешних библиотек и шрифтов.

Разметка, стили и скрипт лежат вместе нарочно — страница должна открываться
на VPS без сборки, CDN и второго порта. Логика тут только рисующая: что
показывать, решает `web.py`, а что отвечать — обработчики бота.
"""

PAGE = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="light dark">
<title>Дайджест</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><text y='26' font-size='26'>📡</text></svg>">
<style>
:root {
  --bg: #f4f5f7; --card: #ffffff; --ink: #16181d; --dim: #6b7280;
  --line: #e3e5ea; --accent: #2f6fed; --accent-ink: #ffffff;
  --me: #dfe9ff; --ok: #1f9254; --warn: #b45309;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #14161a; --card: #1c1f25; --ink: #e8eaee; --dim: #9aa1ad;
    --line: #2a2e36; --accent: #5b8dff; --accent-ink: #0c0e12;
    --me: #24344f; --ok: #4ade80; --warn: #fbbf24;
  }
}
* { box-sizing: border-box; }
html, body { height: 100%; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font: 16px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
        "Helvetica Neue", Arial, sans-serif;
  -webkit-text-size-adjust: 100%;
}
a { color: var(--accent); }
button { font: inherit; cursor: pointer; }

/* ------------------------------------------------------------------ вход */
#login {
  display: none; min-height: 100%; align-items: center; justify-content: center;
  padding: 24px;
}
#login.on { display: flex; }
#login form {
  background: var(--card); border: 1px solid var(--line); border-radius: 16px;
  padding: 28px 24px; width: 100%; max-width: 360px; text-align: center;
}
#login h1 { font-size: 20px; margin: 0 0 4px; }
#login p { color: var(--dim); font-size: 14px; margin: 0 0 18px; }
input[type=password], input[type=text] {
  width: 100%; padding: 12px 14px; border-radius: 10px; font: inherit;
  border: 1px solid var(--line); background: var(--bg); color: var(--ink);
}
input:focus { outline: 2px solid var(--accent); outline-offset: 1px; }
.primary {
  background: var(--accent); color: var(--accent-ink); border: 0;
  border-radius: 10px; padding: 12px 16px; width: 100%; margin-top: 12px;
  font-weight: 600;
}
.err { color: #d64545; font-size: 14px; min-height: 20px; margin-top: 10px; }

/* -------------------------------------------------------------- каркас */
#app { display: none; flex-direction: column; height: 100%; }
#app.on { display: flex; }
header {
  background: var(--card); border-bottom: 1px solid var(--line);
  padding: 10px 16px calc(10px + env(safe-area-inset-top)); position: sticky; top: 0; z-index: 5;
}
.bar { display: flex; align-items: center; gap: 10px; max-width: 820px; margin: 0 auto; }
.bar h1 { font-size: 17px; margin: 0; flex: 1; }
.ghost {
  background: transparent; border: 1px solid var(--line); color: var(--dim);
  border-radius: 8px; padding: 6px 10px; font-size: 13px;
}
#status { max-width: 820px; margin: 6px auto 0; color: var(--dim); font-size: 13px; }
#status b { color: var(--ink); font-weight: 600; }
#busy { color: var(--warn); }

main {
  flex: 1; overflow-y: auto; padding: 16px;
}
#feed { max-width: 820px; margin: 0 auto; display: flex; flex-direction: column; gap: 12px; }
.msg {
  background: var(--card); border: 1px solid var(--line); border-radius: 14px;
  padding: 12px 14px; overflow-wrap: anywhere;
}
.msg.me {
  background: var(--me); border-color: transparent; align-self: flex-end;
  max-width: 80%; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 14px;
}
.msg .at { color: var(--dim); font-size: 12px; margin-bottom: 4px; }
.msg .body { white-space: pre-wrap; }
.msg .body pre { white-space: pre-wrap; margin: 6px 0; }
.msg .body code {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 90%;
  background: rgba(127,127,127,.14); padding: 1px 5px; border-radius: 5px;
}
.keys { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.key {
  background: var(--bg); border: 1px solid var(--line); color: var(--ink);
  border-radius: 999px; padding: 5px 12px; font-size: 14px;
}
.key.on { border-color: var(--accent); color: var(--accent); font-weight: 600; }
.empty { color: var(--dim); text-align: center; padding: 40px 0; }

footer {
  background: var(--card); border-top: 1px solid var(--line);
  padding: 10px 16px calc(10px + env(safe-area-inset-bottom));
}
.wrap { max-width: 820px; margin: 0 auto; }
#chips, #topics { display: flex; gap: 6px; overflow-x: auto; padding-bottom: 8px; }
#chips button, #topics button {
  background: var(--bg); border: 1px solid var(--line); color: var(--ink);
  border-radius: 999px; padding: 6px 12px; font-size: 14px; white-space: nowrap;
}
#topics button { border-color: var(--accent); color: var(--accent); }
#ask { display: flex; gap: 8px; }
#ask input { flex: 1; }
#ask button {
  background: var(--accent); color: var(--accent-ink); border: 0;
  border-radius: 10px; padding: 0 18px; font-weight: 600;
}
#toast {
  position: fixed; left: 50%; bottom: 96px; transform: translateX(-50%);
  background: var(--ink); color: var(--bg); padding: 10px 16px;
  border-radius: 999px; font-size: 14px; opacity: 0; transition: opacity .2s;
  pointer-events: none; max-width: 90%; text-align: center; z-index: 9;
}
#toast.on { opacity: .95; }
</style>
</head>
<body>

<div id="login">
  <form onsubmit="return login(event)">
    <h1>📡 Дайджест</h1>
    <p>Личная страница бота. Введите пароль&nbsp;— он лежит в
       <code>~/.newsdigest/env</code>, строка&nbsp;<code>ND_WEB_TOKEN</code>.</p>
    <input type="password" id="pass" placeholder="Пароль"
           autocomplete="current-password" autofocus>
    <button class="primary" type="submit">Войти</button>
    <div class="err" id="loginErr"></div>
  </form>
</div>

<div id="app">
  <header>
    <div class="bar">
      <h1>📡 Дайджест</h1>
      <button class="ghost" onclick="refresh(true)">Обновить</button>
      <button class="ghost" onclick="logout()">Выйти</button>
    </div>
    <div id="status"></div>
  </header>

  <main id="scroll"><div id="feed"></div></main>

  <footer>
    <div class="wrap">
      <div id="topics"></div>
      <div id="chips"></div>
      <form id="ask" onsubmit="return send(event)">
        <input type="text" id="text" placeholder="Команда, например /digest"
               autocomplete="off" autocapitalize="off" spellcheck="false">
        <button type="submit">▶</button>
      </form>
    </div>
  </footer>
</div>

<div id="toast"></div>

<script>
var last = 0, timer = null, started = false;
var $ = function (id) { return document.getElementById(id); };

/* ------------------------------------------------------------------ сеть */
function call(path, body) {
  var opts = { headers: { 'Content-Type': 'application/json' } };
  if (body) { opts.method = 'POST'; opts.body = JSON.stringify(body); }
  return fetch(path, opts).then(function (res) {
    if (res.status === 401) { showLogin(); return Promise.reject('auth'); }
    return res.json().then(function (data) {
      if (!res.ok) { return Promise.reject(data.error || 'ошибка ' + res.status); }
      return data;
    });
  });
}

function showLogin() {
  stopTimer();
  started = false;
  $('app').className = '';
  $('login').className = 'on';
  var pass = $('pass');
  if (pass) { pass.focus(); }
}

function login(event) {
  event.preventDefault();
  var err = $('loginErr');
  err.textContent = '';
  call('/api/login', { token: $('pass').value }).then(function () {
    $('pass').value = '';
    $('login').className = '';
    $('app').className = 'on';
    start();
  }).catch(function (reason) {
    err.textContent = typeof reason === 'string' ? reason : 'не пускает';
  });
  return false;
}

function logout() {
  call('/api/logout', {}).then(showLogin).catch(showLogin);
}

/* --------------------------------------------------------------- рисуем */
function esc(text) {
  return String(text).replace(/[&<>"]/g, function (ch) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[ch];
  });
}

function drawStatus(state) {
  var sections = state.sections || [];
  var bits = ['Разделов: <b>' + sections.length + '</b> · источников ' + state.feeds,
              'Выпуск ' + esc(state.next) + ', по ' + state.each + ' на раздел'];
  if (state.paused) { bits.push('<b>⏸ на паузе</b>'); }
  if (state.busy) { bits.push('<span id="busy">выполняется: ' + esc(state.busy) + '</span>'); }
  if (!state.owner) { bits.push('<span id="busy">TELEGRAM_CHAT_ID не задан</span>'); }
  $('status').innerHTML = bits.join(' · ');
}

/* раздел одной кнопкой: то же, что /news <раздел> в чате */
function drawTopics(sections) {
  var box = $('topics');
  if (box.childNodes.length === sections.length) { return; }
  box.innerHTML = '';
  sections.forEach(function (section) {
    var button = document.createElement('button');
    button.type = 'button';
    button.textContent = section.label;
    button.title = 'Топ новостей раздела';
    button.onclick = function () { run('/news ' + section.id); };
    box.appendChild(button);
  });
}

function drawChips(commands) {
  var box = $('chips');
  if (box.childNodes.length === commands.length) { return; }
  box.innerHTML = '';
  commands.forEach(function (cmd) {
    var button = document.createElement('button');
    button.type = 'button';
    button.textContent = '/' + cmd.name;
    button.title = cmd.help;
    button.onclick = function () { run('/' + cmd.name); };
    box.appendChild(button);
  });
}

function drawMessage(message) {
  var box = document.createElement('div');
  box.className = 'msg' + (message.kind === 'me' ? ' me' : '');
  box.id = 'm' + message.id;
  var head = message.kind === 'me' ? '' :
      '<div class="at">' + esc(message.at) + '</div>';
  box.innerHTML = head + '<div class="body">' + message.html + '</div>';
  /* ряд кнопок = одна новость, как в Telegram */
  (message.buttons || []).forEach(function (row) {
    var keys = document.createElement('div');
    keys.className = 'keys';
    row.forEach(function (button) { keys.appendChild(drawKey(button)); });
    box.appendChild(keys);
  });
  return box;
}

function drawKey(button) {
  var el = document.createElement('button');
  el.type = 'button';
  el.className = 'key' + (button.pressed ? ' on' : '');
  el.textContent = button.text;
  el.setAttribute('data-press', button.data);
  el.onclick = function () { react(button.data); };
  return el;
}

function apply(data) {
  var scroll = $('scroll');
  var atBottom = scroll.scrollTop + scroll.clientHeight >= scroll.scrollHeight - 80;
  if (data.state) { drawStatus(data.state); drawTopics(data.state.sections || []); }
  if (data.commands) { drawChips(data.commands); }
  var feed = $('feed');
  (data.messages || []).forEach(function (message) {
    var old = $('m' + message.id);
    if (old) { old.replaceWith(drawMessage(message)); } else { feed.appendChild(drawMessage(message)); }
  });
  if (!feed.childNodes.length) {
    feed.innerHTML = '<div class="empty">Пока пусто. Нажмите /digest — ' +
                     'выпуск придёт сюда и в Telegram.</div>';
  }
  if (typeof data.last === 'number' && data.last > last) { last = data.last; }
  if (data.press) { repaint(data.press); }
  if (data.toast) { toast(data.toast); }
  if (atBottom || (data.messages || []).length) { scroll.scrollTop = scroll.scrollHeight; }
}

function repaint(press) {
  if (!press.hash) { return; }
  var keys = document.querySelectorAll('[data-press]');
  Array.prototype.forEach.call(keys, function (el) {
    var parts = el.getAttribute('data-press').split(':');
    if (parts[0] !== 'fb' || parts[2] !== press.hash) { return; }
    el.className = 'key' + (press.pressed && press.pressed[parts[1]] ? ' on' : '');
  });
}

var toastTimer = null;
function toast(text) {
  var box = $('toast');
  box.textContent = text;
  box.className = 'on';
  clearTimeout(toastTimer);
  toastTimer = setTimeout(function () { box.className = ''; }, 2600);
}

/* -------------------------------------------------------------- действия */
function refresh(manual) {
  return call('/api/feed?after=' + last).then(apply).catch(function (reason) {
    if (manual && reason !== 'auth') { toast('Не отвечает: ' + reason); }
  });
}

function run(text) {
  return call('/api/command?after=' + last, { text: text }).then(apply)
    .catch(function (reason) { if (reason !== 'auth') { toast('' + reason); } });
}

function react(data) {
  return call('/api/react?after=' + last, { data: data }).then(apply)
    .catch(function (reason) { if (reason !== 'auth') { toast('' + reason); } });
}

function send(event) {
  event.preventDefault();
  var input = $('text');
  var text = input.value.trim();
  if (!text) { return false; }
  input.value = '';
  run(text);
  return false;
}

/* ------------------------------------------------------ опрос и запуск */
function stopTimer() { if (timer) { clearInterval(timer); timer = null; } }

function startTimer() {
  stopTimer();
  timer = setInterval(function () {
    if (!document.hidden) { refresh(false); }
  }, 5000);
}

function start() {
  if (started) { return; }
  started = true;
  last = 0;
  $('feed').innerHTML = '';
  refresh(false);
  startTimer();
}

document.addEventListener('visibilitychange', function () {
  if (!document.hidden && started) { refresh(false); }
});

call('/api/feed').then(function (data) {
  $('login').className = '';
  $('app').className = 'on';
  started = true;
  apply(data);
  startTimer();
}).catch(function () { /* 401 уже показал форму входа */ });
</script>
</body>
</html>
"""

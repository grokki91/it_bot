# -*- coding: utf-8 -*-
"""Сама страница: один файл, без внешних библиотек и шрифтов.

Разметка, стили и скрипт лежат вместе нарочно — страница должна открываться
на VPS без сборки, CDN и второго порта. Логика тут только рисующая: что
показывать, решает `web.py`.

Страница устроена как новостной сайт: слева разделы, в центре лента карточек,
справа справка о выпуске, популярные источники и темы. Левое меню — это только
разделы: оно не прокручивается и всегда видно целиком, а если разделов больше,
чем влезает в экран, список сам переходит на более плотный шаг. Всё служебное
переехало в шапку: «Избранное» (звёздочка), «Уведомления» (колокольчик) — это
список рассылок: когда пришла, сколько было новостей и пять главных ссылок, —
и «Настройки» (человечек) — подписчики и значения настроек, только для
чтения.

Кнопка «Фильтры» над лентой закрепляет разделы: можно оставить один, можно
несколько («только наука, спорт и экономика») — и «Главное» покажет новости
только из них. Выбранное видно плашками над лентой, снимается нажатием на
плашку и переживает закрытие браузера: набор лежит в localStorage. Пока не
выбрано ничего, полосы плашек нет вовсе — второй список разделов рядом с
левым меню только мешал бы.

Ни строки ввода, ни кнопок «собрать», ни истории запусков здесь нет: боту
командуют на самом VPS, а страница — читалка.

Карточка — это заголовок и текст новости: по одному заголовку не понять, о чём
речь, а ходить за этим на сайт источника читатель не нанимался. Текст занимает
три строки и разворачивается нажатием: так карточки одного роста и на экране
их помещается больше одной.

Срочное (см. `breaking.py`) в ленте видно до чтения: карточка стоит в красной
рамке и с плашкой «⚡ Срочно» над заголовком. Цветом одним дело не обходится —
на чёрно-белом экране и при дальтонизме он не читается, поэтому рядом с рамкой
всегда стоит слово. То же и в «Уведомлениях»: рамка у самого уведомления,
«⚡ Срочно» вместо «📬 Выпуск» в заголовке, молния у ссылки, если срочная
новость легла в историю рядом с плановым выпуском, и ⚡ вместо счётчика на
колокольчике, пока непрочитанное срочное есть.

Картинок к новостям у нас нет и быть не может: в RSS они попадаются далеко не
всегда, а грузить их со сторонних сайтов значит показать этим сайтам, кто и
когда читает вашу ленту (и продырявить CSP, которая сейчас не пускает наружу
вообще ничего). Поэтому обложка карточки рисуется на месте: значок раздела на
его же цвете.
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
  --bg: #f1f3f7; --card: #ffffff; --ink: #14161b; --dim: #6b7280;
  --line: #e5e7eb; --soft: #f3f5f9; --accent: #2f6fed; --accent-ink: #ffffff;
  --tint: #e8effd; --warn: #b45309; --star: #f5a524;
  --hot: #e5484d; --hot-tint: #fff1f1; --hot-ring: rgba(229, 72, 77, .16);
  --shadow: 0 1px 2px rgba(16, 24, 40, .06);
  --tone-l: 40%; --tone-s: 68%;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #101216; --card: #1a1d23; --ink: #e8eaee; --dim: #98a0ac;
    --line: #272b33; --soft: #22262e; --accent: #5b8dff; --accent-ink: #0c0e12;
    --tint: #1c2740; --warn: #fbbf24; --star: #fbbf24;
    --hot: #ff6b6f; --hot-tint: #241417; --hot-ring: rgba(255, 107, 111, .22);
    --shadow: none;
    --tone-l: 68%; --tone-s: 62%;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font: 16px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
        "Helvetica Neue", Arial, sans-serif;
  -webkit-text-size-adjust: 100%;
}
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
button { font: inherit; cursor: pointer; color: inherit; }
h1, h2, h3 { margin: 0; }
.hide { display: none !important; }

/* ------------------------------------------------------------------ вход */
#login {
  display: none; min-height: 100vh; align-items: center; justify-content: center;
  padding: 24px;
}
#login.on { display: flex; }
#login form {
  background: var(--card); border: 1px solid var(--line); border-radius: 18px;
  padding: 28px 24px; width: 100%; max-width: 360px; text-align: center;
  box-shadow: var(--shadow);
}
#login h1 { font-size: 20px; margin-bottom: 4px; }
#login p { color: var(--dim); font-size: 14px; margin: 0 0 18px; }
input[type=password], input[type=text] {
  width: 100%; padding: 12px 14px; border-radius: 12px; font: inherit;
  border: 1px solid var(--line); background: var(--bg); color: var(--ink);
}
input:focus { outline: 2px solid var(--accent); outline-offset: 1px; }
.primary {
  background: var(--accent); color: var(--accent-ink); border: 0;
  border-radius: 12px; padding: 12px 16px; width: 100%; margin-top: 12px;
  font-weight: 600;
}
.err { color: #d64545; font-size: 14px; min-height: 20px; margin-top: 10px; }

/* ---------------------------------------------------------------- шапка */
#app { display: none; }
#app.on { display: block; }
header {
  position: sticky; top: 0; z-index: 20; background: var(--card);
  border-bottom: 1px solid var(--line);
  padding: 10px 20px calc(10px + env(safe-area-inset-top));
}
.top {
  display: flex; align-items: center; gap: 14px; flex-wrap: wrap;
  max-width: 1460px; margin: 0 auto;
}
.brand {
  display: flex; align-items: center; gap: 8px; font-size: 18px;
  font-weight: 700; width: 216px; flex: none;
}
.brand span { font-size: 22px; }
.brand { cursor: pointer; user-select: none; }
.brand:focus-visible { outline: 2px solid var(--dim); outline-offset: 4px;
                       border-radius: 8px; }
.search { flex: 1 1 240px; position: relative; min-width: 0; }
.search input {
  background: var(--soft); border-color: transparent; padding-left: 40px;
  border-radius: 12px;
}
.search .lens {
  position: absolute; left: 14px; top: 50%; transform: translateY(-50%);
  color: var(--dim); pointer-events: none;
}
.search .clear {
  position: absolute; right: 8px; top: 50%; transform: translateY(-50%);
  background: none; border: 0; color: var(--dim); padding: 6px 8px;
  border-radius: 8px;
}
.tools { display: flex; gap: 8px; margin-left: auto; }
.icon {
  position: relative; width: 42px; height: 42px; border-radius: 50%;
  background: var(--card); border: 1px solid var(--line); font-size: 17px;
  display: flex; align-items: center; justify-content: center;
}
.icon.on { border-color: var(--accent); color: var(--accent); }
.icon .badge {
  position: absolute; top: -2px; right: -2px; min-width: 18px; height: 18px;
  padding: 0 5px; border-radius: 9px; background: #e5484d; color: #fff;
  font-size: 11px; line-height: 18px; font-weight: 700;
}

/* --------------------------------------------------------------- каркас */
.shell {
  display: grid; grid-template-columns: 216px minmax(0, 1fr) 320px; gap: 20px;
  max-width: 1460px; margin: 0 auto; padding: 20px; align-items: start;
}
.side, .rail { position: sticky; top: 84px; }
/* Меню разделов не прокручивается: список целиком стоит перед глазами, а если
   разделов больше, чем влезает, страница сама переходит на более плотный шаг
   (см. fitNav) — вместо полосы прокрутки, из-под которой раньше выглядывали
   служебные кнопки. */
.side {
  --nav-gap: 2px; --item-pad: 10px 12px; --item-font: 15px; --item-ico: 17px;
  --item-round: 12px; --foot-pad: 18px;
  display: flex; flex-direction: column; overflow: hidden;
  max-height: calc(100vh - 104px);
}
.side.d1 { --nav-gap: 2px; --item-pad: 8px 11px; --item-font: 14.5px;
           --item-ico: 16px; --item-round: 11px; --foot-pad: 14px; }
.side.d2 { --nav-gap: 1px; --item-pad: 6px 10px; --item-font: 14px;
           --item-ico: 15px; --item-round: 10px; --foot-pad: 10px; }
.side.d3 { --nav-gap: 1px; --item-pad: 4px 10px; --item-font: 13.5px;
           --item-ico: 14px; --item-round: 9px; --foot-pad: 8px; }
.side.d4 { --nav-gap: 0px; --item-pad: 2px 9px; --item-font: 13px;
           --item-ico: 13px; --item-round: 8px; --foot-pad: 6px; }
.side nav {
  display: flex; flex-direction: column; gap: var(--nav-gap);
  flex: 0 1 auto; min-height: 0; overflow: hidden;
}
.side .foot {
  color: var(--dim); font-size: 12px; padding: var(--foot-pad) 12px 0;
  flex: none;
}
.side.d4 .foot { display: none; }
/* Крайний случай: разделов столько, что не спасает и самый плотный шаг.
   Прокрутка тут — меньшее зло, чем разделы, срезанные краем экрана. */
.side.roomy nav { overflow-y: auto; }
.item {
  display: flex; align-items: center; gap: 12px; padding: var(--item-pad);
  border-radius: var(--item-round); border: 0; background: none; width: 100%;
  text-align: left; font-size: var(--item-font); font-weight: 500;
}
.item:hover { background: var(--card); }
.item.on { background: var(--tint); color: var(--accent); font-weight: 600; }
.item .ico { font-size: var(--item-ico); width: 22px; text-align: center;
             flex: none; }
.item .name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis;
              white-space: nowrap; }
.item .num { color: var(--dim); font-size: 12px; font-weight: 500; }
.item.on .num { color: var(--accent); }

/* ----------------------------------------------------------------- лента */
.head { display: flex; align-items: center; gap: 12px; margin-bottom: 16px; }
.head h1 { font-size: 27px; letter-spacing: -.02em; }
.head .meta {
  color: var(--dim); font-size: 13px; margin-left: auto; text-align: right;
}
.tune {
  background: var(--accent); color: var(--accent-ink); border: 0;
  border-radius: 12px; padding: 10px 16px; font-weight: 600; font-size: 14px;
  white-space: nowrap;
}
/* плашки — это закреплённые фильтры, а не второй список разделов: разделы и
   так стоят слева. Фильтров не выбрано — полосы нет вовсе. Выбрано много —
   лишние уезжают вправо, и край растушёван, чтобы это было видно */
.chips {
  display: flex; gap: 8px; overflow-x: auto; padding-bottom: 14px;
  scrollbar-width: none;
  -webkit-mask-image: linear-gradient(to right, #000 92%, transparent);
  mask-image: linear-gradient(to right, #000 92%, transparent);
}
.chips::-webkit-scrollbar { display: none; }
.chips button {
  background: var(--card); border: 1px solid var(--line); color: var(--ink);
  border-radius: 999px; padding: 8px 16px; font-size: 14px; white-space: nowrap;
}
.chips button.on {
  background: var(--accent); border-color: var(--accent); color: var(--accent-ink);
  font-weight: 600;
}
.chips button .x { margin-left: 8px; opacity: .75; font-weight: 400; }

/* ----------------------------------------------------------------- фильтры */
/* Выбор разделов ленты: подложка на весь экран и панель по центру. Отмечают
   галочками, а лента меняется по «Применить» — чтобы набор из трёх разделов
   не собирался тремя запросами к базе */
.sheet {
  display: none; position: fixed; top: 0; right: 0; bottom: 0; left: 0;
  z-index: 40; background: rgba(16, 24, 40, .45); padding: 20px;
  align-items: center; justify-content: center;
}
.sheet.on { display: flex; }
.pane {
  background: var(--card); border: 1px solid var(--line); border-radius: 18px;
  padding: 20px; width: 100%; max-width: 420px; max-height: 84vh;
  display: flex; flex-direction: column;
  box-shadow: 0 14px 44px rgba(16, 24, 40, .3);
}
.pane h3 { font-size: 17px; }
.pane .hint { color: var(--dim); font-size: 13px; margin: 6px 0 0; }
.pick {
  display: flex; flex-direction: column; gap: 2px; margin: 14px 0 16px;
  overflow-y: auto;
}
.pick button {
  display: flex; align-items: center; gap: 10px; width: 100%; text-align: left;
  background: none; border: 0; border-radius: 11px; padding: 9px 10px;
  font-size: 15px;
}
.pick button:hover { background: var(--soft); }
.pick button.on { background: var(--tint); color: var(--accent); font-weight: 600; }
.pick .tick {
  width: 20px; height: 20px; border-radius: 6px; flex: none;
  border: 1.5px solid var(--line); background: var(--bg); color: transparent;
  display: flex; align-items: center; justify-content: center; font-size: 12px;
}
.pick button.on .tick {
  background: var(--accent); border-color: var(--accent); color: var(--accent-ink);
}
.pick .ico { font-size: 17px; width: 22px; text-align: center; flex: none; }
.pick .nm { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis;
            white-space: nowrap; }
.pick .num { color: var(--dim); font-size: 12px; font-weight: 500; }
.pane .pair .primary { width: auto; flex: 1; margin-top: 0; padding: 10px 16px; }
.pane .pair .ghost { padding: 10px 16px; }
#list { display: flex; flex-direction: column; gap: 12px; }

.news {
  display: flex; gap: 16px; background: var(--card); border-radius: 16px;
  border: 1px solid var(--line); box-shadow: var(--shadow); padding: 16px 18px;
}
.news .text { flex: 1; min-width: 0; }
.news .line {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  font-size: 12px; color: var(--dim); margin-bottom: 8px;
}
.news .tag {
  color: hsl(var(--h) var(--tone-s) var(--tone-l));
  font-weight: 700; text-transform: uppercase; letter-spacing: .04em;
}
.news .star { color: var(--star); }
/* срочное видно ещё до чтения: карточка в красной рамке с мягким ободком и
   чуть тёплой подложкой. Плашка раздела при этом остаётся своего цвета —
   «срочно» отвечает на вопрос «когда пришло», а не «о чём» */
.news.hot {
  border-color: var(--hot);
  box-shadow: 0 0 0 3px var(--hot-ring), var(--shadow);
  background: var(--hot-tint);
}
.news .hot-tag {
  color: var(--hot); font-weight: 800; text-transform: uppercase;
  letter-spacing: .04em; white-space: nowrap;
}
/* заголовок нередко начинается с хеша коммита — неразрывного слова длиннее
   экрана. Без переноса такое слово распирает карточку, а вместе с ней и всю
   страницу: на телефоне появлялась горизонтальная прокрутка */
.news h2 {
  font-size: 19px; line-height: 1.32; letter-spacing: -.01em; font-weight: 650;
  overflow-wrap: anywhere;
}
/* заголовок — ссылка, но выглядеть должен заголовком, а не ссылкой */
.news h2 a { color: inherit; }
.news h2 a:hover { color: var(--accent); text-decoration: none; }
/* текст новости: три строки в превью, остальное — по нажатию. Без обрезки
   одна многословная карточка занимала бы экран целиком */
.news .sum {
  margin: 8px 0 0; color: var(--dim); font-size: 14px; line-height: 1.45;
  overflow-wrap: anywhere;
  display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical;
  overflow: hidden;
}
.news .sum.more { cursor: pointer; }
.news .sum.open { -webkit-line-clamp: 99; }
.news .foot { display: flex; align-items: center; gap: 6px; margin-top: 14px; }
.news .src { margin-left: auto; font-size: 13px; display: flex; gap: 5px; }
.act {
  background: none; border: 0; padding: 5px 7px; border-radius: 9px;
  font-size: 15px; line-height: 1; opacity: .62;
}
.act:hover { background: var(--soft); opacity: 1; }
.act.on { opacity: 1; background: var(--tint); }
.cover {
  width: 168px; height: 108px; border-radius: 12px; flex: none;
  display: flex; align-items: center; justify-content: center; font-size: 40px;
  background: linear-gradient(140deg, hsl(var(--h) 62% 62%),
                                      hsl(var(--h) 58% 38%));
}
.empty {
  background: var(--card); border: 1px solid var(--line); border-radius: 16px;
  padding: 44px 24px; text-align: center; color: var(--dim);
}
.empty b { display: block; color: var(--ink); font-size: 17px; margin-bottom: 6px; }
#more {
  display: block; margin: 16px auto 0; background: var(--card);
  border: 1px solid var(--line); color: var(--ink); border-radius: 12px;
  padding: 11px 24px; font-size: 14px; font-weight: 600;
}

/* ------------------------------------------------------------ правая колонка */
.rail { display: flex; flex-direction: column; gap: 12px; }
.box {
  background: var(--card); border: 1px solid var(--line); border-radius: 16px;
  padding: 16px; box-shadow: var(--shadow);
}
.box h3 { font-size: 15px; margin-bottom: 12px; }
.box .who { display: flex; align-items: center; gap: 8px; font-weight: 700; }
.box .facts { color: var(--dim); font-size: 13px; margin: 10px 0 14px; }
.box .facts div { margin-top: 2px; }
.pair { display: flex; gap: 8px; }
.ghost {
  flex: 1; background: var(--soft); border: 1px solid var(--line);
  color: var(--ink); border-radius: 11px; padding: 10px; font-size: 14px;
  font-weight: 500;
}
.ghost.wide { width: 100%; margin-top: 12px; }
.rows { display: flex; flex-direction: column; gap: 10px; }
.rows button {
  display: flex; align-items: center; gap: 10px; background: none; border: 0;
  padding: 0; width: 100%; text-align: left; font-size: 14px;
}
.rows .dot {
  width: 26px; height: 26px; border-radius: 8px; flex: none; color: #fff;
  display: flex; align-items: center; justify-content: center; font-size: 12px;
  font-weight: 700; background: hsl(var(--h) 58% 48%);
}
.rows .nm { flex: 1; color: var(--accent); overflow: hidden;
            text-overflow: ellipsis; white-space: nowrap; }
.rows .rate { color: var(--dim); font-size: 13px; white-space: nowrap; }
.tags { display: flex; flex-wrap: wrap; gap: 8px; }
.tags button {
  background: var(--soft); border: 1px solid var(--line); border-radius: 10px;
  padding: 7px 12px; font-size: 13px; color: var(--accent);
}
.warn { color: var(--warn); }

/* ------------------------------------------------------------ уведомления */
/* Одна рассылка — одна карточка: когда пришла, сколько в ней было и пять
   самых важных ссылок. Ни переписки, ни команд здесь нет и не должно быть */
#alerts { display: flex; flex-direction: column; gap: 12px; }
.mail {
  background: var(--card); border: 1px solid var(--line); border-radius: 16px;
  padding: 16px 18px; box-shadow: var(--shadow);
}
.mail h2 {
  font-size: 17px; display: flex; align-items: baseline; gap: 8px;
  flex-wrap: wrap;
}
.mail h2 .at { color: var(--dim); font-size: 14px; font-weight: 400; }
.mail .facts { color: var(--dim); font-size: 13px; margin-top: 4px; }
.mail ul {
  list-style: none; margin: 12px 0 0; padding: 0; display: flex;
  flex-direction: column; gap: 10px;
}
.mail li { display: flex; gap: 8px; font-size: 14.5px; line-height: 1.4; }
.mail li .ico { flex: none; }
/* та же беда, что и с заголовком новости: длинная ссылка без пробелов */
.mail li span { min-width: 0; overflow-wrap: anywhere; }
.mail .from { color: var(--dim); font-size: 13px; white-space: nowrap; }
.mail .star { color: var(--star); white-space: nowrap; }
/* срочное и в уведомлениях: та же рамка, что у карточки, и молния у самой
   строки — в общем выпуске срочная новость идёт вперемешку с остальными */
.mail.hot {
  border-color: var(--hot);
  box-shadow: 0 0 0 3px var(--hot-ring), var(--shadow);
  background: var(--hot-tint);
}
.mail h2 .hot-tag {
  color: var(--hot); font-size: 13px; font-weight: 800;
  text-transform: uppercase; letter-spacing: .04em; white-space: nowrap;
}
.mail li .bolt { color: var(--hot); font-weight: 700; white-space: nowrap; }
/* колокольчик: пока непрочитанное срочное — на значке молния вместо счёта.
   Число тут и так небольшое, а «сколько» рядом со «срочно» никого не спасает */
.icon .badge.hot, .tabs button .badge.hot { background: var(--hot); }

/* --------------------------------------------------------------- настройки */
#panel { display: flex; flex-direction: column; gap: 12px; }
.opts { display: flex; flex-direction: column; gap: 12px; }
.opt { font-size: 14px; line-height: 1.45; }
.opt .nm {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 13px;
  background: rgba(127, 127, 127, .14); padding: 1px 6px; border-radius: 5px;
}
.opt .val { font-weight: 700; margin-left: 6px; }
.opt .about { color: var(--dim); font-size: 13px; display: block; }
.folk { display: flex; flex-direction: column; gap: 12px; }
.folk .man { font-size: 14px; display: flex; align-items: baseline; gap: 8px;
             flex-wrap: wrap; }
.folk .id {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 13px;
  color: var(--dim);
}
.folk .about { color: var(--dim); font-size: 13px; }

/* ------------------------------------------------------- нижняя навигация */
.tabs {
  display: none; position: fixed; left: 0; right: 0; bottom: 0; z-index: 20;
  background: var(--card); border-top: 1px solid var(--line);
  padding: 6px 4px calc(6px + env(safe-area-inset-bottom));
}
.tabs button {
  flex: 1; background: none; border: 0; padding: 6px 2px; font-size: 11px;
  color: var(--dim); display: flex; flex-direction: column; align-items: center;
  gap: 3px; position: relative;
}
.tabs button b { font-size: 19px; font-weight: 400; line-height: 1; }
.tabs button.on { color: var(--accent); }
.tabs .badge {
  position: absolute; top: 2px; right: 50%; margin-right: -18px; min-width: 16px;
  height: 16px; padding: 0 4px; border-radius: 8px; background: #e5484d;
  color: #fff; font-size: 10px; line-height: 16px; font-weight: 700;
}

#toast {
  position: fixed; left: 50%; bottom: 92px; transform: translateX(-50%);
  background: var(--ink); color: var(--bg); padding: 11px 18px;
  border-radius: 999px; font-size: 14px; opacity: 0; transition: opacity .2s;
  pointer-events: none; max-width: 90%; text-align: center; z-index: 30;
}
#toast.on { opacity: .95; }

@media (max-width: 1180px) {
  .shell { grid-template-columns: 200px minmax(0, 1fr); }
  .rail { display: none; }
  .brand { width: 180px; }
}
@media (max-width: 860px) {
  header { padding: 8px 12px calc(8px + env(safe-area-inset-top)); }
  .brand { width: auto; font-size: 16px; }
  .search { order: 3; flex-basis: 100%; }
  .shell { grid-template-columns: minmax(0, 1fr); padding: 14px 12px 88px;
           gap: 0; }
  .side { display: none; }
  /* «Избранное» на телефоне живёт в нижней панели — в шапке звезда только
     теснила бы поиск */
  #star { display: none; }
  .tabs { display: flex; }
  .head { flex-wrap: wrap; }
  .head h1 { font-size: 23px; }
  .head .meta { margin-left: 0; text-align: left; flex-basis: 100%; order: 3; }
  .tune { margin-left: auto; }
  .news { padding: 14px; gap: 12px; }
  .news h2 { font-size: 17px; }
  .news .line { font-size: 11px; gap: 6px; }
  .news .sum { margin-top: 6px; font-size: 13.5px; }
  .news .foot { margin-top: 10px; }
  /* на телефоне карточка не должна занимать полэкрана: обложка мельче,
     а текст новости всё так же сворачивается до трёх строк */
  .cover { width: 84px; height: 84px; align-self: flex-start; font-size: 28px; }
}
@media (max-width: 560px) {
  /* на узком экране обложка отъедала у текста треть строки, и три строки сути
     превращались в шесть. Значок раздела и так стоит в первой строке карточки,
     поэтому обложке здесь остаётся роль метки — маленькой */
  .news { padding: 12px; gap: 10px; }
  .news h2 { font-size: 16px; }
  .cover { width: 56px; height: 56px; font-size: 22px; border-radius: 10px; }
  .head h1 { font-size: 21px; }
  .tune { padding: 9px 12px; font-size: 13px; }
}
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
    <div class="top">
      <div class="brand" id="brand" role="button" tabindex="0"
           title="На главную" onclick="home()"
           onkeydown="if (event.key === 'Enter' || event.key === ' ')
                      { event.preventDefault(); home(); }">
        <span>📡</span> Дайджест
      </div>
      <form class="search" onsubmit="return search(event)">
        <span class="lens">🔍</span>
        <input type="text" id="q" autocomplete="off" spellcheck="false"
               placeholder="Поиск по новостям, темам или источникам"
               oninput="typed()">
        <button type="button" class="clear hide" id="clear"
                onclick="clearSearch()" title="Очистить">✕</button>
      </form>
      <div class="tools">
        <button class="icon" id="star" onclick="go('liked')"
                title="Избранное">⭐</button>
        <button class="icon" id="bell" onclick="go('alerts')"
                title="Уведомления">🔔</button>
        <button class="icon" id="who" onclick="go('tools')"
                title="Настройки">👤</button>
      </div>
    </div>
  </header>

  <div class="shell">
    <aside class="side">
      <nav id="nav"></nav>
      <div class="foot">© Дайджест<br>Все права защищены</div>
    </aside>

    <main>
      <div class="head">
        <h1 id="title">Главное</h1>
        <div class="meta" id="meta"></div>
        <button class="tune" id="tune" onclick="openFilters()">⚙ Фильтры</button>
      </div>
      <div class="chips" id="chips"></div>
      <div id="list"></div>
      <button id="more" class="hide" onclick="loadNews(false)">Показать ещё ⌄</button>

      <div id="alerts" class="hide"></div>
      <div id="panel" class="hide"></div>
    </main>

    <aside class="rail">
      <div class="box" id="boxDigest"></div>
      <div class="box" id="boxSources"></div>
      <div class="box" id="boxTopics"></div>
    </aside>
  </div>

  <nav class="tabs" id="tabs"></nav>
</div>

<div id="picker" class="sheet" onclick="backdrop(event)">
  <div class="pane">
    <h3>Фильтры ленты</h3>
    <p class="hint">Отметьте разделы, которые хотите видеть в «Главном».
       Можно несколько. Ничего не отмечено — показываем все.</p>
    <div class="pick" id="pick"></div>
    <div class="pair">
      <button class="ghost" type="button" onclick="clearPick()">Сбросить</button>
      <button class="primary" type="button" onclick="applyPick()">Применить</button>
    </div>
  </div>
</div>

<div id="toast"></div>

<script>
/* Состояние страницы целиком: что показываем, где остановились в ленте и
   какую рассылку читатель видел последней. */
var S = {
  view: 'news', section: '', q: '', offset: 0, more: false,
  seen: '', unread: 0, hot: false, hello: true, started: false,
  timer: null, typing: null,
  state: null, alerts: [], tools: null, menu: [], side: null,
  filters: [], pick: []
};

var TABS = [
  { id: 'news',   icon: '🏠', name: 'Главная' },
  { id: 'saved',  icon: '🔖', name: 'Сохранённые' },
  { id: 'liked',  icon: '⭐', name: 'Избранное' },
  { id: 'alerts', icon: '🔔', name: 'Уведомления' },
  { id: 'tools',  icon: '⚙',  name: 'Настройки' }
];

var NAMES = { news: 'Главное', saved: 'Сохранённые', liked: 'Избранное',
              alerts: 'Уведомления', tools: 'Настройки' };

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
  S.started = false;
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

/* --------------------------------------------------------------- мелочи */
function esc(text) {
  return String(text == null ? '' : text).replace(/[&<>"]/g, function (ch) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[ch];
  });
}

function el(tag, cls, text) {
  var node = document.createElement(tag);
  if (cls) { node.className = cls; }
  if (text != null) { node.textContent = text; }
  return node;
}

function plural(count, one, few, many) {
  var tail = Math.abs(count) % 100;
  if (tail >= 11 && tail <= 14) { return many; }
  tail = tail % 10;
  if (tail === 1) { return one; }
  if (tail >= 2 && tail <= 4) { return few; }
  return many;
}

var toastTimer = null;
function toast(text) {
  var box = $('toast');
  box.textContent = text;
  box.className = 'on';
  clearTimeout(toastTimer);
  toastTimer = setTimeout(function () { box.className = ''; }, 2600);
}

function isNews(view) { return view === 'news' || view === 'saved' || view === 'liked'; }

/* Цвет квадратика источника: от его имени, чтобы не прыгал между заходами. */
function hueOf(name) {
  var sum = 0;
  for (var i = 0; i < name.length; i++) { sum += name.charCodeAt(i); }
  return (sum * 37) % 360;
}

/* --------------------------------------------------------------- фильтры */
/* Закреплённые разделы: «только наука и спорт». Это про то, как читателю
   удобно смотреть ленту, а не про настройки бота, — поэтому набор живёт в
   браузере и переживает закрытие вкладки. */
var KEEP = 'nd.filters';

function loadFilters() {
  try {
    var saved = JSON.parse(localStorage.getItem(KEEP) || '[]');
    if (!saved || !saved.length) { return []; }
    return saved.filter(function (id) { return typeof id === 'string' && id; });
  } catch (err) {
    return [];        /* приватный режим или запрет хранилища — живём без памяти */
  }
}

function keepFilters() {
  try { localStorage.setItem(KEEP, JSON.stringify(S.filters)); } catch (err) { }
}

S.filters = loadFilters();

/* Фильтры работают в «Главном»: в разделе читатель уже выбрал, что смотреть,
   а «Сохранённые» и «Избранное» он отмечал руками — там резать нечего. */
function filtering() {
  return S.view === 'news' && !S.section && S.filters.length > 0;
}

function menuEntry(id) {
  for (var i = 0; i < S.menu.length; i++) {
    if (S.menu[i].id === id) { return S.menu[i]; }
  }
  return null;
}

/* Сколько новостей увидит читатель в «Главном» с его набором фильтров. */
function countPicked() {
  var sum = 0;
  S.filters.forEach(function (id) {
    var entry = menuEntry(id);
    if (entry) { sum += entry.count || 0; }
  });
  return sum;
}

/* Раздел пропал из меню (новостей нет и в плане его больше нет) — убираем
   его и из набора: плашка висела бы, а лента про него ничего не знает. */
function pruneFilters() {
  if (!S.menu.length || !S.filters.length) { return; }
  var live = S.filters.filter(function (id) { return !!menuEntry(id); });
  if (live.length !== S.filters.length) {
    S.filters = live;
    keepFilters();
  }
}

/* Набор поменялся: запомнили и вернулись в «Главное» — там его и видно. */
function setFilters(list) {
  S.filters = list;
  keepFilters();
  go('news');
}

function dropFilter(id) {
  setFilters(S.filters.filter(function (item) { return item !== id; }));
}

/* ------------------------------------------------------------ навигация */
function go(view, section) {
  S.view = view;
  S.section = view === 'news' ? (section || '') : '';
  if (view === 'alerts') { seen(); }
  window.scrollTo(0, 0);
  paint();
  if (isNews(view)) { loadNews(true); }
  if (view === 'tools') { loadTools(); }
}

/* Клик по логотипу — возврат на главную: чистый поиск и общая лента. */
function home() {
  $('q').value = '';
  $('clear').className = 'clear hide';
  S.q = '';
  go('news');
}

/* Что видно при этом виде: карточки ленты, уведомления или настройки. */
function paint() {
  var news = isNews(S.view);
  $('list').className = news ? '' : 'hide';
  showChips();
  $('more').className = news && S.more ? '' : 'hide';
  $('alerts').className = S.view === 'alerts' ? '' : 'hide';
  $('panel').className = S.view === 'tools' ? '' : 'hide';
  $('title').textContent = S.section ? sectionName(S.section) : NAMES[S.view];
  $('star').className = 'icon' + (S.view === 'liked' ? ' on' : '');
  $('bell').className = 'icon' + (S.view === 'alerts' ? ' on' : '');
  $('who').className = 'icon' + (S.view === 'tools' ? ' on' : '');
  $('tune').textContent = S.filters.length
    ? '⚙ Фильтры · ' + S.filters.length : '⚙ Фильтры';
  drawMeta();
  drawNav();
  drawChips();
  drawTabs();
  if (S.view === 'alerts') { drawAlerts(); }
  if (S.view === 'tools') { drawPanel(); }
}

function sectionName(id) {
  var entry = menuEntry(id);
  return entry ? entry.title : id;
}

function drawMeta() {
  var st = S.state, bits = [];
  if (!st) { $('meta').textContent = ''; return; }
  if (S.q) { bits.push('Поиск: «' + esc(S.q) + '»'); }
  if (st.collected) { bits.push('🕘 Обновлено в ' + esc(st.collected)); }
  bits.push(st.feeds + ' ' + plural(st.feeds, 'источник', 'источника', 'источников'));
  if (st.paused) { bits.push('<b class="warn">⏸ рассылка на паузе</b>'); }
  if (st.busy) { bits.push('<b class="warn">выполняется: ' + esc(st.busy) + '</b>'); }
  $('meta').innerHTML = bits.join(' · ');
}

function drawNav() {
  var nav = $('nav');
  nav.innerHTML = '';
  S.menu.forEach(function (entry) {
    var on = S.view === 'news' && S.section === entry.id;
    /* с фильтрами «Главное» показывает не всё — и число рядом должно быть
       про то, что читатель там увидит */
    var count = !entry.id && S.filters.length ? countPicked() : entry.count;
    nav.appendChild(navItem(entry.emoji, entry.title, count, on,
                            function () { go('news', entry.id); }));
  });
  fitNav();
}

/* Меню живёт без прокрутки: сколько бы разделов ни пришло, они должны
   помещаться в экран целиком. Подбираем шаг списка от просторного к плотному
   и останавливаемся на первом, при котором список перестаёт вылезать. Если не
   влезает даже самый плотный — берём его: это всё, что можно ужать, не теряя
   разделы. */
var DENSITY = ['', 'd1', 'd2', 'd3', 'd4'];

function fitNav() {
  var side = document.querySelector('.side');
  var nav = $('nav');
  if (!side || !nav || !nav.children.length) { return; }
  side.style.cssText = '';
  /* на телефоне меню скрыто, а у скрытого узла высоты нет — мерить нечего */
  if (!side.offsetParent && side.offsetHeight === 0) { return; }
  for (var i = 0; i < DENSITY.length; i++) {
    side.className = 'side' + (DENSITY[i] ? ' ' + DENSITY[i] : '');
    /* лишний пиксель — запас на дробные высоты строк, иначе список ужимался
       бы на ровном месте */
    if (nav.scrollHeight <= nav.clientHeight + 1) { return; }
  }
  /* Не помог и самый плотный шаг: считаем строку прямо под оставшуюся высоту,
     но не мельче читаемого. */
  var row = Math.floor(nav.clientHeight / nav.children.length);
  var font = Math.max(11, Math.min(13, row - 6));
  var pad = Math.max(0, Math.floor((row - font * 1.45) / 2));
  side.style.setProperty('--nav-gap', '0px');
  side.style.setProperty('--item-pad', pad + 'px 8px');
  side.style.setProperty('--item-font', font + 'px');
  side.style.setProperty('--item-ico', Math.max(11, font - 1) + 'px');
  if (nav.scrollHeight > nav.clientHeight + 1) { side.className = 'side d4 roomy'; }
}

var fitTimer = null;
window.addEventListener('resize', function () {
  clearTimeout(fitTimer);
  fitTimer = setTimeout(fitNav, 120);
});

function navItem(icon, name, count, on, act) {
  var button = el('button', 'item' + (on ? ' on' : ''));
  button.type = 'button';
  button.appendChild(el('span', 'ico', icon));
  button.appendChild(el('span', 'name', name));
  if (count) { button.appendChild(el('span', 'num', count)); }
  button.onclick = act;
  return button;
}

function drawTabs() {
  var box = $('tabs');
  box.innerHTML = '';
  TABS.forEach(function (tab) {
    var on = S.view === tab.id && (tab.id !== 'news' || !S.section);
    var button = el('button', on ? 'on' : '');
    button.type = 'button';
    button.appendChild(el('b', null, tab.icon));
    button.appendChild(el('span', null, tab.name));
    if (tab.id === 'alerts' && S.unread) {
      button.appendChild(alertBadge());
    }
    button.onclick = function () { go(tab.id); };
    box.appendChild(button);
  });
  var bell = $('bell');
  bell.innerHTML = '🔔';
  if (S.unread) { bell.appendChild(alertBadge()); }
}

/* Значок непрочитанного. Обычно это счётчик рассылок, но если среди них есть
   срочное — вместо числа молния: важно не «сколько пришло», а «что». */
function alertBadge() {
  return S.hot ? el('span', 'badge hot', '⚡')
               : el('span', 'badge', S.unread);
}

/* Полоса плашек видна, только когда фильтры есть и работают. Меню ещё не
   пришло — плашек нет: имя раздела берётся оттуда, а показывать вместо него
   идентификатор незачем. */
function showChips() {
  var show = filtering() && S.menu.length > 0;
  $('chips').className = 'chips' + (show ? '' : ' hide');
}

/* Плашки над лентой — это выбранные фильтры. Второго списка разделов тут не
   нужно: он и так стоит слева. Нажатие на плашку снимает фильтр. */
function drawChips() {
  var box = $('chips');
  box.innerHTML = '';
  if (!S.filters.length || !S.menu.length) { return; }
  S.filters.forEach(function (id) {
    var entry = menuEntry(id);
    var button = el('button', 'on',
                    entry ? entry.emoji + ' ' + entry.title : id);
    button.type = 'button';
    button.title = 'Убрать из фильтров';
    button.appendChild(el('span', 'x', '✕'));
    button.onclick = function () { dropFilter(id); };
    box.appendChild(button);
  });
  if (S.filters.length > 1) {
    var all = el('button', null, 'Сбросить всё');
    all.type = 'button';
    all.onclick = function () { setFilters([]); };
    box.appendChild(all);
  }
}

/* ------------------------------------------------------- выбор фильтров */
/* Отмечают галочками, а лента меняется по «Применить»: набор из трёх
   разделов не должен собираться тремя запросами к базе. */
function openFilters() {
  S.pick = S.filters.slice();
  $('picker').className = 'sheet on';
  drawPick();
}

function closeFilters() { $('picker').className = 'sheet'; }

/* нажали мимо панели — закрываем, ничего не меняя */
function backdrop(event) {
  if (event.target === $('picker')) { closeFilters(); }
}

function togglePick(id) {
  S.pick = S.pick.indexOf(id) < 0
    ? S.pick.concat([id])
    : S.pick.filter(function (item) { return item !== id; });
  drawPick();
}

function clearPick() { S.pick = []; drawPick(); }

function applyPick() {
  closeFilters();
  setFilters(S.pick.slice());
}

function drawPick() {
  var box = $('pick');
  box.innerHTML = '';
  var live = S.menu.filter(function (entry) { return !!entry.id; });
  if (!live.length) {
    box.appendChild(el('div', 'hint',
      'Разделы появятся здесь, когда придёт первый выпуск.'));
    return;
  }
  live.forEach(function (entry) {
    var on = S.pick.indexOf(entry.id) >= 0;
    var button = el('button', on ? 'on' : '');
    button.type = 'button';
    button.appendChild(el('span', 'tick', '✓'));
    button.appendChild(el('span', 'ico', entry.emoji));
    button.appendChild(el('span', 'nm', entry.title));
    if (entry.count) { button.appendChild(el('span', 'num', entry.count)); }
    button.onclick = function () { togglePick(entry.id); };
    box.appendChild(button);
  });
}

/* ----------------------------------------------------------------- лента */
function loadNews(reset) {
  if (reset) { S.offset = 0; }
  var path = '/api/news?view=' + encodeURIComponent(isNews(S.view) ? S.view : 'news')
           + '&section=' + encodeURIComponent(S.section)
           + '&sections=' + encodeURIComponent(filtering() ? S.filters.join(',') : '')
           + '&q=' + encodeURIComponent(S.q)
           + '&offset=' + S.offset;
  return call(path).then(function (data) {
    if (data.state) { S.state = data.state; }
    if (data.side) {
      S.side = data.side;
      S.menu = data.side.menu || [];
      pruneFilters();
      drawChips();
      drawNav();
      drawRail();
      showChips();
    }
    S.more = !!data.more;
    S.offset = data.offset + (data.items || []).length;
    drawList(data.items || [], !data.offset);
    drawMeta();
    $('more').className = S.more && isNews(S.view) ? '' : 'hide';
  }).catch(function (reason) {
    if (reason !== 'auth') { toast('' + reason); }
  });
}

function drawList(items, reset) {
  var box = $('list');
  if (reset) { box.innerHTML = ''; }
  items.forEach(function (item) { box.appendChild(drawCard(item)); });
  if (!box.childNodes.length) { box.appendChild(drawEmpty()); }
  markClamped();
}

function drawEmpty() {
  var box = el('div', 'empty');
  if (S.q) {
    box.appendChild(el('b', null, 'Ничего не нашлось'));
    box.appendChild(el('div', null,
      'По запросу «' + S.q + '» в вашей ленте пусто. Ищется только то, что ' +
      'вам уже приходило.' + (filtering()
        ? ' И только в выбранных разделах — снимите плашки, чтобы искать по ' +
          'всей ленте.' : '')));
    return box;
  }
  if (S.view === 'saved') {
    box.appendChild(el('b', null, 'Закладок пока нет'));
    box.appendChild(el('div', null, 'Нажмите 🔖 под новостью — она окажется здесь.'));
    return box;
  }
  if (S.view === 'liked') {
    box.appendChild(el('b', null, 'Ничего не отмечено'));
    box.appendChild(el('div', null,
      'Нажмите 👍 под новостью: так бот поймёт, что вам интересно.'));
    return box;
  }
  if (filtering()) {
    box.appendChild(el('b', null, 'По вашим фильтрам пусто'));
    box.appendChild(el('div', null,
      'В выбранных разделах новостей пока нет. Снимите плашку или наберите ' +
      'другие разделы — кнопка «Фильтры» над лентой.'));
    return box;
  }
  box.appendChild(el('b', null, 'Здесь пока пусто'));
  box.appendChild(el('div', null, S.state && S.state.next
    ? 'Выпуск ещё не приходил. Ближайший — ' + S.state.next + '.'
    : 'Выпуск ещё не приходил.'));
  return box;
}

function drawCard(item) {
  var card = el('article', 'news' + (item.breaking ? ' hot' : ''));
  card.id = 'n' + item.hash;
  card.style.setProperty('--h', String(item.tone));

  var text = el('div', 'text');
  var line = el('div', 'line');
  /* срочное первым делом: рамка бросается в глаза, но словом надёжнее —
     на чёрно-белом экране и при дальтонизме цвет не читается вовсе */
  if (item.breaking) {
    line.appendChild(el('span', 'hot-tag', '⚡ Срочно'));
    line.appendChild(el('span', null, '·'));
  }
  line.appendChild(el('span', 'tag', item.emoji + ' ' + item.label));
  if (item.at) {
    line.appendChild(el('span', null, '·'));
    line.appendChild(el('time', null, item.at));
  }
  if (item.score) {
    line.appendChild(el('span', 'star', '⭐ ' + item.score.toFixed(1)));
  }
  text.appendChild(line);

  var head = el('h2');
  if (item.url) {
    var link = el('a', null, item.title);
    link.href = item.url;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    head.appendChild(link);
  } else {
    head.textContent = item.title;
  }
  text.appendChild(head);
  if (item.summary) { text.appendChild(drawSummary(item.summary)); }

  var foot = el('div', 'foot');
  foot.appendChild(actButton('🔖', 'save', item.saved, item));
  foot.appendChild(actButton('👍', 'up', item.verdict === 'up', item));
  foot.appendChild(actButton('👎', 'down', item.verdict === 'down', item));
  var src = el('span', 'src');
  if (item.url) {
    var out = el('a', null, item.source);
    out.href = item.url;
    out.target = '_blank';
    out.rel = 'noopener noreferrer';
    src.appendChild(out);
    src.appendChild(el('span', null, '↗'));
  } else {
    src.appendChild(el('span', null, item.source));
  }
  foot.appendChild(src);
  text.appendChild(foot);

  card.appendChild(text);
  card.appendChild(el('div', 'cover', item.emoji));
  return card;
}

function drawSummary(text) {
  var sum = el('p', 'sum', text);
  sum.onclick = function () {
    if (sum.className.indexOf('more') < 0) { return; }   /* текст и так весь тут */
    var open = sum.className.indexOf('open') < 0;
    sum.className = open ? 'sum more open' : 'sum more';
    sum.title = open ? 'Свернуть' : 'Показать целиком';
  };
  return sum;
}

/* Обрезал ли CSS текст новости, знает только браузер: три строки — это разное
   число знаков на телефоне и на широком экране. Поэтому меряем уже вставленные
   в страницу карточки и зовём развернуть только те, где текст не поместился. */
function markClamped() {
  var sums = document.querySelectorAll('#list .sum');
  Array.prototype.forEach.call(sums, function (sum) {
    if (sum.className.indexOf('open') >= 0) { return; }
    if (sum.scrollHeight > sum.clientHeight + 1) {
      sum.className = 'sum more';
      sum.title = 'Показать целиком';
    } else {
      sum.className = 'sum';
      sum.removeAttribute('title');
    }
  });
}

function actButton(icon, kind, on, item) {
  var button = el('button', 'act' + (on ? ' on' : ''), icon);
  button.type = 'button';
  button.setAttribute('data-act', kind + ':' + item.hash);
  button.onclick = function () { react('fb:' + kind + ':' + item.hash); };
  return button;
}

/* ------------------------------------------------------- правая колонка */
function drawRail() {
  drawDigestBox();
  drawSourcesBox();
  drawTopicsBox();
}

function drawDigestBox() {
  var box = $('boxDigest'), st = S.state;
  box.innerHTML = '';
  if (!st) { return; }
  var who = el('div', 'who');
  who.appendChild(el('span', null, '📡'));
  who.appendChild(el('span', null, 'Дайджест'));
  box.appendChild(who);

  var facts = el('div', 'facts');
  facts.appendChild(el('div', null,
    st.feeds + ' ' + plural(st.feeds, 'источник', 'источника', 'источников') +
    ' · ' + st.sections.length + ' ' +
    plural(st.sections.length, 'раздел', 'раздела', 'разделов')));
  facts.appendChild(el('div', null, 'Выпуск ' + st.next));
  facts.appendChild(el('div', null, 'По ' + st.each + ' ' +
    plural(st.each, 'новости', 'новости', 'новостей') + ' на раздел'));
  if (st.paused) { facts.appendChild(el('div', 'warn', '⏸ рассылка на паузе')); }
  if (!st.owner) {
    facts.appendChild(el('div', 'warn', 'TELEGRAM_CHAT_ID не задан'));
  }
  box.appendChild(facts);

  var pair = el('div', 'pair');
  var upd = el('button', 'ghost', '↻ Обновить');
  upd.type = 'button';
  upd.onclick = function () { loadNews(true); refresh(true); };
  var out = el('button', 'ghost', '⏻ Выйти');
  out.type = 'button';
  out.onclick = logout;
  pair.appendChild(upd);
  pair.appendChild(out);
  box.appendChild(pair);
}

function drawSourcesBox() {
  var box = $('boxSources');
  var list = (S.side && S.side.sources) || [];
  box.className = 'box' + (list.length ? '' : ' hide');
  box.innerHTML = '';
  if (!list.length) { return; }
  box.appendChild(el('h3', null, 'Популярные источники'));
  var rows = el('div', 'rows');
  list.forEach(function (source) {
    var button = el('button');
    button.type = 'button';
    button.title = source.count + ' ' +
      plural(source.count, 'новость', 'новости', 'новостей') + ' в вашей ленте';
    var dot = el('span', 'dot', source.name.slice(0, 1).toUpperCase());
    dot.style.setProperty('--h', String(hueOf(source.name)));
    button.appendChild(dot);
    button.appendChild(el('span', 'nm', source.name));
    button.appendChild(el('span', 'rate', source.rating
      ? '⭐ ' + source.rating.toFixed(1) : source.count));
    button.onclick = function () { find(source.id); };
    rows.appendChild(button);
  });
  box.appendChild(rows);
}

function drawTopicsBox() {
  var box = $('boxTopics');
  var list = (S.side && S.side.topics) || [];
  box.className = 'box' + (list.length ? '' : ' hide');
  box.innerHTML = '';
  if (!list.length) { return; }
  box.appendChild(el('h3', null, 'Популярные темы'));
  var tags = el('div', 'tags');
  list.forEach(function (topic) {
    var button = el('button', null, topic.word);
    button.type = 'button';
    button.title = topic.count + ' ' +
      plural(topic.count, 'новость', 'новости', 'новостей');
    button.onclick = function () { find(topic.word); };
    tags.appendChild(button);
  });
  box.appendChild(tags);
}

/* ---------------------------------------------------------------- поиск */
function find(text) {
  S.q = text;
  $('q').value = text;
  $('clear').className = 'clear';
  go('news', '');
}

function search(event) {
  event.preventDefault();
  S.q = $('q').value.trim();
  go('news', S.section);
  return false;
}

/* Печатают быстрее, чем отвечает база, — ждём паузы в наборе. */
function typed() {
  $('clear').className = $('q').value ? 'clear' : 'clear hide';
  clearTimeout(S.typing);
  S.typing = setTimeout(function () {
    var value = $('q').value.trim();
    if (value === S.q) { return; }
    S.q = value;
    if (!isNews(S.view)) { S.view = 'news'; paint(); }
    loadNews(true);
  }, 350);
}

function clearSearch() {
  $('q').value = '';
  $('clear').className = 'clear hide';
  S.q = '';
  loadNews(true);
}

/* ------------------------------------------------------------ уведомления */
/* Одна рассылка — одна карточка: когда пришла, сколько в ней было новостей
   и пять самых важных ссылок. Всё остальное читатель смотрит в ленте. */
function drawAlerts() {
  var box = $('alerts');
  box.innerHTML = '';
  if (!S.alerts.length) {
    var empty = el('div', 'empty');
    empty.appendChild(el('b', null, 'Рассылок пока не было'));
    empty.appendChild(el('div', null, S.state && S.state.next
      ? 'Ближайший выпуск — ' + S.state.next + '.'
      : 'Здесь появится каждый ушедший выпуск.'));
    box.appendChild(empty);
    return;
  }
  S.alerts.forEach(function (mail) { box.appendChild(drawMail(mail)); });
}

function drawMail(mail) {
  var box = el('div', 'mail' + (mail.breaking ? ' hot' : ''));
  var head = el('h2');
  /* срочное приходит одной новостью и вне расписания — такое и не выпуск
     вовсе. Если же оно попало в общий выпуск, заголовок остаётся выпуском,
     а молния встаёт рядом плашкой */
  var alone = mail.breaking && mail.count === 1;
  head.appendChild(el('span', null, alone ? '⚡ Срочно' : '📬 Выпуск'));
  if (mail.breaking && !alone) {
    head.appendChild(el('span', 'hot-tag', '⚡ Срочно'));
  }
  head.appendChild(el('span', 'at', mail.when + ', ' + mail.time));
  box.appendChild(head);

  var facts = mail.count + ' ' +
      plural(mail.count, 'новость', 'новости', 'новостей');
  if (mail.sections) {
    facts += ' · ' + mail.sections + ' ' +
             plural(mail.sections, 'раздел', 'раздела', 'разделов');
  }
  box.appendChild(el('div', 'facts', facts));

  if (mail.links.length) {
    var list = el('ul');
    /* в одиночном срочном молния уже стоит в заголовке — в единственной
       строке под ним она только повторяется */
    mail.links.forEach(function (link) {
      list.appendChild(drawLink(link, !alone));
    });
    box.appendChild(list);
  }
  return box;
}

function drawLink(link, mark) {
  var row = el('li');
  row.appendChild(el('span', 'ico', link.emoji));
  var text = el('span');
  if (link.breaking && mark) {
    text.appendChild(el('span', 'bolt', '⚡ Срочно '));
  }
  if (link.url) {
    var out = el('a', null, link.title);
    out.href = link.url;
    out.target = '_blank';
    out.rel = 'noopener noreferrer';
    text.appendChild(out);
  } else {
    text.appendChild(el('span', null, link.title));
  }
  text.appendChild(el('span', 'from', ' — ' + link.source));
  if (link.score) {
    text.appendChild(el('span', 'star', ' ⭐ ' + link.score.toFixed(1)));
  }
  row.appendChild(text);
  return row;
}

/* ------------------------------------------------------- панель настроек */
/* Только чтение: подписчики и настройки приложения. Меняются они на самом
   VPS, в ~/.newsdigest/env, — страница про них просто рассказывает. */
function drawPanel() {
  var box = $('panel'), data = S.tools;
  box.innerHTML = '';
  if (!data) {
    box.appendChild(el('div', 'empty', 'Читаю настройки…'));
    return;
  }

  var folk = el('div', 'box');
  folk.appendChild(el('h3', null, 'Подписчики (' + data.readers.length + ')'));
  var list = el('div', 'folk');
  data.readers.forEach(function (man) { list.appendChild(drawReader(man)); });
  folk.appendChild(list);
  box.appendChild(folk);

  var opts = el('div', 'box');
  opts.appendChild(el('h3', null, 'Настройки приложения'));
  opts.appendChild(el('div', 'facts', 'Часовой пояс: ' + data.tz));
  var rows = el('div', 'opts');
  data.settings.forEach(function (opt) { rows.appendChild(drawOption(opt)); });
  opts.appendChild(rows);
  var out = el('button', 'ghost wide', '⏻ Выйти');
  out.type = 'button';
  out.onclick = logout;
  opts.appendChild(out);
  box.appendChild(opts);
}

var ROLES = { owner: '👑', member: '•', pending: '⏳' };

function drawReader(man) {
  var box = el('div');
  var line = el('div', 'man');
  line.appendChild(el('span', null, ROLES[man.role] || '•'));
  line.appendChild(el('span', null, man.title));
  line.appendChild(el('span', 'id', man.chat));
  if (man.role === 'pending') { line.appendChild(el('span', 'about', 'ждёт')); }
  if (man.paused) { line.appendChild(el('span', 'warn', '⏸ пауза')); }
  box.appendChild(line);
  box.appendChild(el('div', 'about', man.own));
  return box;
}

function drawOption(opt) {
  var box = el('div', 'opt');
  box.appendChild(el('span', 'nm', opt.name));
  box.appendChild(el('span', 'val', opt.value + (opt.own ? ' ✏️' : '')));
  box.appendChild(el('span', 'about', opt.about));
  return box;
}

/* --------------------------------------------------------------- действия */
function applyAlerts(data) {
  if (data.state) { S.state = data.state; }
  S.alerts = data.alerts || [];
  /* непрочитанное — это рассылки, пришедшие после последней увиденной.
     При заходе считать нечего: всё, что уже лежит, читатель видел раньше */
  S.unread = 0;
  S.hot = false;
  if (S.hello) {
    S.hello = false;
    seen();
  } else {
    for (var i = 0; i < S.alerts.length && S.alerts[i].id !== S.seen; i++) {
      S.unread++;
      if (S.alerts[i].breaking) { S.hot = true; }
    }
  }
  if (S.view === 'alerts') { seen(); drawAlerts(); }
  drawMeta();
  drawTabs();
  drawNav();
  drawDigestBox();
}

/* «Всё это я видел»: следующая рассылка снова зажжёт значок на колокольчике. */
function seen() {
  S.seen = S.alerts.length ? S.alerts[0].id : S.seen;
  S.unread = 0;
  S.hot = false;
}

/* Отметку о нажатии ставим в карточке ленты — там, где кнопки и живут. */
function repaint(press) {
  if (!press.hash) { return; }
  var acts = document.querySelectorAll('[data-act]');
  Array.prototype.forEach.call(acts, function (node) {
    var parts = node.getAttribute('data-act').split(':');
    if (parts[1] !== press.hash) { return; }
    node.className = 'act' + (press.pressed && press.pressed[parts[0]] ? ' on' : '');
  });
  /* из закладок и избранного карточка уходит сразу: читатель только что
     снял отметку, ради которой она тут и была */
  var card = $('n' + press.hash);
  var gone = (S.view === 'saved' && press.pressed && !press.pressed.save)
          || (S.view === 'liked' && press.pressed && !press.pressed.up);
  if (card && gone) {
    card.remove();
    if (!$('list').childNodes.length) { $('list').appendChild(drawEmpty()); }
  }
}

function react(data) {
  return call('/api/react', { data: data }).then(function (result) {
    repaint(result);
    if (result.toast) { toast(result.toast); }
  }).catch(function (reason) { if (reason !== 'auth') { toast('' + reason); } });
}

function loadTools() {
  return call('/api/tools').then(function (data) {
    S.tools = data;
    if (data.state) { S.state = data.state; }
    if (S.view === 'tools') { drawPanel(); }
    drawMeta();
    drawDigestBox();
  }).catch(function (reason) {
    if (reason !== 'auth') { toast('' + reason); }
  });
}

function refresh(manual) {
  var before = S.alerts.length ? S.alerts[0].id : '';
  return call('/api/alerts').then(function (data) {
    applyAlerts(data);
    /* пришёл выпуск — значит в ленте появились новости, перечитываем её */
    var now = S.alerts.length ? S.alerts[0].id : '';
    if (now !== before && isNews(S.view)) { loadNews(true); }
    if (manual) { toast('Обновлено'); }
  }).catch(function (reason) {
    if (manual && reason !== 'auth') { toast('Не отвечает: ' + reason); }
  });
}

/* ------------------------------------------------------ опрос и запуск */
function stopTimer() { if (S.timer) { clearInterval(S.timer); S.timer = null; } }

function startTimer() {
  stopTimer();
  S.timer = setInterval(function () {
    if (!document.hidden) { refresh(false); }
  }, 8000);
}

function start() {
  if (S.started) { return; }
  S.started = true;
  S.hello = true;
  refresh(false);
  loadNews(true);
  paint();
  startTimer();
}

document.addEventListener('visibilitychange', function () {
  if (!document.hidden && S.started) { refresh(false); }
});

document.addEventListener('keydown', function (event) {
  if (event.key === 'Escape') { closeFilters(); }
});

/* повернули телефон, растянули окно — в три строки помещается уже другое */
window.addEventListener('resize', markClamped);

call('/api/alerts').then(function (data) {
  $('login').className = '';
  $('app').className = 'on';
  S.started = true;
  applyAlerts(data);
  loadNews(true);
  paint();
  startTimer();
}).catch(function () { /* 401 уже показал форму входа */ });
</script>
</body>
</html>
"""

# -*- coding: utf-8 -*-
"""Сама страница: один файл, без внешних библиотек и шрифтов.

Разметка, стили и скрипт лежат вместе нарочно — страница должна открываться
на VPS без сборки, CDN и второго порта. Логика тут только рисующая: что
показывать, решает `web.py`.

Страница устроена как новостной сайт: слева разделы, в центре лента карточек,
справа справка о выпуске, популярные источники и темы. Левое меню — это только
разделы: оно стоит карточкой, не прокручивается и всегда видно целиком, а если
разделов больше, чем влезает в экран, список сам переходит на более плотный
шаг. Всё служебное переехало в шапку: «Избранное» (звёздочка), «Уведомления»
(колокольчик) — это список рассылок: когда пришла, сколько было новостей и
пять главных ссылок, — и «Настройки» (человечек) — подписчики и значения
настроек, только для чтения.

Значок раздела в этом столбце нарисован линией, а не взят эмодзи (ICONS): у
эмодзи свой цвет, и выбранный пункт от этого переставал читаться — синими в
нём становились название и число, но не картинка. Линия берёт цвет строки и
одинаково выглядит в обеих темах. Значок есть у каждого раздела из PROFILES;
незнакомый — а разделы заводят на ходу — получает метку «прочее». Той же
линией нарисованы и значки навигации: кружки в шапке и нижняя панель на
телефоне. Эмодзи там несли каждый свой цвет, и панель пестрила, — теперь и
шапка, и панель, и столбец разделов выглядят одинаково.

Под разделами стоит переключатель темы: светлая и тёмная, две кнопки, и
нажатая подсвечена. Выбор лежит в браузере (`nd.theme`) и переживает закрытие
вкладки; пока выбора нет, страница идёт за системной настройкой и слушает её
дальше. Ставит тему крошечный скрипт в самой голове страницы — иначе тёмная
страница успевала бы мигнуть белым. На телефоне столбца разделов нет, и та же
кнопка стоит в шапке; на ней нарисовано, куда переключит.

Строка поиска в шапке занимает не всю ширину: ищут раз в сеанс, а место рядом
нужно постоянно. Позвать её можно с клавиатуры — Ctrl+K (на маке ⌘K) или `/`,
как в почте и редакторах; о первом написано прямо в строке, и подсказка
уступает место крестику «очистить», как только начали набирать.

Кнопка «Фильтры» над лентой закрепляет разделы: можно оставить один, можно
несколько («только наука, спорт и экономика») — и «Главное» покажет новости
только из них. Выбранное видно плашками над лентой, снимается нажатием на
плашку и переживает закрытие браузера: набор лежит в localStorage. Пока не
выбрано ничего, полосы плашек нет вовсе — второй список разделов рядом с
левым меню только мешал бы.

На телефоне левого меню нет, и разделы переехали в шапку строкой рубрик:
они видны сразу, прокручиваются вбок, выбранная подсвечена и сама
подтягивается к середине. Ряд под них освободил поиск — он свернулся до лупы
рядом с колокольчиком и разворачивается нажатием на неё. Полем ввода
пользуются раз в сеанс, разделами — постоянно, поэтому постоянный ряд достаётся
разделам. Свёрнутый поиск уносит с собой и запрос: строка, ушедшая вместе с
невидимым фильтром на ленте, хуже, чем её отсутствие.

Ни строки ввода, ни кнопок «собрать», ни истории запусков здесь нет: боту
командуют на самом VPS, а страница — читалка.

Страница открыта всем, а служебное на ней — только владельцу. Гость видит
новостной сайт: лента, разделы, поиск, популярные источники и темы. Ни
уведомлений о рассылках, ни подписчиков, ни настроек, ни справки о выпуске
справа, ни кнопок 👍/👎/🔖 под карточками у него нет — и не потому, что они
спрятаны стилями: `web.py` этих данных ему не отдаёт, а всякий POST, кроме
входа, ему закрыт. Владелец нажимает ключ в шапке, вводит пароль — и страница
становится прежней: рассылки, подписчики, настройки, отметки.

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
<!-- страница ставится на телефон значком и открывается без адресной строки -->
<link rel="manifest" href="/manifest.webmanifest">
<link rel="apple-touch-icon" href="/icon.svg">
<meta name="apple-mobile-web-app-title" content="Дайджест">
<!-- та же лента для чужой читалки: подписаться можно, не открывая страницу -->
<link rel="alternate" type="application/rss+xml" title="Дайджест" href="/rss">
<!-- Тема выбирается до первой отрисовки, иначе тёмная страница успевала бы
     мигнуть белым. Это единственный скрипт в голове: всё остальное ждёт
     конца разметки. -->
<script>
(function () {
  var pick = '';
  try { pick = localStorage.getItem('nd.theme') || ''; } catch (err) { pick = ''; }
  if (pick !== 'light' && pick !== 'dark') {
    pick = window.matchMedia
        && window.matchMedia('(prefers-color-scheme: dark)').matches
         ? 'dark' : 'light';
  }
  document.documentElement.setAttribute('data-theme', pick);
})();
</script>
<style>
/* Какая тема сейчас, написано в атрибуте data-theme у <html>: его ставит
   скрипт в голове страницы — по выбору читателя, а если выбора не было, по
   системной настройке. Поэтому тёмная палитра здесь одна (а не две: на
   prefers-color-scheme и на выбор руками), и меняется она без перезагрузки. */
:root {
  color-scheme: light;
  --bg: #f1f3f7; --card: #ffffff; --ink: #14161b; --dim: #6b7280;
  --line: #e5e7eb; --soft: #f3f5f9; --accent: #2f6fed; --accent-ink: #ffffff;
  --tint: #e8effd; --warn: #b45309; --star: #f5a524;
  --hot: #e5484d; --hot-tint: #fff1f1; --hot-ring: rgba(229, 72, 77, .16);
  --shadow: 0 1px 2px rgba(16, 24, 40, .06);
  --tone-l: 40%; --tone-s: 68%;
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --bg: #101216; --card: #1a1d23; --ink: #e8eaee; --dim: #98a0ac;
  --line: #272b33; --soft: #22262e; --accent: #5b8dff; --accent-ink: #0c0e12;
  --tint: #1c2740; --warn: #fbbf24; --star: #fbbf24;
  --hot: #ff6b6f; --hot-tint: #241417; --hot-ring: rgba(255, 107, 111, .22);
  --shadow: none;
  --tone-l: 68%; --tone-s: 62%;
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
  font-weight: 700; width: 244px; flex: none;
}
.brand span { font-size: 22px; }
.brand { cursor: pointer; user-select: none; }
.brand:focus-visible { outline: 2px solid var(--dim); outline-offset: 4px;
                       border-radius: 8px; }
/* Строка поиска не тянется на всю шапку: ищут раз в сеанс, а место рядом с
   разделами и значками нужно постоянно. Ширины хватает на запрос из трёх-
   четырёх слов, а подсказка справа говорит, чем строку позвать с клавиатуры,
   не целясь мышью. */
.search { flex: 0 1 380px; position: relative; min-width: 0; }
.search input {
  background: var(--soft); border-color: transparent;
  padding-left: 38px; padding-right: 66px; border-radius: 12px;
}
.search .lens {
  position: absolute; left: 13px; top: 50%; transform: translateY(-50%);
  color: var(--dim); pointer-events: none; display: flex;
}
.search .lens svg { width: 17px; height: 17px; }
.search .kbd {
  position: absolute; right: 9px; top: 50%; transform: translateY(-50%);
  font-size: 11.5px; font-weight: 600; line-height: 1.6;
  color: var(--dim); pointer-events: none;
  border: 1px solid var(--line); border-radius: 6px; padding: 1px 6px;
  background: var(--card); white-space: nowrap;
}
.search .clear {
  position: absolute; right: 8px; top: 50%; transform: translateY(-50%);
  background: none; border: 0; color: var(--dim); padding: 6px 8px;
  border-radius: 8px;
}
/* Рубрики в шапке — телефонная замена левому меню: на узком экране места на
   список разделов нет, а строка поиска занимала целый ряд ради того, чем
   пользуются раз в сеанс. Поиск ушёл под лупу, ряд достался рубрикам: они
   видны сразу, прокручиваются вбок, выбранная подсвечена, а правый край
   растушёван — видно, что за ним ещё есть. На широком экране разделы стоят
   слева, и ни строка рубрик, ни лупа не нужны. */
.rubrics {
  display: none; gap: 8px; overflow-x: auto; scrollbar-width: none;
  max-width: 1460px; margin: 0 auto; padding: 10px 0 2px;
  -webkit-mask-image: linear-gradient(to right, #000 92%, transparent);
  mask-image: linear-gradient(to right, #000 92%, transparent);
}
.rubrics::-webkit-scrollbar { display: none; }
.rubrics button {
  flex: none; background: var(--soft); border: 1px solid transparent;
  color: var(--ink); border-radius: 999px; padding: 6px 13px; font-size: 13.5px;
  white-space: nowrap;
}
.rubrics button.on {
  background: var(--accent); border-color: var(--accent);
  color: var(--accent-ink); font-weight: 600;
}
#find { display: none; }
.tools { display: flex; gap: 8px; margin-left: auto; }
.icon {
  position: relative; width: 42px; height: 42px; border-radius: 50%;
  background: var(--card); border: 1px solid var(--line); font-size: 17px;
  display: flex; align-items: center; justify-content: center;
}
.icon.on { border-color: var(--accent); color: var(--accent); }
.icon svg { width: 18px; height: 18px; }
/* Тема на широком экране переключается в столбце разделов; в шапке кнопка
   нужна только там, где столбца нет, — на телефоне. */
#theme { display: none; }
.icon .badge {
  position: absolute; top: -2px; right: -2px; min-width: 18px; height: 18px;
  padding: 0 5px; border-radius: 9px; background: #e5484d; color: #fff;
  font-size: 11px; line-height: 18px; font-weight: 700;
}

/* --------------------------------------------------------------- каркас */
.shell {
  display: grid; grid-template-columns: 244px minmax(0, 1fr) 320px; gap: 20px;
  max-width: 1460px; margin: 0 auto; padding: 20px; align-items: start;
}
.side, .rail { position: sticky; top: 84px; }
/* Меню разделов не прокручивается: список целиком стоит перед глазами, а если
   разделов больше, чем влезает, страница сама переходит на более плотный шаг
   (см. fitNav) — вместо полосы прокрутки, из-под которой раньше выглядывали
   служебные кнопки. */
.side {
  --nav-gap: 2px; --item-pad: 9px 10px; --item-font: 14.5px; --item-ico: 17px;
  --item-round: 12px; --foot-pad: 16px;
  display: flex; flex-direction: column; overflow: hidden;
  max-height: calc(100vh - 104px);
  background: var(--card); border: 1px solid var(--line); border-radius: 18px;
  padding: 12px 10px; box-shadow: var(--shadow);
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
/* Тема — под разделами, а не в настройках: это про то, как читателю смотреть,
   а не про службу бота, и владельцем для этого быть не нужно. Две кнопки, а
   не одна с переключением: видно не только куда нажать, но и что сейчас. */
.themes {
  display: flex; justify-content: center; gap: 8px; flex: none;
  border-top: 1px solid var(--line);
  margin-top: var(--foot-pad); padding-top: var(--foot-pad);
}
.tgl {
  width: 34px; height: 34px; border-radius: 50%; flex: none;
  border: 1px solid var(--line); background: var(--bg); color: var(--dim);
  display: flex; align-items: center; justify-content: center;
}
.tgl svg { width: 17px; height: 17px; }
.tgl:hover { color: var(--ink); }
.tgl.on {
  background: var(--tint); border-color: var(--accent); color: var(--accent);
}
.side.d3 .tgl, .side.d4 .tgl { width: 28px; height: 28px; }
.side.d3 .tgl svg, .side.d4 .tgl svg { width: 15px; height: 15px; }
/* Крайний случай: разделов столько, что не спасает и самый плотный шаг.
   Прокрутка тут — меньшее зло, чем разделы, срезанные краем экрана. */
.side.roomy nav { overflow-y: auto; }
.item {
  display: flex; align-items: center; gap: 10px; padding: var(--item-pad);
  border-radius: var(--item-round); border: 0; background: none; width: 100%;
  text-align: left; font-size: var(--item-font); font-weight: 500;
}
.item:hover { background: var(--soft); }
.item.on { background: var(--tint); color: var(--accent); font-weight: 600; }
.item .ico {
  font-size: var(--item-ico); width: 22px; flex: none;
  display: flex; align-items: center; justify-content: center;
}
.item .ico svg { width: 1.3em; height: 1.3em; }
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
.pick .ico {
  font-size: 17px; width: 22px; flex: none;
  display: flex; align-items: center; justify-content: center;
}
.pick .ico svg { width: 1.3em; height: 1.3em; }
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
/* оговорка фактчека: «препринт, без рецензирования», «один источник».
   Она про то, как читать заголовок, поэтому стоит под текстом и не
   сворачивается вместе с ним */
.news .caveat {
  margin: 8px 0 0; font-size: 13px; line-height: 1.4;
  color: var(--dim); overflow-wrap: anywhere;
}
/* «Ранее по теме»: чем эта новость продолжает уже прочитанное. Стоит под
   текстом и до подписи — это часть новости, а не служебная строка. Вертикальная
   черта слева читается как «то же самое, но раньше»: связь видно и без цвета */
.news .back {
  margin: 10px 0 0; padding-left: 11px; border-left: 2px solid var(--line);
}
.news .back .cap {
  display: block; font-size: 11.5px; letter-spacing: .04em; text-transform: uppercase;
  color: var(--dim); margin-bottom: 3px;
}
.news .back a, .news .back span.step {
  display: block; font-size: 13px; line-height: 1.4; color: var(--dim);
  margin-top: 2px; overflow-wrap: anywhere;
}
.news .back a:hover { color: var(--accent); text-decoration: none; }
.news .back time { font-variant-numeric: tabular-nums; opacity: .75; }
.news .foot { display: flex; align-items: center; gap: 6px; margin-top: 14px; }
.news .src { margin-left: auto; font-size: 13px; display: flex; gap: 5px; }
.act {
  background: none; border: 0; padding: 5px 7px; border-radius: 9px;
  font-size: 15px; line-height: 1; opacity: .62;
}
.act:hover { background: var(--soft); opacity: 1; }
.act.on { opacity: 1; background: var(--tint); }
.cover {
  width: 116px; height: 96px; border-radius: 12px; flex: none;
  display: flex; align-items: center; justify-content: center; font-size: 34px;
  background: linear-gradient(140deg, hsl(var(--h) 62% 62%),
                                      hsl(var(--h) 58% 38%));
}
/* Выбранная с клавиатуры карточка. Обводка, а не заливка: у срочного своя
   рамка и свой фон, и подсветка выбора не должна с ними спорить */
.news.sel { box-shadow: 0 0 0 2px var(--accent); }
/* Черта между днями. Лента идёт от свежего к старому, и без неё две недели
   выпусков читаются как один бесконечный день: «вчера, 23:27» у каждой
   карточки в отдельности время называет, а порядок дней — нет */
.daybar {
  display: flex; align-items: center; gap: 10px;
  margin: 22px 2px 12px; color: var(--dim);
  font-size: 12.5px; font-weight: 700; letter-spacing: .06em;
  text-transform: uppercase;
}
.daybar::after {
  content: ''; flex: 1; height: 1px; background: var(--line);
}
#list > .daybar:first-child { margin-top: 2px; }
/* Граница прочитанного: всё выше пришло с прошлого захода. Считается в
   браузере — серверу знать, когда читатель заходил на страницу, незачем */
.seenbar {
  display: flex; align-items: center; gap: 10px;
  margin: 20px 2px 14px; color: var(--dim); font-size: 12.5px;
}
.seenbar::before, .seenbar::after {
  content: ''; flex: 1; height: 1px; background: var(--line);
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
/* Гостю ходить некуда, кроме ленты: панель из одной кнопки — не навигация,
   а полоса поперёк экрана. Разделы у него в шапке строкой рубрик. */
body.guest .tabs { display: none; }
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
.tabs button .ico { display: flex; align-items: center; line-height: 1; }
.tabs button .ico svg { width: 21px; height: 21px; }
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
  /* Строка поиска свёрнута до лупы в шапке и разворачивается по нажатию на
     неё; пока ищут — рубрики уступают строке свой ряд, чтобы шапка не
     съедала пол-экрана. */
  #find { display: flex; }
  .search { display: none; }
  header.finding .search {
    display: block; order: 3; flex-basis: 100%; margin-top: 8px;
  }
  /* На телефоне подсказка про Ctrl+K врёт: клавиатуры с Ctrl там нет, а место
     в строке она занимает. Освободившееся место отдаём самому запросу. */
  .search .kbd { display: none; }
  .search input { padding-right: 40px; }
  .rubrics { display: flex; }
  header.finding .rubrics { display: none; }
  .shell { grid-template-columns: minmax(0, 1fr); padding: 14px 12px 88px;
           gap: 0; }
  .side { display: none; }
  /* без нижней панели незачем и место под неё */
  body.guest .shell { padding-bottom: 20px; }
  /* «Избранное» на телефоне живёт в нижней панели — в шапке звезда только
     теснила бы поиск. Место освободилось под тему: столбца разделов, где она
     стоит на широком экране, здесь нет. */
  #star { display: none; }
  #theme { display: flex; }
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
    <p>Вход для владельца. Читать новости можно и без него.</p>
    <input type="password" id="pass" placeholder="Пароль"
           autocomplete="current-password">
    <button class="primary" type="submit">Войти</button>
    <button class="ghost wide" type="button" onclick="hideLogin()">К новостям</button>
    <div class="err" id="loginErr"></div>
  </form>
</div>

<div id="app">
  <header id="hdr">
    <div class="top">
      <div class="brand" id="brand" role="button" tabindex="0"
           title="На главную" onclick="home()"
           onkeydown="if (event.key === 'Enter' || event.key === ' ')
                      { event.preventDefault(); home(); }">
        <span>📡</span> Дайджест
      </div>
      <form class="search" onsubmit="return search(event)">
        <span class="lens" id="lens"></span>
        <input type="text" id="q" autocomplete="off" spellcheck="false"
               placeholder="Поиск по новостям и темам"
               oninput="typed()"
               onkeydown="if (event.key === 'Escape') { hideSearch(); }">
        <span class="kbd" id="kbd"></span>
        <button type="button" class="clear hide" id="clear"
                onclick="clearSearch()" title="Очистить">✕</button>
      </form>
      <div class="tools">
        <button class="icon" id="find" onclick="toggleSearch()"
                title="Поиск" aria-label="Поиск"></button>
        <button class="icon" id="star" onclick="go('liked')"
                title="Избранное"></button>
        <button class="icon" id="bell" onclick="go('alerts')"
                title="Уведомления"></button>
        <button class="icon" id="theme" onclick="flipTheme()"
                aria-label="Сменить тему"></button>
        <button class="icon" id="who" onclick="whoTap()"
                title="Настройки"></button>
      </div>
    </div>
    <nav class="rubrics" id="rubrics"></nav>
  </header>

  <div class="shell">
    <aside class="side">
      <nav id="nav"></nav>
      <div class="themes" role="group" aria-label="Тема оформления">
        <button type="button" class="tgl" id="lightTheme"
                onclick="setTheme('light')" title="Светлая тема"
                aria-label="Светлая тема"></button>
        <button type="button" class="tgl" id="darkTheme"
                onclick="setTheme('dark')" title="Тёмная тема"
                aria-label="Тёмная тема"></button>
      </div>
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
      <div class="box" id="boxStories"></div>
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
  view: 'news', section: '', q: '', offset: 0, more: false, finding: false,
  seen: '', last: '', unread: 0, hot: false, hello: true, started: false,
  admin: false,
  /* показ ленты: последний нарисованный день, отметка прошлого захода и
     сколько новостей оказалось выше неё, выбранная с клавиатуры карточка */
  day: '', mark: '', fresh: 0, drawn: false, cursor: -1,
  timer: null, typing: null,
  state: null, alerts: [], tools: null, menu: [], side: null,
  filters: [], pick: []
};

/* Владелец ли смотрит, решает не страница, а ответ сервера: свои экраны она
   рисует по `state.admin`, а данные для них всё равно приходят только по
   паролю. Соврать себе `S.admin = true` в консоли можно — увидеть от этого
   нечего: сервер отдаст 401. */
var TABS = [
  { id: 'news',   icon: 'home',     name: 'Главная' },
  { id: 'saved',  icon: 'bookmark', name: 'Сохранённые', admin: true },
  { id: 'liked',  icon: 'star',     name: 'Избранное', admin: true },
  { id: 'alerts', icon: 'bell',     name: 'Уведомления', admin: true },
  { id: 'tools',  icon: 'gear',     name: 'Настройки', admin: true }
];

/* Гостю доступна одна лента: остальное — про рассылки и настройки бота. */
function allowed(view) { return S.admin || view === 'news'; }

var NAMES = { news: 'Главное', saved: 'Сохранённые', liked: 'Избранное',
              alerts: 'Уведомления', tools: 'Настройки' };

/* ------------------------------------------------------------- значки */
/* Значки разделов рисуем сами, одной линией на общей сетке 24x24. Эмодзи
   тянут за собой чужой цвет: столбец разделов от них пестрил, а выбранный
   пункт переставал читаться — синими в нём становились название и число, но
   не картинка. Линия же берёт цвет строки: серую у обычной, синюю у выбранной,
   и одинаково выглядит в светлой теме и в тёмной.

   Здесь только внутренности <svg>: рамку добавляет svgIcon(). Незнакомый
   раздел (их заводят командой на месте) получает метку — ту же, что стоит у
   раздела «Прочее». */
var ICON_HEAD = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
              + 'stroke-width="1.6" stroke-linecap="round" '
              + 'stroke-linejoin="round" aria-hidden="true" focusable="false">';

var ICONS = {
  '': '<path d="M3.6 10.7 12 4.2l8.4 6.5"/>'
    + '<path d="M5.8 9.8V19a1.2 1.2 0 0 0 1.2 1.2h10a1.2 1.2 0 0 0 1.2-1.2V9.8"/>'
    + '<path d="M9.8 20.2v-5.4h4.4v5.4"/>',
  politics: '<path d="M12 3.4 20.6 8H3.4z"/>'
    + '<path d="M6.2 8.6v8.2M10.1 8.6v8.2M13.9 8.6v8.2M17.8 8.6v8.2"/>'
    + '<path d="M4.6 16.8h14.8"/><path d="M3.4 20.4h17.2"/>',
  incidents: '<path d="M7.2 20.4h9.6"/>'
    + '<path d="M8.4 20.4v-6.2a3.6 3.6 0 0 1 7.2 0v6.2"/>'
    + '<path d="M12 3.6v2.3"/><path d="M6.6 6.2 8.2 7.8"/>'
    + '<path d="M17.4 6.2 15.8 7.8"/><path d="M4.2 12.4h1.9M17.9 12.4h1.9"/>',
  science: '<path d="M9.6 3.6v5.6L5.2 17a2.3 2.3 0 0 0 2 3.4h9.6a2.3 2.3 0 0 0 '
    + '2-3.4l-4.4-7.8V3.6"/><path d="M8.4 3.6h7.2"/><path d="M7.3 14.6h9.4"/>',
  cybersec: '<path d="M12 3.4 19.2 6v6.1c0 4.1-2.9 7.1-7.2 8.5-4.3-1.4-7.2-4.4'
    + '-7.2-8.5V6z"/><path d="m9.4 12.2 1.9 1.9 3.5-3.8"/>',
  cinema: '<rect x="3.4" y="7.4" width="17.2" height="12.4" rx="2.2"/>'
    + '<path d="M3.4 11.8h17.2"/>'
    + '<path d="M8.4 7.4 6.2 11.8M13.6 7.4l-2.2 4.4M18.8 7.4l-2.2 4.4"/>',
  ai: '<rect x="4.4" y="8" width="15.2" height="11.6" rx="3.2"/>'
    + '<path d="M12 4.6V8"/><circle cx="12" cy="3.4" r="1.1"/>'
    + '<path d="M9.2 12.4v1.6M14.8 12.4v1.6"/><path d="M9.8 16.6h4.4"/>'
    + '<path d="M4.4 12.6H2.9M19.6 12.6h1.5"/>',
  dev: '<path d="M8.8 8.2 4.4 12l4.4 3.8"/><path d="M15.2 8.2 19.6 12l-4.4 3.8"/>'
    + '<path d="M13.4 5.2 10.6 18.8"/>',
  hardware: '<rect x="7.4" y="7.4" width="9.2" height="9.2" rx="2"/>'
    + '<rect x="10.6" y="10.6" width="2.8" height="2.8" rx=".8"/>'
    + '<path d="M10 4.6v2.8M14 4.6v2.8M10 16.6v2.8M14 16.6v2.8"/>'
    + '<path d="M4.6 10h2.8M4.6 14h2.8M16.6 10h2.8M16.6 14h2.8"/>',
  robots: '<path d="M4 20.4h7.6"/><path d="M7.8 20.4v-4.6"/>'
    + '<circle cx="7.8" cy="14.2" r="1.6"/><path d="M9 13.1 12.9 9.2"/>'
    + '<circle cx="14.1" cy="8" r="1.6"/><path d="M15.3 6.9 17.4 4.8"/>'
    + '<path d="M16.2 3.6 20.4 7.8"/>',
  space: '<path d="M12 3.4c2.8 2.4 4.3 5.6 4.3 9.1l-1.7 3.3H9.4l-1.7-3.3c0-3.5 '
    + '1.5-6.7 4.3-9.1z"/><circle cx="12" cy="10.2" r="1.9"/>'
    + '<path d="M9.4 15.8 6.8 18.4l.6-4.2"/><path d="m14.6 15.8 2.6 2.6-.6-4.2"/>'
    + '<path d="M10.6 18.6 12 21.2l1.4-2.6"/>',
  climate: '<circle cx="12" cy="12" r="8.4"/><path d="M3.7 12h16.6"/>'
    + '<path d="M12 3.6c2.2 2.3 3.4 5.2 3.4 8.4s-1.2 6.1-3.4 8.4c-2.2-2.3-3.4'
    + '-5.2-3.4-8.4S9.8 5.9 12 3.6z"/>',
  medicine: '<path d="M6.2 3.6v4.9a4.1 4.1 0 0 0 8.2 0V3.6"/>'
    + '<path d="M4.9 3.6h2.6M13.1 3.6h2.6"/>'
    + '<path d="M10.3 12.6v2.3a4.2 4.2 0 0 0 8.4 0v-1.5"/>'
    + '<circle cx="18.7" cy="11.3" r="2"/>',
  health: '<path d="M12 20.2C9.5 18.5 4.2 15 4.2 10.6a3.9 3.9 0 0 1 7.8-1.5 3.9 '
    + '3.9 0 0 1 7.8 1.5c0 4.4-5.3 7.9-7.8 9.6z"/>'
    + '<path d="M6.2 12.2h2.9l1.4-2.4 1.9 4.2 1.3-1.8h4.1"/>',
  economy: '<circle cx="12" cy="12" r="8.4"/>'
    + '<path d="M14.8 9.4c-.5-1-1.6-1.7-2.9-1.7-1.7 0-2.9 1-2.9 2.3 0 3 5.9 1.6 '
    + '5.9 4.6 0 1.4-1.3 2.4-3 2.4-1.5 0-2.6-.7-3.1-1.8"/>'
    + '<path d="M12 6.1v1.6M12 16.6v1.6"/>',
  sports: '<circle cx="12" cy="12" r="8.4"/>'
    + '<path d="M12 3.8 8.6 8.5l1.3 4.3h4.2l1.3-4.3z"/>'
    + '<path d="M4.1 9.8 8.6 8.5M19.9 9.8l-4.5-1.3M7 18.7l2.9-5.9M17 18.7l-2.9-5.9"/>',
  games: '<path d="M8.8 8.6h6.4a5.2 5.2 0 0 1 5.1 4.3l.5 2.7a2.5 2.5 0 0 1-4.6 '
    + '1.7l-1.2-2H9l-1.2 2a2.5 2.5 0 0 1-4.6-1.7l.5-2.7a5.2 5.2 0 0 1 5.1-4.3z"/>'
    + '<path d="M8.6 11.7v2.6M7.3 13h2.6"/>'
    + '<path d="M15.4 12.2h.01M17.2 14h.01"/>',
  crypto: '<circle cx="12" cy="12" r="8.4"/><path d="M9.6 7.8v8.4"/>'
    + '<path d="M9.6 12h3.4a2.1 2.1 0 0 1 0 4.2H9.6"/>'
    + '<path d="M9.6 7.8h3a2.1 2.1 0 0 1 0 4.2"/>'
    + '<path d="M11.4 6.1v1.7M13.4 6.1v1.7M11.4 16.2v1.7M13.4 16.2v1.7"/>',
  custom: '<path d="M12 20.6s6.2-5.4 6.2-9.8a6.2 6.2 0 1 0-12.4 0c0 4.4 6.2 9.8 '
    + '6.2 9.8z"/><circle cx="12" cy="10.6" r="2.3"/>'
};

var LENS_ICON = '<circle cx="11" cy="11" r="6.4"/><path d="M20.2 20.2 15.7 15.7"/>';

var SUN_ICON = '<circle cx="12" cy="12" r="4.2"/>'
             + '<path d="M12 2.8v2.3M12 18.9v2.3M2.8 12h2.3M18.9 12h2.3"/>'
             + '<path d="m5.5 5.5 1.6 1.6M16.9 16.9l1.6 1.6M18.5 5.5l-1.6 1.6'
             + 'M7.1 16.9l-1.6 1.6"/>';

var MOON_ICON = '<path d="M20.4 13.6A8.6 8.6 0 0 1 10.4 3.6a8.6 8.6 0 1 0 10 10z"/>';

/* Значки навигации — нижняя панель на телефоне и кружки в шапке. Рисуем их той
   же линией на той же сетке, что и значки разделов: эмодзи здесь несли каждый
   свой цвет, и панель пестрила, а у выбранной кнопки синим становилось только
   слово под картинкой. Линия берёт цвет кнопки — серый у обычной, синий у
   выбранной, — и панель наконец читается как одно целое. */
var UI_ICONS = {
  home: ICONS[''],
  bookmark: '<path d="M6.8 3.8h10.4a1.2 1.2 0 0 1 1.2 1.2v15.2L12 16.6l-6.4 3.6'
    + 'V5a1.2 1.2 0 0 1 1.2-1.2z"/>',
  star: '<path d="m12 3.6 2.6 5.4 5.9.9-4.3 4.1 1 5.9L12 17.1l-5.2 2.8 1-5.9'
    + '-4.3-4.1 5.9-.9z"/>',
  bell: '<path d="M12 3.4a5.7 5.7 0 0 0-5.7 5.7c0 4.2-1.5 5.7-1.5 5.7h14.4'
    + 's-1.5-1.5-1.5-5.7A5.7 5.7 0 0 0 12 3.4z"/>'
    + '<path d="M10.2 18a2.1 2.1 0 0 0 3.6 0"/>',
  gear: '<path d="M4.2 7.4h9M17.6 7.4h2.2M4.2 16.6h2.4M11 16.6h8.8"/>'
    + '<circle cx="15.3" cy="7.4" r="2.3"/><circle cx="8.7" cy="16.6" r="2.3"/>',
  user: '<circle cx="12" cy="8.4" r="3.7"/>'
    + '<path d="M4.9 20.2a7.1 7.1 0 0 1 14.2 0"/>',
  key: '<circle cx="8.2" cy="12" r="3.9"/><path d="M12.1 12h8.3"/>'
    + '<path d="M17.2 12v3.2M20.4 12v2.4"/>'
};

function svgIcon(body) { return ICON_HEAD + body + '</svg>'; }

/* Значок навигации в своей обёртке — той же, что у значка раздела: размер
   задаёт CSS кнопки, а не сама картинка. */
function uiIcon(name) {
  var box = el('span', 'ico');
  box.innerHTML = svgIcon(UI_ICONS[name]);
  return box;
}

/* Значок раздела строкой меню. Разметку берём из своего же набора — снаружи
   сюда ничего не приходит, кроме идентификатора раздела. */
function iconNode(id) {
  var box = el('span', 'ico');
  box.innerHTML = svgIcon(ICONS[id] || ICONS.custom);
  return box;
}

var $ = function (id) { return document.getElementById(id); };

/* ------------------------------------------------------------------ сеть */
function call(path, body) {
  var opts = { headers: { 'Content-Type': 'application/json' } };
  if (body) { opts.method = 'POST'; opts.body = JSON.stringify(body); }
  return fetch(path, opts).then(function (res) {
    if (res.status === 401) { dropAdmin(); return Promise.reject('auth'); }
    return res.json().then(function (data) {
      if (!res.ok) { return Promise.reject(data.error || 'ошибка ' + res.status); }
      return data;
    });
  });
}

/* Вход — это отдельный экран поверх сайта, а не ворота перед ним: страница
   и без пароля показывает новости, поэтому из формы всегда есть дорога
   обратно в ленту. */
function openLogin() {
  stopTimer();
  $('loginErr').textContent = '';
  $('app').className = '';
  $('login').className = 'on';
  $('pass').focus();
}

function hideLogin() {
  $('pass').value = '';
  $('login').className = '';
  $('app').className = 'on';
  startTimer();
}

/* Кнопка в шапке: владельцу — настройки, гостю — вход. */
function whoTap() {
  if (S.admin) { go('tools'); } else { openLogin(); }
}

function login(event) {
  event.preventDefault();
  var err = $('loginErr');
  err.textContent = '';
  call('/api/login', { token: $('pass').value }).then(function () {
    hideLogin();
    S.admin = true;
    S.view = 'news';
    S.section = '';
    reboot();
  }).catch(function (reason) {
    err.textContent = typeof reason === 'string' ? reason : 'не пускает';
  });
  return false;
}

function logout() {
  call('/api/logout', {}).then(dropAdmin).catch(dropAdmin);
}

/* Вышли сами или кончился вход — страница не гаснет, а становится тем, чем
   она открыта всякому: новостями. Служебное со всеми его данными уходит. */
function dropAdmin() {
  if (!S.admin) { return; }            /* и так гость — перерисовывать нечего */
  S.admin = false;
  S.tools = null;
  if (!allowed(S.view)) { S.view = 'news'; S.section = ''; }
  reboot();
}

/* Сменилась роль — всё, что страница помнит, принадлежит другому читателю:
   выбрасываем и перечитываем с нуля. */
function reboot() {
  S.state = null;
  S.alerts = [];
  S.last = '';
  S.unread = 0;
  S.hot = false;
  S.hello = true;
  paint();
  refresh(false);
  loadNews(true);
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

/* --------------------------------------------------- что уже видели */
/* Время самой свежей новости прошлого захода. Лежит в браузере, а не в базе:
   серверу знать, когда читатель открывал страницу, незачем, а страницу могут
   смотреть с телефона и с ноутбука порознь — и «новое» у них своё. */
var MARK = 'nd.seen';

function lastVisit() {
  try { return localStorage.getItem(MARK) || ''; } catch (err) { return ''; }
}

function keepVisit(iso) {
  try { if (iso) { localStorage.setItem(MARK, iso); } } catch (err) { }
}

/* Общая лента без поиска — единственное место, где отметку можно двигать: в
   поиске и в закладках порядок не хронологический, и «новое» в них соврало бы.
   Отфильтрованную по разделам черту рисуем, а отметку не трогаем: за чертой
   осталось бы непрочитанное из разделов, которые сейчас скрыты. */
function chronological() {
  return S.view === 'news' && !S.q;
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
  if (!allowed(view)) { view = 'news'; }
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
  S.finding = false;
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
  drawIcons();
  $('tune').textContent = S.filters.length
    ? '⚙ Фильтры · ' + S.filters.length : '⚙ Фильтры';
  markSearch();
  drawMeta();
  drawNav();
  drawRubrics();
  drawChips();
  drawTabs();
  if (S.view === 'alerts') { drawAlerts(); }
  if (S.view === 'tools') { drawPanel(); }
}

/* Значки в шапке. У гостя от них остаётся один: ключ — вход для владельца.
   «Избранное» и «Уведомления» ему не показываем — там отметки и рассылки
   владельца, и сервер их всё равно не отдаст. */
function drawIcons() {
  document.body.className = S.admin ? '' : 'guest';
  $('star').className = 'icon' + (S.admin ? '' : ' hide')
                      + (S.view === 'liked' ? ' on' : '');
  $('bell').className = 'icon' + (S.admin ? '' : ' hide')
                      + (S.view === 'alerts' ? ' on' : '');
  var who = $('who');
  who.className = 'icon' + (S.view === 'tools' ? ' on' : '');
  who.innerHTML = svgIcon(S.admin ? UI_ICONS.user : UI_ICONS.key);
  who.title = S.admin ? 'Настройки' : 'Войти';
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
  /* сколько у бота источников, стоит ли рассылка на паузе и чем он занят —
     это про службу, а не про новости: такое видит только владелец */
  if (S.admin) {
    bits.push(st.feeds + ' ' +
              plural(st.feeds, 'источник', 'источника', 'источников'));
    if (st.paused) { bits.push('<b class="warn">⏸ рассылка на паузе</b>'); }
    if (st.busy) {
      bits.push('<b class="warn">выполняется: ' + esc(st.busy) + '</b>');
    }
  }
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
    nav.appendChild(navItem(entry.id, entry.title, count, on,
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

/* Рубрики в шапке — то же меню разделов, что слева на широком экране:
   на телефоне левого меню нет, а ходить за разделами в отдельный экран
   читатель не станет. Выбранная рубрика подтягивается к середине строки —
   иначе она осталась бы за краем, и было бы не видно, что вообще выбрано. */
var rubricsDrawn = null;

function drawRubrics() {
  var box = $('rubrics');
  var ids = S.menu.map(function (entry) { return entry.id; });
  var sign = ids.length + ':' + ids.join(',') + ':'
           + (S.view === 'news' ? '1' : '0') + S.section;
  /* Перерисовка сбрасывает прокрутку строки вбок, а лента перечитывается и
     сама («Показать ещё», пришедший выпуск): рисуем, только когда набор
     рубрик или выбранная и правда поменялись. */
  if (sign === rubricsDrawn) { return; }
  rubricsDrawn = sign;
  box.innerHTML = '';
  var here = null;
  S.menu.forEach(function (entry) {
    var on = S.view === 'news' && S.section === entry.id;
    /* «Главное» в строке рубрик — это «Все»: рядом с названиями разделов
       читается как ещё один раздел, а это вся лента целиком */
    var button = el('button', on ? 'on' : null, entry.id ? entry.title : 'Все');
    button.type = 'button';
    button.onclick = function () { go('news', entry.id); };
    if (on) { here = button; }
    box.appendChild(button);
  });
  if (here) { centerRubric(box, here); }
}

/* Подтянуть рубрику к середине строки, не трогая прокрутку самой страницы:
   scrollIntoView увёл бы заодно и её. */
function centerRubric(box, button) {
  var shift = button.offsetLeft - (box.clientWidth - button.offsetWidth) / 2;
  box.scrollLeft = Math.max(0, shift);
}

function navItem(id, name, count, on, act) {
  var button = el('button', 'item' + (on ? ' on' : ''));
  button.type = 'button';
  button.appendChild(iconNode(id));
  button.appendChild(el('span', 'name', name));
  if (count) { button.appendChild(el('span', 'num', count)); }
  button.onclick = act;
  return button;
}

function drawTabs() {
  var box = $('tabs');
  box.innerHTML = '';
  TABS.forEach(function (tab) {
    if (tab.admin && !S.admin) { return; }
    var on = S.view === tab.id && (tab.id !== 'news' || !S.section);
    var button = el('button', on ? 'on' : '');
    button.type = 'button';
    button.appendChild(uiIcon(tab.icon));
    button.appendChild(el('span', null, tab.name));
    if (tab.id === 'alerts' && S.unread) {
      button.appendChild(alertBadge());
    }
    button.onclick = function () { go(tab.id); };
    box.appendChild(button);
  });
  var bell = $('bell');
  bell.innerHTML = svgIcon(UI_ICONS.bell);
  if (S.admin && S.unread) { bell.appendChild(alertBadge()); }
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
    button.appendChild(iconNode(entry.id));
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
    keepState(data.state);
    if (data.side) {
      S.side = data.side;
      S.menu = data.side.menu || [];
      pruneFilters();
      drawChips();
      drawNav();
      drawRubrics();
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
  if (reset) {
    box.innerHTML = '';
    S.day = '';
    S.cursor = -1;
    /* отметку читаем один раз на показ ленты и до конца показа не трогаем:
       иначе черта уехала бы вслед за только что записанным временем */
    S.mark = chronological() ? lastVisit() : '';
    S.fresh = 0;
    S.drawn = false;
  }
  items.forEach(function (item) {
    if (item.day && item.day !== S.day) {
      S.day = item.day;
      box.appendChild(el('div', 'daybar', item.dayName || ''));
    }
    if (S.mark && item.iso && item.iso <= S.mark) {
      /* первая новость, которую читатель уже видел: выше неё всё новое */
      if (!S.drawn && S.fresh) { box.appendChild(drawSeenLine(S.fresh)); }
      S.drawn = true;
    } else if (S.mark && !S.drawn) {
      S.fresh += 1;
    }
    box.appendChild(drawCard(item));
  });
  /* двигаем отметку только по полной ленте: в разделе и под фильтрами за
     чертой осталось бы непрочитанное из тех разделов, что сейчас скрыты */
  if (reset && chronological() && !S.section && !filtering() &&
      items.length && items[0].iso) {
    keepVisit(items[0].iso);
  }
  if (!box.childNodes.length) { box.appendChild(drawEmpty()); }
  markClamped();
}

function drawSeenLine(count) {
  return el('div', 'seenbar', count + ' ' +
    plural(count, 'новая новость', 'новые новости', 'новых новостей') +
    ' с прошлого захода');
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
  box.appendChild(el('div', null, S.admin && S.state && S.state.next
    ? 'Выпуск ещё не приходил. Ближайший — ' + S.state.next + '.'
    : 'Новостей пока нет — загляните позже.'));
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
  if (item.caveat) { text.appendChild(el('p', 'caveat', '⚠️ ' + item.caveat)); }
  if (item.earlier && item.earlier.length) { text.appendChild(drawEarlier(item)); }

  var foot = el('div', 'foot');
  /* 👍/👎/🔖 — это вкусы владельца, они уходят боту и меняют выпуск. Гостю
     кнопки не рисуем: нажать ему всё равно нечего — POST для него закрыт */
  if (S.admin) {
    foot.appendChild(actButton('🔖', 'save', item.saved, item));
    foot.appendChild(actButton('👍', 'up', item.verdict === 'up', item));
    foot.appendChild(actButton('👎', 'down', item.verdict === 'down', item));
  }
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

/* Цепочка сюжета под карточкой. Событие другое, а сюжет тот же: землетрясение
   было вечером, число жертв пришло ночью. Показываем в обратном порядке —
   от ближайшего шага к самому раннему, так и вспоминают. */
function drawEarlier(item) {
  var box = el('div', 'back');
  box.appendChild(el('span', 'cap', '🧵 Ранее по теме'));
  item.earlier.forEach(function (step) {
    var row;
    if (step.url) {
      row = el('a', null, step.title);
      row.href = step.url;
      row.target = '_blank';
      row.rel = 'noopener noreferrer';
    } else {
      row = el('span', 'step', step.title);
    }
    if (step.at) {
      row.appendChild(document.createTextNode(' '));
      row.appendChild(el('time', null, '· ' + step.at));
    }
    box.appendChild(row);
  });
  return box;
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
  drawStoriesBox();
  drawSourcesBox();
  drawTopicsBox();
}

/* Справка о выпуске — расписание, источники, пауза — целиком служебная,
   и гостю правой колонки с ней не полагается. */
function drawDigestBox() {
  var box = $('boxDigest'), st = S.state;
  box.className = 'box' + (S.admin ? '' : ' hide');
  box.innerHTML = '';
  if (!st || !S.admin) { return; }
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
    facts.appendChild(el('div', 'warn', 'чат владельца не задан'));
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

/* Сюжеты, которые сейчас развиваются. Считается по тем же связям, что и
   «Ранее по теме» под карточкой, — отдельных запросов к модели за этим нет. */
function drawStoriesBox() {
  var box = $('boxStories');
  var list = (S.side && S.side.stories) || [];
  box.className = 'box' + (list.length ? '' : ' hide');
  box.innerHTML = '';
  if (!list.length) { return; }
  box.appendChild(el('h3', null, 'Сюжеты недели'));
  var rows = el('div', 'rows');
  list.forEach(function (story) {
    var button = el('button');
    button.type = 'button';
    button.title = 'Развивается: ' + story.count + ' ' +
      plural(story.count, 'новость', 'новости', 'новостей') +
      (story.at ? ', последняя — ' + story.at : '');
    button.appendChild(el('span', 'dot', story.emoji));
    button.appendChild(el('span', 'nm', story.title));
    button.appendChild(el('span', 'rate', String(story.count)));
    button.onclick = function () { find(story.title); };
    rows.appendChild(button);
  });
  box.appendChild(rows);
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
  markQuery();
  S.finding = true;     /* запрос пришёл не из строки — покажем, что ищем */
  go('news', '');
}

/* На телефоне строка поиска свёрнута до лупы в шапке: разворачиваем по
   нажатию и сворачиваем, когда искать больше нечего. На широком экране
   строка стоит в шапке всегда — там это переключение ничего не меняет,
   кроме самого запроса. */
function toggleSearch() {
  if (S.finding) { hideSearch(); } else { showSearch(); }
}

function showSearch() {
  S.finding = true;
  markSearch();
  $('q').focus();
  $('q').select();
}

/* Сворачиваем вместе с запросом: строка ушла, а лента осталась резаной по
   невидимому слову — это худшее, что можно сделать с читателем. */
function hideSearch() {
  S.finding = false;
  markSearch();
  if (S.q || $('q').value) { clearSearch(); }
}

function markSearch() {
  $('hdr').className = S.finding ? 'finding' : '';
  $('find').className = 'icon' + (S.finding ? ' on' : '');
}

/* Правый угол строки поиска занят по очереди: пусто — подсказка про Ctrl+K,
   набрано — крестик «очистить». Вместе они бы налезли друг на друга. */
function markQuery() {
  var filled = !!$('q').value;
  $('clear').className = filled ? 'clear' : 'clear hide';
  $('kbd').className = filled ? 'kbd hide' : 'kbd';
}

function search(event) {
  event.preventDefault();
  S.q = $('q').value.trim();
  go('news', S.section);
  return false;
}

/* Печатают быстрее, чем отвечает база, — ждём паузы в наборе. */
function typed() {
  markQuery();
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
  markQuery();
  S.q = '';
  loadNews(true);
}

/* ------------------------------------------------------------------ тема */
/* Светлая или тёмная — выбор читателя, а не настройка бота: он лежит в
   браузере и никуда не уходит. Пока выбора нет, идём за системой и слушаем
   её дальше: сменился системный вид — сменился и наш. Сам атрибут ставит
   скрипт в голове страницы, здесь только его правка руками. */
var THEME = 'nd.theme';

function theme() {
  return document.documentElement.getAttribute('data-theme') === 'dark'
       ? 'dark' : 'light';
}

function chosenTheme() {
  try { return localStorage.getItem(THEME) || ''; } catch (err) { return ''; }
}

function setTheme(name) {
  var pick = name === 'dark' ? 'dark' : 'light';
  document.documentElement.setAttribute('data-theme', pick);
  try { localStorage.setItem(THEME, pick); } catch (err) { }
  drawTheme();
}

function flipTheme() { setTheme(theme() === 'dark' ? 'light' : 'dark'); }

/* В столбце разделов стоят обе кнопки, и нажатая подсвечена: видно не только
   куда нажать, но и что сейчас. В шапке (это телефон, столбца там нет) кнопка
   одна, и на ней нарисовано, куда переключит, — иначе она читалась бы как
   «сейчас день», а не «сделать ночь». */
function drawTheme() {
  var now = theme();
  var light = $('lightTheme');
  var dark = $('darkTheme');
  light.className = 'tgl' + (now === 'light' ? ' on' : '');
  dark.className = 'tgl' + (now === 'dark' ? ' on' : '');
  light.innerHTML = svgIcon(SUN_ICON);
  dark.innerHTML = svgIcon(MOON_ICON);
  var head = $('theme');
  head.innerHTML = svgIcon(now === 'dark' ? SUN_ICON : MOON_ICON);
  head.title = now === 'dark' ? 'Светлая тема' : 'Тёмная тема';
}

function watchTheme() {
  if (!window.matchMedia) { return; }
  var system = window.matchMedia('(prefers-color-scheme: dark)');
  var follow = function (event) {
    if (chosenTheme()) { return; }    /* выбрали руками — система не указ */
    document.documentElement.setAttribute('data-theme',
                                          event.matches ? 'dark' : 'light');
    drawTheme();
  };
  if (system.addEventListener) { system.addEventListener('change', follow); }
  else if (system.addListener) { system.addListener(follow); }
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
/* Только чтение: подписчики и настройки приложения. Правят их на самой
   машине бота — страница про них просто рассказывает. Экран этот
   владельцу и виден: гостю сюда не попасть, а данные к нему не приедут. */
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
/* Состояние приходит с каждым ответом, и вместе с ним — кто мы сегодня.
   Один этот признак и решает, что страница рисует. */
function keepState(st) {
  if (!st) { return; }
  S.state = st;
  if (!!st.admin === S.admin) { return; }
  /* вошли или вышли — меняется вся страница разом, а не одна панель */
  S.admin = !!st.admin;
  if (!allowed(S.view)) { S.view = 'news'; S.section = ''; }
  paint();
}

/* По чему видно, что лента пополнилась. У владельца это последняя рассылка,
   у гостя рассылок нет — там самая свежая новость. Считает признак сервер,
   странице довольно того, что он изменился. */
function feedMark() { return S.last; }

function applyAlerts(data) {
  keepState(data.state);
  S.alerts = data.alerts || [];
  S.last = data.last || '';
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
    keepState(data.state);
    if (S.view === 'tools') { drawPanel(); }
    drawMeta();
    drawDigestBox();
  }).catch(function (reason) {
    if (reason !== 'auth') { toast('' + reason); }
  });
}

function refresh(manual) {
  /* пока состояния нет, сравнивать не с чем: первый ответ — не «пришло
     новое», а просто первый ответ, и ленту за ним перечитывать незачем */
  var known = !!S.state, before = feedMark();
  return call('/api/alerts').then(function (data) {
    applyAlerts(data);
    /* пришёл выпуск — значит в ленте появились новости, перечитываем её */
    if (known && feedMark() !== before && isNews(S.view)) { loadNews(true); }
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

/* Страница открывается сразу лентой: пароля для новостей не спрашивают, а
   владельца сервер узнает по cookie — и тогда в ответе придёт `admin`. */
function start() {
  if (S.started) { return; }
  S.started = true;
  $('login').className = '';
  $('app').className = 'on';
  reboot();
  startTimer();
}

document.addEventListener('visibilitychange', function () {
  if (!document.hidden && S.started) { refresh(false); }
});

/* ----------------------------------------------------------- клавиатура */
/* Лента листается с клавиатуры так же, как в почте и в читалках: j и k ведут
   по карточкам, Enter открывает источник, o разворачивает текст, / встаёт в
   поиск. Мышью всё это работало и раньше — здесь только руки не отрываются
   от клавиатуры. Стрелки не занимаем: ими прокручивают страницу целиком. */
function cards() {
  return document.querySelectorAll('#list .news');
}

function pick(shift) {
  var list = cards();
  if (!list.length) { return; }
  var at = S.cursor + shift;
  if (at < 0) { at = 0; }
  if (at >= list.length) { at = list.length - 1; }
  /* класс снимаем через classList, а не правкой className регуляркой:
     PAGE — обычная строка Python, и \\b в ней стал бы символом забоя */
  Array.prototype.forEach.call(list, function (card) {
    card.classList.remove('sel');
  });
  S.cursor = at;
  list[at].classList.add('sel');
  list[at].scrollIntoView({block: 'nearest'});
}

function current() {
  var list = cards();
  return S.cursor >= 0 && S.cursor < list.length ? list[S.cursor] : null;
}

/* Печатает человек или листает — видно по тому, где стоит курсор: в поле
   ввода j и k это буквы, а не команды. */
function typing(node) {
  if (!node) { return false; }
  var tag = (node.tagName || '').toLowerCase();
  return tag === 'input' || tag === 'textarea' || tag === 'select' ||
         node.isContentEditable;
}

document.addEventListener('keydown', function (event) {
  if (event.key === 'Escape') {
    closeFilters();
    if ($('login').className === 'on') { hideLogin(); }
    return;
  }
  /* Ctrl+K (на маке ⌘K) — тот же жест, что и в почте, и в редакторах: встать
     в поиск, откуда бы ни писали. Поэтому он идёт до проверки на «печатают в
     поле»: из чужого поля позвать поиск тоже надо. */
  if ((event.ctrlKey || event.metaKey) && !event.altKey &&
      (event.key === 'k' || event.key === 'K')) {
    event.preventDefault();
    showSearch();
    return;
  }
  if (event.metaKey || event.ctrlKey || event.altKey) { return; }
  if (typing(document.activeElement)) { return; }

  if (event.key === '/') {
    event.preventDefault();
    showSearch();
    return;
  }
  if (event.key === 'j') { event.preventDefault(); pick(1); return; }
  if (event.key === 'k') { event.preventDefault(); pick(-1); return; }

  var card = current();
  if (!card) { return; }
  if (event.key === 'Enter') {
    var link = card.querySelector('h2 a');
    if (link) { event.preventDefault(); window.open(link.href, '_blank', 'noopener'); }
    return;
  }
  if (event.key === 'o') {
    var sum = card.querySelector('.sum');
    if (sum) { event.preventDefault(); sum.click(); }
  }
});

/* повернули телефон, растянули окно — в три строки помещается уже другое */
window.addEventListener('resize', markClamped);

/* Значки, которые не меняются от данных: лупа в строке поиска и подсказка
   про Ctrl+K. На маке тот же жест зовут ⌘K — пишем так, как он выглядит на
   клавиатуре читателя. */
var MAC = /Mac|iPhone|iPad|iPod/.test(navigator.platform
                                      || navigator.userAgent || '');
$('lens').innerHTML = svgIcon(LENS_ICON);
$('find').innerHTML = svgIcon(LENS_ICON);
$('star').innerHTML = svgIcon(UI_ICONS.star);
$('bell').innerHTML = svgIcon(UI_ICONS.bell);
$('who').innerHTML = svgIcon(UI_ICONS.user);
$('kbd').textContent = MAC ? '⌘K' : 'Ctrl K';
drawTheme();
watchTheme();

start();
</script>
</body>
</html>
"""

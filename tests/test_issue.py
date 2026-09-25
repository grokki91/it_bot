# -*- coding: utf-8 -*-
"""Выпуск экранами: оглавление, разделы и переходы между ними.

Сеть не трогается: Telegram подменён списками отправленного и правок.
"""
import logging
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ND_HOME", tempfile.mkdtemp(prefix="ndtest-"))

from newsdigest import bot, config, feedback, issueview, render, storage  # noqa: E402
from newsdigest.config import CFG  # noqa: E402
from newsdigest.telegram import TG_LIMIT  # noqa: E402

from test_core import item  # noqa: E402

logging.getLogger("nd").addHandler(logging.NullHandler())
logging.getLogger("nd").propagate = False

CHAT = "77"


def cards(count, topic="ai", text="кратко", score=7.5):
    out = []
    for i in range(count):
        group = [item("https://%s.com/%d" % (topic, i), "Заголовок %s%d" % (topic, i),
                      "src-%s%d" % (topic, i))]
        out.append(({"headline": "Заголовок %s%d" % (topic, i), "what": text,
                     "why": "потому что"}, group, score - i * 0.1, "labs"))
    return out


def issue(*sizes, note="", text="кратко"):
    """Снимок выпуска из нескольких разделов — то, что ложится в базу."""
    names = ("ai", "medicine", "space", "science", "climate", "politics",
             "sports", "games", "economy", "cinema")
    blocks = [(names[i], cards(size, names[i], text, 9.0 - i))
              for i, size in enumerate(sizes)]
    return issueview.snapshot(blocks, render.issue_info(blocks, 3775, note))


class TestSnapshot(unittest.TestCase):
    def test_keeps_everything_a_screen_needs(self):
        card = issue(2)["sections"][0]["cards"][0]
        for field in ("hash", "title", "what", "why", "url", "source", "score"):
            self.assertTrue(card[field], field)

    def test_survives_json(self):
        import json
        before = issue(2, 3)
        self.assertEqual(json.loads(json.dumps(before, ensure_ascii=False)), before)


class TestHub(unittest.TestCase):
    """Оглавление: минимум в шапке, главное за день и кнопки разделов."""

    def test_header_holds_only_the_issue_and_the_day(self):
        text, _shown = issueview.hub_text(issue(2, 2, 1))
        head = text.split("\n\n")[0]
        self.assertEqual(len(head.split("\n")), 2)
        self.assertIn("5 новостей", head)
        self.assertIn(render.day(), head)
        for gone in ("материалов за сутки", "раздела", "продолжение"):
            self.assertNotIn(gone, head)

    def test_shows_five_best_news_of_the_day(self):
        # порядок главного — по оценке, а не по порядку разделов
        blocks = [("ai", cards(4, "ai", score=7.0)),
                  ("medicine", cards(2, "medicine", score=9.0))]
        snapshot = issueview.snapshot(blocks, render.issue_info(blocks, 100))
        text, shown = issueview.hub_text(snapshot)
        self.assertEqual(shown, issueview.TOP_SHOWN)
        self.assertIn("ГЛАВНОЕ СЕГОДНЯ", text)
        self.assertIn("<b>Заголовок medicine0</b>", text)
        self.assertLess(text.index("Заголовок medicine1"),
                        text.index("Заголовок ai0"))
        self.assertIn("<b>Заголовок ai2</b>", text)
        self.assertNotIn("Заголовок ai3", text)     # шестая — уже под кнопкой

    def test_top_news_are_not_numbered(self):
        text, shown = issueview.hub_text(issue(6, 6), issueview.TOP_MAX)
        self.assertEqual(shown, issueview.TOP_MAX)
        self.assertNotRegex(text, r"<b>\d+\. ")

    def test_summary_is_cut_to_one_line(self):
        long_text = "Первое предложение сути. " + "И ещё много слов подряд. " * 8
        text, _shown = issueview.hub_text(issue(2, text=long_text))
        self.assertIn("Первое предложение сути.", text)
        self.assertNotIn("И ещё много слов подряд. И ещё", text)

    def test_sections_are_a_grid_without_counts(self):
        snapshot = issue(5, 3, 4)
        _text, shown = issueview.hub_text(snapshot)
        keyboard = issueview.hub_keyboard(snapshot, 12, shown)
        sections = [[b["text"] for b in row] for row in keyboard
                    if row[0]["callback_data"].startswith("nav:12:sec:")]
        self.assertEqual(sections, [["🤖 ИИ и технологии", "🩺 Медицина"],
                                    ["🚀 Космос"]])
        data = [b["callback_data"] for row in keyboard for b in row]
        self.assertIn("nav:12:sec:medicine", data)
        for row in keyboard:
            for button in row:
                self.assertLessEqual(len(button["callback_data"].encode()), 64)

    def test_long_section_names_are_short_on_buttons(self):
        """В пол-экрана «Компьютерное железо» не помещается — там «Железо»,
        а в самом разделе имя полное."""
        blocks = [("hardware", cards(1, "hardware")), ("ai", cards(1, "ai"))]
        snapshot = issueview.snapshot(blocks, render.issue_info(blocks, 100))
        labels = [b["text"] for row in issueview.hub_screen(snapshot, 3)[1]
                  for b in row]
        self.assertIn("🖥 Железо", labels)
        text = issueview.screen(snapshot, 3, issueview.SEC, "hardware")[0]
        self.assertIn("<b>Компьютерное железо</b>", text)

    def test_menu_is_six_rows_instead_of_nine(self):
        """Десять разделов и десять главных новостей: «ещё главное», три ряда
        разделов, «все разделы», «Мои темы» с «Поделиться». Столбиком было
        девять рядов во всю ширину."""
        keyboard = issueview.hub_screen(issue(*([1] * 10)), 1)[1]
        self.assertEqual(len(keyboard), 6)
        self.assertEqual([len(row) for row in keyboard], [1, 2, 2, 2, 1, 2])

    def test_rest_of_the_sections_hides_behind_one_button(self):
        snapshot = issue(*([1] * 10))
        text, keyboard = issueview.hub_screen(snapshot, 1)
        sections = [b for row in keyboard for b in row
                    if b["callback_data"].startswith("nav:1:sec:")]
        self.assertEqual(len(sections), issueview.SECTIONS_SHOWN)
        # последняя строка оглавления — «Мои темы», «все разделы» перед ней
        self.assertEqual(keyboard[-1][0]["callback_data"], "pref:open::1")
        self.assertEqual(keyboard[-2], [{"text": "☰ Все разделы",
                                         "callback_data": "nav:1:secs"}])
        # «все разделы» показывает все разделы и умеет свернуться обратно
        wide = issueview.hub_screen(snapshot, 1, issueview.SECS)[1]
        self.assertEqual(len([b for row in wide for b in row
                              if b["callback_data"].startswith("nav:1:sec:")]), 10)
        self.assertEqual(wide[-2][0]["callback_data"], "nav:1:home")
        self.assertTrue(text)

    def test_sections_hide_only_if_that_saves_rows(self):
        """Восемь разделов — те же четыре ряда, что шесть и «☰ Все разделы»:
        прятать два раздела за кнопкой незачем."""
        keyboard = issueview.hub_screen(issue(*([1] * 8)), 1)[1]
        data = [b["callback_data"] for row in keyboard for b in row]
        self.assertEqual(len([d for d in data if d.startswith("nav:1:sec:")]), 8)
        self.assertNotIn("nav:1:secs", data)

    def test_more_top_news_opens_the_rest(self):
        snapshot = issue(6, 6)
        keyboard = issueview.hub_screen(snapshot, 1)[1]
        self.assertEqual(keyboard[0][0]["callback_data"], "nav:1:top")
        self.assertIn("Ещё 5 главных новостей", keyboard[0][0]["text"])
        text, shown = issueview.hub_text(snapshot, issueview.TOP_MAX)
        self.assertEqual(shown, issueview.TOP_MAX)
        self.assertEqual(text.count("🔗"), issueview.TOP_MAX)
        # раскрытое главное сворачивается обратно к пяти
        keyboard = issueview.hub_screen(snapshot, 1, issueview.TOP)[1]
        self.assertEqual(keyboard[0][0]["callback_data"], "nav:1:home")

    def test_empty_sections_are_named_on_the_sections_screen(self):
        snapshot = issue(2, 2, note="без новостей: Роботы")
        self.assertNotIn("без новостей", issueview.hub_screen(snapshot, 1)[0])
        self.assertIn("без новостей: Роботы",
                      issueview.hub_screen(snapshot, 1, issueview.SECS)[0])


class TestSection(unittest.TestCase):
    """Экран раздела: новости, реакции и дорога назад."""

    def test_shows_the_section_with_its_news(self):
        snapshot = issue(3, 2)
        text, keyboard = issueview.screen(snapshot, 7, issueview.SEC, "ai")
        self.assertIn("<b>ИИ и технологии</b>", text)
        self.assertIn("3 новости", text)
        self.assertIn("Заголовок ai2", text)
        self.assertNotIn("Заголовок medicine0", text)
        self.assertEqual(keyboard[-1][0]["callback_data"], "nav:7:home")
        self.assertIn("К разделам", keyboard[-1][0]["text"])

    def test_reaction_row_per_news(self):
        snapshot = issue(3, 2)
        _text, keyboard = issueview.screen(snapshot, 7, issueview.SEC, "ai")
        rows = [r for r in keyboard if r[0]["callback_data"].startswith("fb:")]
        self.assertEqual(len(rows), 3)
        self.assertEqual([b["text"] for b in rows[0]][1:], ["👎", "🔖"])
        self.assertTrue(rows[0][0]["text"].startswith("👍 Заголовок"))

    def test_past_votes_are_marked(self):
        snapshot = issue(2, 2)
        url_hash = snapshot["sections"][0]["cards"][0]["hash"]
        _text, keyboard = issueview.screen(snapshot, 7, issueview.SEC, "ai",
                                           {url_hash: feedback.UP}, {url_hash})
        self.assertTrue(keyboard[0][0]["text"].endswith(render.MARK))
        self.assertTrue(keyboard[0][2]["text"].endswith(render.MARK))
        self.assertFalse(keyboard[0][1]["text"].endswith(render.MARK))

    def test_long_section_hides_the_tail_behind_a_button(self):
        snapshot = issue(9, 2)
        text, keyboard = issueview.screen(snapshot, 7, issueview.SEC, "ai")
        self.assertEqual(text.count("🔗"), issueview.SECTION_SHOWN)
        more = [r for r in keyboard if r[0]["callback_data"] == "nav:7:all:ai"]
        self.assertEqual(len(more), 1)
        self.assertIn("Ещё 4", more[0][0]["text"])
        whole, _kb = issueview.screen(snapshot, 7, issueview.ALL, "ai")
        self.assertEqual(whole.count("🔗"), 9)

    def test_huge_section_still_fits_the_message(self):
        snapshot = issue(40, text="очень длинный текст " * 40)
        text, _keyboard = issueview.screen(snapshot, 7, issueview.ALL, "ai")
        self.assertLessEqual(len(text), TG_LIMIT)
        self.assertIn("Заголовок ai0", text)

    def test_single_section_issue_opens_straight_at_the_news(self):
        """Ответ /news листать нечем: оглавление из одного пункта — лишний шаг."""
        snapshot = issue(4)
        text, keyboard = issueview.screen(snapshot, 7)
        self.assertIn("<b>ИИ и технологии</b>", text)
        self.assertNotIn("ГЛАВНОЕ СЕГОДНЯ", text)
        self.assertFalse([r for r in keyboard
                          if r[0]["callback_data"] == "nav:7:home"])

    def test_unknown_section_falls_back_to_the_hub(self):
        text, _keyboard = issueview.screen(issue(2, 2), 7, issueview.SEC, "нет")
        self.assertIn("ГЛАВНОЕ СЕГОДНЯ", text)


def shared_text(link) -> str:
    """Подпись, которую ссылка t.me/share положит в чат под ссылкой."""
    from urllib.parse import parse_qs, urlparse
    return (parse_qs(urlparse(link).query).get("text") or [""])[0]


class TestShare(unittest.TestCase):
    """«Поделиться»: под теми же новостями — их заголовки, нажал — Telegram
    спросил, в какой чат."""

    def test_hub_has_share_next_to_my_topics(self):
        keyboard = issueview.hub_screen(issue(3, 2), 4)[1]
        self.assertEqual([b["callback_data"] for b in keyboard[-1]],
                         ["pref:open::4", "nav:4:share"])

    def test_section_has_share_next_to_back(self):
        _text, keyboard = issueview.screen(issue(3, 2), 4, issueview.SEC, "ai")
        self.assertEqual([b["callback_data"] for b in keyboard[-1]],
                         ["nav:4:home", "nav:4:share:sec:ai"])

    def test_hub_share_lists_just_the_news_on_screen(self):
        snapshot = issue(6, 6)
        hub = issueview.hub_screen(snapshot, 4)[0]
        text, keyboard = issueview.screen(snapshot, 4, issueview.SHARE)
        # текст не меняется: читатель видит те же новости, меняются кнопки
        self.assertEqual(text, hub)
        news = keyboard[:-1]
        self.assertEqual(len(news), issueview.TOP_SHOWN)    # пять — не десять
        self.assertEqual(news[0][0]["text"], "Заголовок ai0")   # без номера
        self.assertEqual(keyboard[-1], [{"text": "✖️ Отмена",
                                         "callback_data": "nav:4:home"}])

    def test_expanded_top_shares_all_ten_and_cancels_back_to_it(self):
        snapshot = issue(6, 6)
        top, keyboard = issueview.hub_screen(snapshot, 4, issueview.TOP)
        self.assertEqual(keyboard[-1][-1]["callback_data"], "nav:4:share:top")
        _ident, name, arg = issueview.parse("nav:4:share:top")
        text, keyboard = issueview.screen(snapshot, 4, name, arg)
        self.assertEqual(text, top)
        self.assertEqual(len(keyboard) - 1, issueview.TOP_MAX)
        self.assertEqual(keyboard[-1][0]["callback_data"], "nav:4:top")

    def test_full_section_shares_every_news_it_shows(self):
        snapshot = issue(8, 2)
        keyboard = issueview.screen(snapshot, 4, issueview.ALL, "ai")[1]
        self.assertEqual(keyboard[-1][-1]["callback_data"], "nav:4:share:all:ai")
        _text, keyboard = issueview.screen(snapshot, 4, issueview.SHARE, "all:ai")
        self.assertEqual(len(keyboard) - 1, 8)
        self.assertEqual(keyboard[-1][0]["callback_data"], "nav:4:all:ai")

    def test_buttons_of_earlier_issues_still_work(self):
        """Кнопка из уже разосланного выпуска несёт только раздел: `share:ai`."""
        snapshot = issue(3, 2)
        _text, keyboard = issueview.screen(snapshot, 4, issueview.SHARE,
                                           "medicine")
        self.assertEqual(len(keyboard), 3)
        self.assertEqual(keyboard[0][0]["text"], "Заголовок medicine0")
        self.assertEqual(keyboard[-1][0]["callback_data"], "nav:4:sec:medicine")
        # раздела в выпуске нет — делимся главным из оглавления
        _text, keyboard = issueview.screen(snapshot, 4, issueview.SHARE, "нет")
        self.assertEqual(keyboard[-1][0]["callback_data"], "nav:4:home")

    def test_single_section_issue_shares_its_section(self):
        """У ответа /news оглавления нет — делиться нечем, кроме раздела."""
        snapshot = issue(4)
        text, keyboard = issueview.screen(snapshot, 4, issueview.SHARE)
        self.assertNotIn("ГЛАВНОЕ СЕГОДНЯ", text)
        self.assertEqual(len(keyboard) - 1, 4)
        self.assertEqual(keyboard[-1][0]["callback_data"], "nav:4:sec:ai")

    def test_news_button_opens_telegram_share(self):
        from urllib.parse import parse_qs, urlparse
        snapshot = issue(2)
        card = snapshot["sections"][0]["cards"][0]
        _text, keyboard = issueview.screen(snapshot, 4, issueview.SHARE, "sec:ai")
        button = keyboard[0][0]
        self.assertNotIn("callback_data", button)
        link = urlparse(button["url"])
        self.assertEqual((link.netloc, link.path), ("t.me", "/share/url"))
        query = parse_qs(link.query)
        self.assertEqual(query["url"], [card["url"]])
        self.assertTrue(query["text"][0].startswith(card["title"]))

    def test_share_label_ends_on_a_whole_word(self):
        """Длинный заголовок Telegram обрывает на полуслове («обходят защит»),
        поэтому режем сами — по слову и с «…»."""
        snapshot = issue(2)
        card = snapshot["sections"][0]["cards"][0]
        card["title"] = "Вредоносные npm-пакеты обходят защиту реестра и крадут токены"
        _text, keyboard = issueview.screen(snapshot, 4, issueview.SHARE, "sec:ai")
        label = keyboard[0][0]["text"]
        self.assertTrue(label.endswith("…"))
        self.assertLessEqual(len(label), issueview.SHARE_LABEL + 1)
        self.assertTrue(card["title"].startswith(label[:-1]))
        self.assertEqual(card["title"][len(label) - 1], " ")

    def test_share_keyboard_stays_light_for_telegram(self):
        """Десять длинных кириллических подписей — это больше 12 КБ ссылок,
        Telegram такую разметку отвергает (REPLY_MARKUP_TOO_LONG)."""
        import json
        snapshot = issue(6, 6)
        for block in snapshot["sections"]:
            for card in block["cards"]:
                card["title"] = "Очень длинный заголовок новости " * 6
                card["what"] = "Подробная суть новости без точки " * 8
        _text, keyboard = issueview.screen(snapshot, 4, issueview.SHARE,
                                           issueview.TOP)
        links = [row[0]["url"] for row in keyboard[:-1]]
        self.assertEqual(len(links),
                         len(issueview.hub_view(snapshot, issueview.TOP)[1]))
        self.assertGreater(len(links), issueview.TOP_SHOWN)
        self.assertLessEqual(sum(len(link) for link in links),
                             issueview.SHARE_BUDGET)
        self.assertLess(len(json.dumps(keyboard, ensure_ascii=False).encode()), 6000)
        self.assertTrue(shared_text(links[0]).startswith("Очень длинный"))

    def test_share_link_without_room_keeps_just_the_url(self):
        from urllib.parse import parse_qs, urlparse
        card = {"url": "https://example.com/" + "a" * 300, "title": "Заголовок"}
        query = parse_qs(urlparse(issueview.share_link(card, 100)).query)
        self.assertEqual(query, {"url": [card["url"]]})

    def test_shared_text_is_the_title_and_a_whole_sentence(self):
        card = {"title": "ИИ-агент OpenAI обошёл защиту портала Medicare",
                "what": "Агент сам прошёл капчу. Подробности — в отчёте.",
                "url": "https://example.com/openai-agent"}
        link = issueview.share_link(card)
        self.assertEqual(shared_text(link),
                         card["title"] + "\n\nАгент сам прошёл капчу.")
        # на суть места нет — уходит заголовок целиком, а не «… — А…»
        tight = issueview.share_link(card, len(link) - 1)
        self.assertEqual(shared_text(tight), card["title"])

    def test_spare_budget_goes_to_the_news_that_need_it(self):
        """Ровная доля бюджета мала для заголовка с сутью, а короткие
        заголовки её не выбирают — остаток отдаётся тем, кому не хватило."""
        long = {"title": "Длинный заголовок про важное событие дня, которое "
                         "обсуждают все",
                "what": "Суть события одним спокойным предложением, в котором "
                        "есть всё нужное читателю.",
                "url": "https://example.com/long"}
        brief = [{"title": "Коротко", "url": "https://example.com/%d" % i}
                 for i in range(4)]
        cards = [long] + brief
        even = issueview.SHARE_BUDGET // len(cards)
        self.assertGreater(len(issueview.share_link(long)), even)
        links = issueview.share_links(cards)
        self.assertLessEqual(sum(len(link) for link in links),
                             issueview.SHARE_BUDGET)
        self.assertEqual(shared_text(links[0]), long["title"] + "\n\n" + long["what"])
        self.assertEqual([shared_text(link) for link in links[1:]], ["Коротко"] * 4)

    def test_whole_titles_come_before_summaries(self):
        """Запас бюджета сперва возвращает обрезанные заголовки и только
        потом идёт на суть: иначе суть верхней новости съедает его целиком,
        а заголовки ниже так и уходят в чат с «…»."""
        first = {"title": "Коротко", "url": "https://example.com/a",
                 "what": "Суть события одним спокойным, ясным и коротким "
                         "предложением."}
        second = {"title": "Очень длинный заголовок новости который никак не "
                           "помещается в свою долю ссылок на экране "
                           "поделиться и ещё немного слов",
                  "url": "https://example.com/b"}
        link = issueview.share_link
        # бюджет — ровно на оба заголовка целиком
        budget = len(link(first, lead=False)) + len(link(second))
        # суть первой не влезает в ровную долю, но стоит меньше, чем
        # недобрал заголовок второй: раздавай запас просто сверху вниз —
        # суть досталась бы первой, а вторая ушла бы с обрубком
        self.assertGreater(len(link(first)), budget // 2)
        self.assertLess(len(link(first)) - len(link(first, lead=False)),
                        len(link(second)) - len(link(second, budget // 2)))
        links = issueview.share_links([first, second], budget)
        self.assertEqual([shared_text(each) for each in links],
                         [first["title"], second["title"]])

    def test_long_summary_is_left_out_not_cut(self):
        card = {"title": "Заголовок", "what": "слово " * 200,
                "url": "https://example.com/a?b=1&c=2"}
        link = issueview.share_link(card)
        self.assertIn("url=https%3A%2F%2Fexample.com%2Fa%3Fb%3D1%26c%3D2", link)
        self.assertEqual(shared_text(link), "Заголовок")


class TestRoutes(unittest.TestCase):
    def test_round_trip(self):
        self.assertEqual(issueview.parse(issueview.route(3, "sec", "ai")),
                         (3, "sec", "ai"))
        self.assertEqual(issueview.parse(issueview.route(3)), (3, "home", ""))

    def test_share_keeps_the_whole_origin(self):
        self.assertEqual(issueview.parse("nav:3:share:all:ai"),
                         (3, "share", "all:ai"))

    def test_alien_data_is_not_ours(self):
        for data in ("fb:up:hash", "sub:ok:1", "nav:", "nav:x:home", ""):
            self.assertEqual(issueview.parse(data), (0, "", ""))


class TestStorage(unittest.TestCase):
    def setUp(self):
        self.conn = storage.db()
        self.conn.execute("DELETE FROM issues")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_saved_issue_comes_back(self):
        ident = storage.save_issue(self.conn, CHAT, issue(2, 1))
        self.assertEqual(storage.load_issue(self.conn, CHAT, ident)["count"], 3)

    def test_issue_of_another_chat_is_not_given_away(self):
        ident = storage.save_issue(self.conn, CHAT, issue(1))
        self.assertEqual(storage.load_issue(self.conn, "999", ident), {})
        self.assertEqual(storage.load_issue(self.conn, CHAT, 0), {})

    def test_old_issues_are_dropped(self):
        idents = [storage.save_issue(self.conn, CHAT, issue(1))
                  for _ in range(storage.ISSUES_KEEP + 3)]
        self.assertEqual(storage.load_issue(self.conn, CHAT, idents[0]), {})
        self.assertTrue(storage.load_issue(self.conn, CHAT, idents[-1]))


class TestNavigation(unittest.TestCase):
    """Нажатие кнопки правит то же сообщение, а не присылает новое."""

    def setUp(self):
        self.answers, self.edits = [], []
        self._real = (bot.tg_answer_callback, bot.tg_edit_text, bot.tg_send)
        bot.tg_answer_callback = lambda cb_id, text="", alert=False: \
            self.answers.append(text)
        bot.tg_edit_text = lambda chat, mid, text, kb=None: \
            self.edits.append((chat, mid, text, kb))
        bot.tg_send = lambda *a, **kw: None
        self._owner = config.TG_CHAT
        config.TG_CHAT = CHAT
        conn = storage.db()
        for table in ("issues", "feedback", "saved", "subscribers"):
            conn.execute("DELETE FROM %s" % table)
        conn.commit()
        self.ident = storage.save_issue(conn, CHAT, issue(3, 2, 1))
        conn.close()

    def tearDown(self):
        bot.tg_answer_callback, bot.tg_edit_text, bot.tg_send = self._real
        config.TG_CHAT = self._owner

    def press(self, data, chat_id=CHAT):
        bot.handle_update({"update_id": 1, "callback_query": {
            "id": "cb", "data": data,
            "message": {"message_id": 9, "chat": {"id": chat_id}},
        }}, worker=None)

    def test_section_opens_in_place(self):
        self.press("nav:%d:sec:medicine" % self.ident)
        chat, message_id, text, keyboard = self.edits[-1]
        self.assertEqual((chat, message_id), (CHAT, 9))
        self.assertIn("<b>Медицина</b>", text)
        self.assertEqual(keyboard[-1][0]["callback_data"], "nav:%d:home" % self.ident)

    def test_back_returns_to_the_hub(self):
        self.press("nav:%d:sec:medicine" % self.ident)
        self.press("nav:%d:home" % self.ident)
        self.assertIn("ГЛАВНОЕ СЕГОДНЯ", self.edits[-1][2])

    def test_vote_inside_a_section_keeps_navigation(self):
        self.press("nav:%d:sec:ai" % self.ident)
        keyboard = self.edits[-1][3]
        vote = keyboard[0][0]["callback_data"]
        conn = storage.db()
        conn.execute("DELETE FROM items")
        conn.commit()
        conn.close()
        bot.handle_update({"update_id": 2, "callback_query": {
            "id": "cb", "data": vote,
            "message": {"message_id": 9, "chat": {"id": CHAT},
                        "reply_markup": {"inline_keyboard": keyboard}},
        }}, worker=None)
        conn = storage.db()
        verdicts, _saved = feedback.press_state(conn, CHAT)
        conn.close()
        self.assertEqual(len(verdicts), 1)
        # оценка отметилась, а кнопки перехода остались на месте
        self.press("nav:%d:sec:ai" % self.ident)
        keyboard = self.edits[-1][3]
        self.assertTrue(keyboard[0][0]["text"].endswith(render.MARK))
        self.assertEqual(keyboard[-1][0]["callback_data"], "nav:%d:home" % self.ident)

    def test_share_opens_in_place_and_cancel_returns(self):
        self.press("nav:%d:home" % self.ident)
        hub = self.edits[-1][2]
        self.press("nav:%d:share" % self.ident)
        text, keyboard = self.edits[-1][2], self.edits[-1][3]
        self.assertEqual(text, hub)             # текст тот же, сменились кнопки
        self.assertTrue(keyboard[0][0]["url"].startswith("https://t.me/share/url?"))
        # что делать — объясняет всплывашка, а не строка в сообщении
        self.assertEqual(self.answers[-1], issueview.SHARE_HINT)
        self.press(keyboard[-1][0]["callback_data"])
        self.assertIn("ГЛАВНОЕ СЕГОДНЯ", self.edits[-1][2])
        self.assertEqual(self.answers[-1], "")

    def test_section_share_cancels_back_to_the_same_buttons(self):
        self.press("nav:%d:sec:ai" % self.ident)
        section, keyboard = self.edits[-1][2], self.edits[-1][3]
        self.press(keyboard[-1][-1]["callback_data"])       # 📤 Поделиться
        self.assertEqual(self.edits[-1][2], section)
        self.press(self.edits[-1][3][-1][0]["callback_data"])   # ✖️ Отмена
        self.assertEqual(self.edits[-1][3], keyboard)

    def test_message_too_old_to_edit_says_so(self):
        """Telegram не даёт править сообщения старше двух суток."""
        def refuse(chat, mid, text, kb=None):
            raise RuntimeError("Telegram отклонил запрос: 400: message can't be edited")

        bot.tg_edit_text = refuse
        self.press("nav:%d:sec:ai" % self.ident)
        self.assertIn("слишком старый", self.answers[-1])

    def test_old_issue_says_so_instead_of_failing(self):
        self.press("nav:%d:sec:ai" % (self.ident + 500))
        self.assertEqual(self.edits, [])
        self.assertIn("старый", self.answers[-1])

    def test_other_edit_failure_is_not_blamed_on_age(self):
        def refuse(chat, mid, text, kb=None):
            raise RuntimeError("Telegram отклонил запрос: 400: REPLY_MARKUP_TOO_LONG")

        bot.tg_edit_text = refuse
        self.press("nav:%d:share" % self.ident)
        self.assertNotIn("старый", self.answers[-1])

    def test_stranger_gets_nothing(self):
        self.press("nav:%d:sec:ai" % self.ident, chat_id="999")
        self.assertEqual(self.edits, [])


class TestButtonsOff(unittest.TestCase):
    def test_no_reactions_but_navigation_stays(self):
        CFG["feedback_buttons"] = False
        try:
            _text, keyboard = issueview.screen(issue(3, 2), 1, issueview.SEC, "ai")
        finally:
            CFG["feedback_buttons"] = True
        self.assertEqual(len(keyboard), 1)
        self.assertIn("К разделам", keyboard[0][0]["text"])


if __name__ == "__main__":
    unittest.main()

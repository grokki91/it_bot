# -*- coding: utf-8 -*-
"""Интеграция бесплатного маршрута (OpenRouter → DeepSeek) на копии живой базы.

Тесты не видят контракт внешнего API и миграцию `spend` и `shadow` на настоящей
базе. Стоит трёх-четырёх запросов к DeepSeek — меньше $0.001.

    python3 tools/itest_free.py
"""
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import itest  # noqa: E402

#: столько разделов бесплатной модели должны совпасть с DeepSeek
AGREE = 0.8


def main(rep):
    itest.prod_copy()
    from newsdigest import classify, config, llm, sections, spend, storage, translate
    from newsdigest.config import CFG
    from newsdigest.rank import cluster
    config.load_env()
    if not config.OR_KEY or not config.DS_KEY:
        raise itest.Blocked("нет OPENROUTER_API_KEY или DEEPSEEK_API_KEY")
    if not llm.free_models():
        raise itest.Blocked("ND_FREE_MODELS не задан — какие модели проверять, "
                            "подскажет `digest.py doctor`")
    seen = []

    class Grab(logging.Handler):
        def emit(self, record):
            seen.append(record.getMessage())
    config.log.addHandler(Grab())
    conn = storage.db()

    def counters(kind):
        spend.flush()
        row = conn.execute("SELECT SUM(calls) c, SUM(free_calls) f, SUM(fallbacks) b, "
                           "SUM(cost) AS cost FROM spend WHERE kind=?",
                           (kind,)).fetchone()
        return (row["c"] or 0, row["f"] or 0, row["b"] or 0, row["cost"] or 0.0)

    # --- классификация: бесплатно, потом DeepSeek, сверка разделов
    rows = [dict(r) for r in conn.execute(
        "SELECT title, summary FROM items WHERE title != '' "
        "ORDER BY rowid DESC LIMIT 15")]
    if not rows:
        raise itest.Blocked("в базе нет материалов")
    topics = sections.plan()
    before = counters("classify")
    CFG["free_kinds"] = "classify"
    free, _ = classify.ask_model(rows, topics)
    mid = counters("classify")
    CFG["free_kinds"] = ""
    paid, _ = classify.ask_model(rows, topics)
    after = counters("classify")
    rep.metric("classify free_calls", expected=before[1] + 1, actual=mid[1])
    rep.metric("classify calls после бесплатного", expected=before[0], actual=mid[0])
    rep.metric("classify cost после бесплатного", expected=round(before[3], 9),
               actual=round(mid[3], 9))
    rep.metric("classify calls после DeepSeek", expected=mid[0] + 1, actual=after[0])
    rep.metric("classify покрытие бесплатного", expected=len(rows), actual=len(free))
    rep.metric("classify покрытие DeepSeek", expected=len(rows), actual=len(paid))
    same = sum(1 for idx in paid if free.get(idx) == paid[idx])
    rep.lines.append("classify совпало: %d из %d" % (same, len(paid)))
    rep.check(same >= AGREE * max(1, len(paid)),
              "разделы бесплатной модели совпадают с DeepSeek не меньше %d%%"
              % (AGREE * 100))

    # --- перевод: английские заголовки → русские
    english = [r["title"] for r in conn.execute(
        "SELECT title FROM items WHERE title != '' ORDER BY rowid DESC LIMIT 500")
        if translate.foreign(r["title"])][:5]
    if english:
        conn.execute("DELETE FROM translations")   # копия: кэш не мешает спросить
        conn.commit()
        CFG["free_kinds"] = "translate"
        ready, _ = translate.translated(conn, english)
        rep.metric("перевод: стало по-русски", expected=len(english),
                   actual=sum(1 for t in english
                              if translate.improved(t, ready.get(t, ""))))

    # --- сверка: ранжирует DeepSeek, бесплатная отвечает в фоне, в shadow — счётчики
    rep.check(storage.table_exists(conn, "shadow"), "таблица shadow на живой базе")
    CFG.update(free_kinds="", free_shadow="rank", free_shadow_max=10 ** 6)
    llm._free_off.clear()

    def shadowed():
        spend.flush()
        row = conn.execute("SELECT SUM(asks) a, SUM(failed) f, SUM(items) i, "
                           "SUM(agree) g, SUM(risky) r FROM shadow WHERE kind='rank'"
                           ).fetchone()
        return tuple(row[k] or 0 for k in ("a", "f", "i", "g", "r"))
    groups = cluster([dict(r) for r in conn.execute(
        "SELECT * FROM items WHERE title != '' ORDER BY rowid DESC LIMIT 40")],
        CFG["similarity"])[:8]
    before, paid_before = shadowed(), counters("rank")
    ranked, _ = llm.rank_clusters(groups, sections.persona(topics))
    llm._shadow_jobs.join()
    after, paid_after = shadowed(), counters("rank")
    rep.metric("сверка: DeepSeek ответил", expected=paid_before[0] + 1,
               actual=paid_after[0])
    rep.metric("сверка: в free_calls не попала", expected=paid_before[1],
               actual=paid_after[1])
    rep.metric("сверка: запросов", expected=before[0] + 1, actual=after[0])
    if after[1] == before[1]:          # бесплатная ответила годно — сверены все id
        rep.metric("сверка: сверено id", expected=before[2] + sum(
            1 for row in llm._by_id(ranked).values()
            if llm._value("rank", row) is not None), actual=after[2])
    rep.lines.append("rank: сверено %d, совпало %d, опасных %d, брак %d"
                     % (after[2] - before[2], after[3] - before[3],
                        after[4] - before[4], after[1] - before[1]))
    CFG["free_shadow"] = ""

    # --- откат: несуществующая модель → DeepSeek
    CFG["free_kinds"], CFG["free_models"] = "classify", "nonexistent/model-for-itest:free"
    llm._free_off.clear()
    before = counters("classify")
    got, _ = classify.ask_model(rows[:3], topics)
    after = counters("classify")
    rep.metric("откат: fallbacks", expected=before[2] + 1, actual=after[2])
    rep.metric("откат: ответил DeepSeek", expected=before[0] + 1, actual=after[0])
    rep.metric("откат: покрытие", expected=3, actual=len(got))

    rep.check(not any(config.OR_KEY in line for line in seen),
              "ключа OpenRouter нет в логе")
    rep.check(not itest.integrity(conn), "целостность базы")
    conn.close()


if __name__ == "__main__":
    itest.run(main)

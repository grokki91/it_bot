# -*- coding: utf-8 -*-
"""Карточки бесплатной модели рядом с карточками DeepSeek — сравнить глазами.

Карточку автоматически не сверить: это текст, и «совпало» у двух хороших
пересказов не бывает. Поэтому скрипт на копии живой базы пишет карточки пяти
свежих событий дважды — через бесплатную модель и через DeepSeek — и печатает
их рядом. Ничего не отправляет и кэш карточек не трогает. Стоит одного запроса
к DeepSeek — меньше $0.001.

    python3 tools/freecards.py            # пять самых подтверждённых событий
    python3 tools/freecards.py 8          # восемь
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import itest  # noqa: E402


def main(count=5):
    itest.prod_copy()
    from newsdigest import config, llm, pipeline, sections, storage
    from newsdigest.config import CFG
    from newsdigest.rank import cluster, primary_of
    config.load_env()
    if not config.OR_KEY or not config.DS_KEY:
        sys.exit("нет OPENROUTER_API_KEY или DEEPSEEK_API_KEY")
    if not llm.free_models():
        sys.exit("ND_FREE_MODELS не задан — какие модели есть, подскажет "
                 "`digest.py doctor`")
    conn = storage.db()
    groups = cluster(pipeline.fresh_rows(conn), CFG["similarity"])
    conn.close()
    # больше подтверждений — больше голосов в тексте: карточке есть что сводить
    groups.sort(key=lambda g: len({i["source_id"] for i in g}), reverse=True)
    picked = [(group, 0.0, "") for group in groups[:count]]
    if not picked:
        sys.exit("свежих материалов нет")
    persona = sections.persona(sections.plan())
    answers = {}
    for route in ("free", "paid"):
        try:
            answers[route], _usage = llm.summarize_batch(
                picked, persona, CFG["language"], route=route)
        except llm.LLMError as exc:
            answers[route] = {}
            print("%s: %s" % (route, exc))
    for idx, (group, _score, _cat) in enumerate(picked):
        print("\n=== %d. %s" % (idx + 1, primary_of(group)["title"][:100]))
        for route, name in (("free", "бесплатная"), ("paid", "DeepSeek")):
            card = answers[route].get(idx) or {}
            print("--- %s" % name)
            for field in ("headline", "what", "why"):
                print("  %-8s %s" % (field, card.get(field, "—")))


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 5)

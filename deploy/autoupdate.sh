#!/bin/sh
# Автообновление News digest: подтянуть новые коммиты и перезапустить демона.
#
# Запускается таймером systemd (сгенерировать его: python3 digest.py autoupdate).
# Ничего не делает и молчит, если новых коммитов нет — journal от него не растёт.
#
# Настройки (переменные окружения, все необязательные):
#   ND_REPO      каталог с кодом            (по умолчанию — родитель этого файла)
#   ND_BRANCH    ветка                      (main)
#   ND_SERVICE   имя юнита демона           (newsdigest)
#   ND_SELFTEST  1 — прогнать тесты перед перезапуском и откатиться, если упали (0)
set -eu

REPO="${ND_REPO:-$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)}"
BRANCH="${ND_BRANCH:-main}"
SERVICE="${ND_SERVICE:-newsdigest}"
SELFTEST="${ND_SELFTEST:-0}"

say() { echo "[autoupdate] $*"; }
die() { say "$*"; exit 1; }

[ -d "$REPO/.git" ] || die "$REPO — не git-репозиторий, обновлять нечего"

# Каталог с кодом принадлежит обычному пользователю, а таймер работает от root:
# git всегда запускаем от владельца — иначе появятся файлы root'а, а ssh-ключ
# для приватного репозитория лежит в ~/.ssh владельца, не в /root.
OWNER=$(ls -ld "$REPO" | awk '{print $3}')
if [ "$(id -u)" = 0 ] && [ "$OWNER" != root ]; then
    git() { runuser -u "$OWNER" -- git -C "$REPO" "$@"; }
    asowner() { runuser -u "$OWNER" -- "$@"; }
else
    git() { command git -C "$REPO" "$@"; }
    asowner() { "$@"; }
fi

branch=$(git rev-parse --abbrev-ref HEAD)
[ "$branch" = "$BRANCH" ] || die "на сервере ветка $branch, а не $BRANCH — не трогаю"
git diff --quiet && git diff --cached --quiet ||
    die "в рабочем каталоге незакоммиченные правки — не трогаю"

before=$(git rev-parse HEAD)
git fetch --quiet origin "$BRANCH"
after=$(git rev-parse "origin/$BRANCH")
[ "$before" != "$after" ] || exit 0          # нечего делать — молча выходим

changed=$(git diff --name-only "$before" "$after")
say "обновление $(echo "$before" | cut -c1-7) -> $(echo "$after" | cut -c1-7)"
git merge --ff-only "origin/$BRANCH" >/dev/null || die "ff-only мерж не прошёл"

if [ "$SELFTEST" = 1 ]; then
    if ! asowner sh -c "cd '$REPO' && python3 -m unittest discover -s tests" >/dev/null 2>&1; then
        say "тесты упали на $(echo "$after" | cut -c1-7) — откат на $(echo "$before" | cut -c1-7), демон не тронут"
        git reset --hard "$before" >/dev/null
        exit 1
    fi
fi

# README и прочий текст не влияют на работу демона — перезапуск не нужен.
if ! echo "$changed" | grep -qv '\.md$'; then
    say "изменилась только документация — перезапуск не нужен"
    exit 0
fi

if [ "$(id -u)" = 0 ]; then
    systemctl restart "$SERVICE"
elif sudo -n systemctl restart "$SERVICE" 2>/dev/null; then
    :
else
    say "код обновлён, но нет прав на перезапуск: sudo systemctl restart $SERVICE"
    exit 1
fi
say "демон $SERVICE перезапущен на $(echo "$after" | cut -c1-7)"

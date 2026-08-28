## Что меняется

<!-- Одним абзацем: что было не так и что стало. -->

## Как проверено

<!-- Какие тесты гоняли, что смотрели руками. -->

```bash
python3 -m unittest discover -s tests
```

## Секреты

В этом PR — ни в коде, ни в описании, ни в логах и скриншотах — нет токенов,
паролей, ключей API и ссылок с ключами внутри (`?api_key=…`,  <!-- nd-redact: allow -->
`https://user:пароль@host/…`). Вставляемые куски логов прогнаны через:  <!-- nd-redact: allow -->

```bash
python3 digest.py scrub ~/.newsdigest/digest.log > safe.log
python3 digest.py scrub --check safe.log
```

- [ ] Проверил: секретов нет
- [ ] `python3 -m unittest discover -s tests` — зелёные

<!--
Проверка в CI гоняет `digest.py scrub --check` по всем файлам репозитория.
Заведомо игрушечный пример ключа в тесте или документации помечается в той же
строке комментарием `nd-redact: allow`.
-->

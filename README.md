# Kvízy z učebnice – backend v2

Flask API pro rodinnou kvízovou aplikaci. Zachovává dosavadní `quizzes`, `users`
a `results`. Při startu přidá `accounts` a vazbu `results.account_id`.
Historické výsledky zůstanou v databázi a uvidí je administrátor; jména v nich
nejsou automaticky spojena s novými účty.

## Konfigurace Renderu

Ve službě `quiz-backend` → **Environment** nastavte:

| Název | Účel |
| --- | --- |
| `DATABASE_URL` | Stávající spojení do Neon; ponechat beze změny. |
| `QUIZ_SESSION_SECRET` | Nový náhodný řetězec alespoň 32 znaků pro přihlašovací relace. |
| `QUIZ_BOOTSTRAP_ADMIN_USERNAME` | Přihlašovací jméno prvního administrátora. |
| `QUIZ_BOOTSTRAP_ADMIN_PASSWORD` | Heslo prvního administrátora, alespoň 8 znaků (12 nebo více doporučeno). Po prvním úspěšném přihlášení lze proměnnou odstranit. |
| `OPENAI_API_KEY` | Samostatný OpenAI API klíč pro tuto aplikaci. Nikdy ho nevkládat do Netlify ani do Reactu. |
| `FRONTEND_ORIGINS` | Volitelné při změně webové adresy. Výchozí povolené adresy jsou `https://unique-truffle-80425d.netlify.app` a `http://localhost:3000`. Více adres oddělit čárkou. |

Klíče, hesla ani spojovací řetězec nevkládejte do GitHubu, chatu ani snímků.
Původní `QUIZ_PLUGIN_WRITE_TOKEN` lze po vypnutí starého pluginu odebrat.

Stávající Render služba byla vytvořena mimo Blueprint. Změna `render.yaml`
proto sama nemusí změnit její příkaz spuštění. V nastavení služby ověřte:

```text
Build Command: pip install -r requirements.txt
Start Command: gunicorn --bind 0.0.0.0:$PORT --workers 1 --threads 2 --timeout 240 wsgi:app
```

## Bezpečné pořadí nasazení

1. V Neon vytvořte větev ze současné produkční databáze jako zálohu před změnou.
2. Nastavte tajné proměnné na Renderu. Současná adresa Netlify je v konfiguraci již povolena.
3. Nasaďte backend. Zdravotní kontrola `GET /` má vrátit `{"status":"ok"}`.
4. Přihlaste se prvním administrátorským účtem a ověřte, že seznam starých kvízů a historických výsledků zůstal zachován.
5. Nasaďte frontend na Netlify. V případě jiné adresy API nastavte `REACT_APP_API_BASE` v Netlify build environment.
6. Na telefonu ověřte focení i výběr z galerie, vytvořte malý návrh, zkontrolujte citace, zveřejněte a vyplňte kvíz.
7. Přidejte rodinné účty a ověřte, že každý žák vidí jen své výsledky.

Backend a frontend se musí nasadit ve stejném termínu: stará veřejná verze webu
neumí posílat přihlašovací token. Před přepnutím lze vše ověřit na oddělené
větvi databáze a náhledových službách.

## API

Všechny níže uvedené cesty kromě `/`, `/auth/login` a kompatibilní
`/create_plugin_quiz` používají hlavičku `Authorization: Bearer <session-token>`.
JSON chyby mají tvar `{"error":"..."}` a správné odpovědi se žákovi vracejí
až po odevzdání.

| Metoda a cesta | Role | Vstup a výstup |
| --- | --- | --- |
| `GET /` | veřejné | Stav API. |
| `POST /auth/login` | veřejné | `{"username":"...","password":"..."}` → token a účet. |
| `GET /auth/me` | přihlášený | Aktuální účet. |
| `GET /get_all_quizzes` | přihlášený | Seznam kvízů; žák vidí pouze vlastní agregované výsledky. |
| `GET /get_quiz?id=...` | přihlášený | Žák dostane otázky bez `correct` a citací. |
| `POST /submit_answers` | přihlášený | `{"quiz_id":"...","answers":{"0":0,"1":2}}` → skóre, správné odpovědi a citace. |
| `GET /get_results` | přihlášený | Žák vlastní historii; admin veškerou včetně historické. |
| `GET /get_results_by_quiz?quiz_id=...` | přihlášený | Historie pro jeden kvíz se stejným omezením. |
| `GET /get_result?id=...` | přihlášený | Detail vlastního výsledku nebo libovolného pro admina. |
| `POST /admin/generate-quiz` | admin | `multipart/form-data`: `images` (1–10), `audience`, `topic`, `question_count` (1–30). Vrátí návrh, neukládá ho. |
| `POST /create_quiz` | admin | Návrh jako JSON → uložené ID. Každá otázka musí mít citaci a číslo fotografie. |
| `DELETE /delete_quiz?id=...` | admin | Smaže kvíz včetně výsledků. |
| `GET /admin/accounts` | admin | Seznam účtů. |
| `POST /admin/accounts` | admin | `{"username":"...","password":"..."}` → nový žák. |
| `POST /admin/accounts/<id>/password` | admin | `{"password":"..."}` → nové heslo. |
| `POST /create_plugin_quiz` | starý token | Dočasná kompatibilita se starým pluginem; token `QUIZ_PLUGIN_WRITE_TOKEN`. |

Žákovské účty přijímají hesla od 4 znaků, administrátorský účet od 8.
Po 10 chybných pokusech se účet na 15 minut zablokuje.

`questions` má tvar:

```json
{"question":"Text otázky","options":["A","B","C","D"],"correct":0,"source_quote":"Doslovná citace","source_image":"1"}
```

Číslo `correct` může být 0–3. Fotografie se na serveru trvale neukládají.
AI výstup je návrh a citace je nutné před zveřejněním porovnat s učebnicí.

## Lokální ověření

```cmd
python -m pip install -r requirements.txt
python -m unittest -v test_app.py
```

Pro spuštění API jsou navíc nutné lokální environment variables; produkční
hodnoty do lokálních souborů neukládejte.

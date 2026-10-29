#!/usr/bin/env python3
import os
import sys
import glob
import re
import json
import time
import shutil
import tempfile
import urllib.request
import subprocess
import datetime
from concurrent.futures import ThreadPoolExecutor

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "../.."))
VERSION_FILE = os.path.join(REPO_ROOT, "version.txt")
REVISION_FILE = os.path.join(REPO_ROOT, "revision.txt")
CHANGELOG_FILE = os.path.join(REPO_ROOT, "CHANGELOG.md")
LINUX_MODULES_FILE = os.path.join(REPO_ROOT, "linux-modules.txt")
WPS_DIR = "/opt/kingsoft/wps-office/office6"

BASE_URL = "https://global-static.wpscdn.com/office/commons/static/kplugin/global/plugin/wps_intl/addons/pool/win-x64/klangruru_3.1.0.{}.7z"
SCAN_WINDOW = 50
# Сколько последних сборок скачивать для сравнения покрытия модулей
CANDIDATES = 10

APP_GROUPS = {
    "wpp": "WPS Presentation (редактор презентаций)",
    "pdf": "WPS PDF (инструменты работы с PDF)",
    "wps": "WPS Writer (текстовый редактор)",
    "et": "WPS Spreadsheets (электронные таблицы)",
    "wpsoffice": "Главный интерфейс и меню",
    "officespace": "Облачные сервисы и автосохранение",
    "kaccountsdk": "Профиль и авторизация"
}

def check_ver(ver):
    url = BASE_URL.format(ver)
    req = urllib.request.Request(url, method='HEAD')
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False

def module_key(path):
    # addons/<модуль>/mui/ru_RU/*.qm -> addons/<модуль>, ru_RU/<файл>.qm -> mui/<файл>.qm
    parts = path.replace("\\", "/").split("/")
    if not parts[-1].endswith(".qm"):
        return None
    if parts[0] == "addons" and len(parts) > 2:
        return f"addons/{parts[1]}"
    if parts[0] == "ru_RU" and len(parts) == 2:
        return f"mui/{parts[1]}"
    return None

def update_linux_modules():
    addons_dir = os.path.join(WPS_DIR, "addons")
    mui_dir = os.path.join(WPS_DIR, "mui", "zh_CN")
    keys = [f"addons/{d}" for d in sorted(os.listdir(addons_dir)) if os.path.isdir(os.path.join(addons_dir, d))]
    keys += [f"mui/{f}" for f in sorted(os.listdir(mui_dir)) if f.endswith(".qm")]
    with open(LINUX_MODULES_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(keys) + "\n")
    print(f"Saved {len(keys)} modules from {WPS_DIR} to {LINUX_MODULES_FILE}")

def load_linux_modules():
    if not os.path.exists(LINUX_MODULES_FILE):
        return None
    with open(LINUX_MODULES_FILE, "r", encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip()}

def archive_coverage(ver, linux_modules):
    # Сколько модулей Linux-версии WPS переводит данная сборка (-1 при ошибке)
    with tempfile.TemporaryDirectory() as tmp:
        archive = os.path.join(tmp, "pack.7z")
        try:
            urllib.request.urlretrieve(BASE_URL.format(ver), archive)
            res = subprocess.run(["7z", "l", "-slt", archive], capture_output=True, text=True, check=True)
        except Exception as e:
            sys.stderr.write(f"Coverage check error for build {ver}: {e}\n")
            return -1
    keys = {module_key(line[7:]) for line in res.stdout.splitlines() if line.startswith("Path = ")}
    return len(keys & linux_modules)

def extract_qm_strings(filepath):
    try:
        with open(filepath, "rb") as f:
            data = f.read()
    except Exception:
        return set()

    best_strings = set()
    best_count = 0

    # Пробуем декодировать как UTF-16BE со смещением 0 и 1
    for offset in [0, 1]:
        try:
            text = data[offset:].decode("utf-16be", errors="ignore")
            words = re.findall(r"[а-яА-ЯёЁ\s]{3,}", text)
            cleaned = set()
            for w in words:
                w = w.strip()
                w = re.sub(r"^[^\w\s]+|[^\w\s]+$", "", w).strip()
                if len(w) > 3:
                    cleaned.add(w)
            if len(cleaned) > best_count:
                best_count = len(cleaned)
                best_strings = cleaned
        except Exception:
            pass

    # Резервный вариант: пробуем UTF-8
    try:
        text_utf8 = data.decode("utf-8", errors="ignore")
        words_utf8 = re.findall(r"[а-яА-ЯёЁ\s]{3,}", text_utf8)
        cleaned_utf8 = set()
        for w in words_utf8:
            w = w.strip()
            w = re.sub(r"^[^\w\s]+|[^\w\s]+$", "", w).strip()
            if len(w) > 3:
                cleaned_utf8.add(w)
        if len(cleaned_utf8) > best_count:
            best_strings = cleaned_utf8
    except Exception:
        pass

    return best_strings

def set_github_output(name, value):
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as f:
            f.write(f"{name}={value}\n")

def main():
    if not os.path.exists(VERSION_FILE):
        print(f"Error: {VERSION_FILE} not found.")
        sys.exit(1)

    with open(VERSION_FILE, "r", encoding="utf-8") as f:
        current_build = f.read().strip()

    current_rev = "1"
    if os.path.exists(REVISION_FILE):
        with open(REVISION_FILE, "r", encoding="utf-8") as f:
            current_rev = f.read().strip() or "1"

    print(f"Current version in version.txt: {current_build}, revision: {current_rev}")

    dry_run = False
    target_build_arg = None
    for arg in sys.argv[1:]:
        if arg == "--dry-run":
            dry_run = True
        elif arg == "--update-modules":
            update_linux_modules()
            sys.exit(0)
        else:
            target_build_arg = arg

    if target_build_arg:
        latest_build = str(target_build_arg)
        print(f"Target build specified via CLI argument: {latest_build}")
    else:
        # В нумерации сборок бывают пропуски (например, 3322 нет, а 3330 есть),
        # поэтому проверяем окнами по SCAN_WINDOW номеров и останавливаемся,
        # только когда целое окно пустое.
        latest_build = int(current_build)
        all_found = []
        with ThreadPoolExecutor(max_workers=16) as pool:
            while True:
                window = list(range(latest_build + 1, latest_build + 1 + SCAN_WINDOW))
                found = [v for v, ok in zip(window, pool.map(check_ver, window)) if ok]
                if not found:
                    break
                all_found += found
                latest_build = max(found)

        # В один пул параллельно выкладываются сборки из нескольких веток с разным
        # набором модулей, поэтому максимальный номер — случайная ветка. Берём
        # сборку, которая переводит больше всего модулей Linux-версии WPS, а при
        # равенстве — самую новую. Текущая сборка участвует в сравнении, чтобы
        # не откатиться на более бедную ветку.
        linux_modules = load_linux_modules()
        if all_found and linux_modules:
            candidates = all_found[-CANDIDATES:] + [int(current_build)]
            with ThreadPoolExecutor(max_workers=4) as pool:
                scores = dict(zip(candidates, pool.map(lambda v: archive_coverage(v, linux_modules), candidates)))
            for v in sorted(scores):
                print(f"Build {v}: covers {scores[v]} of {len(linux_modules)} Linux modules")
            latest_build = max(scores, key=lambda v: (scores[v], v))
        elif all_found:
            print(f"Warning: {LINUX_MODULES_FILE} not found, taking the highest build number.")
        latest_build = str(latest_build)

    # Проверяем, менялись ли любые файлы пакета в Git (кроме служебных мета-файлов)
    IGNORE_META = {"version.txt", "revision.txt", "CHANGELOG.md", "linux-modules.txt"}
    pkg_files_changed = False
    modified_pkg_files = []
    try:
        res = subprocess.run(["git", "diff", "HEAD~1", "HEAD", "--name-only"], capture_output=True, text=True)
        mod_files = res.stdout.splitlines()
        for mf in mod_files:
            if mf not in IGNORE_META and not mf.startswith("dist/"):
                pkg_files_changed = True
                modified_pkg_files.append(mf)
                print(f"Detected modification in package file: {mf}")
    except Exception as e:
        sys.stderr.write(f"Git diff check error: {e}\n")

    git_diff_text = ""
    if modified_pkg_files:
        try:
            diff_res = subprocess.run(["git", "diff", "HEAD~1", "HEAD", "--"] + modified_pkg_files, capture_output=True, text=True)
            git_diff_text = diff_res.stdout
        except Exception as e:
            sys.stderr.write(f"Git diff content fetch error: {e}\n")

    if latest_build == current_build and not pkg_files_changed:
        print(f"No changes found. Current version {current_build}-{current_rev} is latest.")
        set_github_output("updated", "false")
        sys.exit(0)

    if latest_build != current_build:
        next_rev = "1"
        print(f"NEW UPSTREAM VERSION FOUND: {latest_build} (was {current_build}). Resetting revision to 1.")
    else:
        next_rev = str(int(current_rev) + 1)
        print(f"Packaging files updated for build {latest_build}. Bumping package revision to {next_rev}.")

    old_url = BASE_URL.format(current_build)
    new_url = BASE_URL.format(latest_build)

    tmp_dir = tempfile.mkdtemp()
    try:
        old_archive = os.path.join(tmp_dir, "old.7z")
        new_archive = os.path.join(tmp_dir, "new.7z")

        print("Downloading old and new archives for diff analysis...")
        os.system(f"wget -q --no-check-certificate -O '{old_archive}' '{old_url}'")
        os.system(f"wget -q --no-check-certificate -O '{new_archive}' '{new_url}'")

        res = subprocess.run(["7z", "t", new_archive], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if res.returncode != 0:
            print("Error: New archive is invalid or corrupt.")
            sys.exit(1)

        old_extracted = os.path.join(tmp_dir, "old_extracted")
        new_extracted = os.path.join(tmp_dir, "new_extracted")
        os.makedirs(old_extracted, exist_ok=True)
        os.makedirs(new_extracted, exist_ok=True)

        if os.path.exists(old_archive) and os.path.getsize(old_archive) > 0:
            subprocess.run(["7z", "x", old_archive, f"-o{old_extracted}", "-y"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["7z", "x", new_archive, f"-o{new_extracted}", "-y"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        if not os.path.exists(os.path.join(new_extracted, "ru_RU/wps.qm")):
            print("Error: ru_RU/wps.qm missing in new archive.")
            sys.exit(1)

        latest_mtime = 0
        for root, _, files in os.walk(new_extracted):
            for file in files:
                try:
                    m = os.path.getmtime(os.path.join(root, file))
                    if m > latest_mtime:
                        latest_mtime = m
                except:
                    pass

        if latest_mtime > 0:
            build_date = datetime.datetime.fromtimestamp(latest_mtime, datetime.timezone.utc).strftime("%Y-%m-%d")
        else:
            build_date = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")

        deb_version = f"12.3.1.0.{latest_build}-{next_rev}"

        old_files = {os.path.relpath(p, old_extracted) for p in glob.glob(old_extracted + "/**", recursive=True) if os.path.isfile(p)}
        new_files = {os.path.relpath(p, new_extracted) for p in glob.glob(new_extracted + "/**", recursive=True) if os.path.isfile(p)}

        added = sorted(list(new_files - old_files))
        common = sorted(list(new_files & old_files))

        changed_apps = set()
        changed_apps_info = []
        for f in common:
            old_f_path = os.path.join(old_extracted, f)
            new_f_path = os.path.join(new_extracted, f)
            if os.path.getsize(old_f_path) != os.path.getsize(new_f_path):
                base_mod = os.path.basename(f).replace(".qm", "").lower()
                if base_mod in APP_GROUPS:
                    app_name = APP_GROUPS[base_mod]
                    changed_apps.add(app_name)
                    
                    old_strs = extract_qm_strings(old_f_path)
                    new_strs = extract_qm_strings(new_f_path)
                    added_strs = sorted(list(new_strs - old_strs))
                    
                    if added_strs:
                        sample = added_strs[:15]
                        if len(added_strs) > 15:
                            sample_str = ", ".join(sample) + f" и ещё {len(added_strs) - 15} переведенных фраз"
                        else:
                            sample_str = ", ".join(sample)
                        changed_apps_info.append(f"- **{app_name}**: Добавлены/изменены переводы для: {sample_str}")
                    else:
                        changed_apps_info.append(f"- **{app_name}**: Обновлен перевод интерфейса")

        human_changed_list = sorted(list(changed_apps))

        raw_summary_parts = []
        if changed_apps_info:
            raw_summary_parts.append("Обновлен перевод в приложениях:\n" + "\n".join(changed_apps_info))
        elif human_changed_list:
            raw_summary_parts.append(f"Обновлен перевод в приложениях: {', '.join(human_changed_list)}")
        
        templates_added = [f for f in added if f.endswith((".wps", ".et", ".dps", ".docx", ".xlsx", ".pptx", ".rcc"))]
        if len(templates_added) > 0:
            raw_summary_parts.append(f"Добавлено {len(templates_added)} новых файлов графических шаблонов/диаграмм")

        raw_diff_text = "\n".join(raw_summary_parts)

        is_new_upstream = (latest_build != current_build)
        is_revision_bump = (latest_build == current_build and pkg_files_changed)

        raw_key = os.environ.get("GEMINI_API_KEY", "")
        api_key = raw_key.replace('"', '').replace("'", "").strip()

        ai_generated = False
        new_entry_content = ""

        if api_key:
            if is_revision_bump:
                prompt = f"""Ты — технический редактор языкового пакета Linux wps-office-langpack-ru. Сформируй краткий и полезный Changelog на русском языке для релиза версии {deb_version}.

В этом релизе обновлены **настройки интеграции и установки пакета** (автоматика установки, шрифты, настройки профиля), а сам перевод остается версии {latest_build}.

ТРЕБОВАНИЯ:
- Пиши только полезные факты ДЛЯ КОНЕЧНОГО ПОЛЬЗОВАТЕЛЯ.
- НЕ ВЫДУМЫВАЙ никакие улучшения и изменения, которых нет в ДАННЫХ ИЗМЕНЕНИЙ (GIT DIFF) ниже. Если в DIFF изменены только пути, названия файлов или пара строк, опиши строго только это.
- КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО писать про ИИ-помощника, ИИ-функции (AI assistant, Copilot, ИИ-ассистент), так как эти функции актуальны только для Windows-версии, а пакет собирается для Linux.
- КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО писать технические слова разработчиков ("build.sh", "postinst", "git diff", "dpkg", "скрипт сборки"). Пользователю они не нужны.
- НЕ ПИШИ воду и хвалебные слова.
- Длина: 2-3 коротких и понятных пункта.

ДАННЫЕ ИЗМЕНЕНИЙ (GIT DIFF):
{git_diff_text}

Формат каждого пункта: * **Компонент**: Сухое и понятное описание пользы.
Выведи ТОЛЬКО маркированный список изменений в формате Markdown."""
            elif is_new_upstream and pkg_files_changed:
                prompt = f"""Ты — технический редактор языкового пакета Linux wps-office-langpack-ru. Сформируй краткий и полезный Changelog на русском языке для релиза версии {deb_version} (новый билд перевода {latest_build}, был {current_build}).

В этом релизе вышли НОВЫЙ перевод от разработчиков Kingsoft и обновления в скриптах установки/настройки пакета!

ТРЕБОВАНИЯ:
- Опиши кратко обновления перевода интерфейса/шаблонов и полезные улучшения в установке/настройке пакета для пользователя.
- НЕ ВЫДУМЫВАЙ изменения в установке/настройке пакета, которых нет в ДАННЫХ ИЗМЕНЕНИЙ (GIT DIFF) ниже.
- КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО писать про ИИ-помощника, ИИ-функции (AI assistant, Copilot, ИИ-ассистент), так как эти функции актуальны только для Windows-версии, а пакет собирается для Linux.
- КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО писать технические слова разработчиков ("build.sh", "postinst", "git diff", "dpkg", "скрипт сборки").
- НЕ ПИШИ воду и хвалебные слова.

ДАННЫЕ ПО ПЕРЕВОДУ:
{raw_diff_text}

ДАННЫЕ ИЗМЕНЕНИЙ СИСТЕМНОГО ПАКЕТА (GIT DIFF):
{git_diff_text}

Формат каждого пункта: * **Компонент**: Сухое и понятное описание.
Выведи ТОЛЬКО маркированный список изменений в формате Markdown."""
            else:
                prompt = f"""Ты — технический редактор языковых пакетов Linux. Сформируй краткий и точный Changelog на русском языке для релиза языкового пакета WPS Office {deb_version} (билд {latest_build}, был {current_build}).

ВАЖНОЕ ПРАВИЛО: ЭТО ЯЗЫКОВОЙ ПАКЕТ (LANGPACK). Здесь НЕТ программного или исполняемого кода C++! Пиши ТОЛЬКО про **перевод интерфейса, подсказки меню и локализованные шаблоны**.

ТРЕБОВАНИЯ:
- Пиши только короткие факты про обновление перевода и шаблонов (3-4 пункта).
- НЕ ВЫДУМЫВАЙ никакие детали о том, что именно поменялось внутри перевода (например, "исправлен перевод меню рецензирования"), если этих деталей нет в ДАННЫХ. Ограничься общими фразами: "Обновлен перевод элементов интерфейса и меню для WPS Writer" и т.д.
- КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО писать про ИИ-помощника, ИИ-функции (AI assistant, Copilot, ИИ-ассистент), так как эти функции актуальны только для Windows-версии, а пакет собирается для Linux.
- КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО писать "обновлен программный код", "библиотеки", "исполняемый модуль".
- НЕ ПИШИ воду и хвалебные слова.

ДАННЫЕ:
{raw_diff_text}

Выведи ТОЛЬКО маркированный список изменений в формате Markdown."""

            endpoints = [
                "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent",
                "https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent",
                "https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-lite-latest:generateContent"
            ]
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.2}
            }

            for base_url in endpoints:
                try:
                    headers = {
                        "Content-Type": "application/json",
                        "X-goog-api-key": api_key
                    }
                    req_obj = urllib.request.Request(base_url, data=json.dumps(payload).encode("utf-8"), headers=headers)
                    with urllib.request.urlopen(req_obj, timeout=60) as resp:
                        data = json.loads(resp.read().decode("utf-8"))
                        new_entry_content = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                        if new_entry_content:
                            ai_generated = True
                            break
                except Exception as e:
                    sys.stderr.write(f"Gemini API error ({base_url}): {e}\n")
                    time.sleep(1)

        if not ai_generated:
            lines = []
            if is_revision_bump:
                lines.append("* **Интеграция и автоматизация**: Улучшена автоматическая настройка русского языка при установке пакета и очистка при удалении.")
            else:
                if human_changed_list:
                    lines.append("- **Обновлен перевод интерфейса и меню**:")
                    for app in human_changed_list:
                        lines.append(f"  - {app}")
                if len(added) > 0:
                    lines.append(f"- **Добавлено новых русскоязычных шаблонов**: {len(added)} файлов")
            new_entry_content = "\n".join(lines)

        new_entry_content = re.sub(r'^\s*#+\s+[^\n]*\n*', '', new_entry_content).strip()
        new_block = f"## [{deb_version}] - {build_date}\n\n{new_entry_content}"

        header = "# История изменений (Changelog)\n\n"
        existing_content = ""

        if os.path.exists(CHANGELOG_FILE):
            with open(CHANGELOG_FILE, "r", encoding="utf-8") as f:
                existing_content = f.read()

        existing_content = re.sub(r'^\s*#\s+[^\n]*\n*', '', existing_content).strip()

        if existing_content:
            full_changelog = header + new_block + "\n\n" + existing_content + "\n"
        else:
            full_changelog = header + new_block + "\n"

        if dry_run:
            print("=== DRY RUN (NO FILES MODIFIED) ===")
            print(f"Proposed debian version: {deb_version}")
            print(f"Proposed build date: {build_date}")
            print("\n--- RAW INPUT DATA PASSED TO GEMINI ---")
            print(f"RAW DIFF TEXT (QM changes):\n{raw_diff_text}")
            print(f"GIT DIFF TEXT (Packaging changes):\n{git_diff_text}")
            print("---------------------------------------\n")
            print("Generated Changelog entry:")
            print(new_block)
            print("===================================")
        else:
            with open(CHANGELOG_FILE, "w", encoding="utf-8") as f:
                f.write(full_changelog)

            with open(VERSION_FILE, "w", encoding="utf-8") as f:
                f.write(f"{latest_build}\n")

            with open(REVISION_FILE, "w", encoding="utf-8") as f:
                f.write(f"{next_rev}\n")

            print(f"Version bumped to {latest_build}, CHANGELOG.md updated.")
            set_github_output("updated", "true")
            set_github_output("version", deb_version)
            set_github_output("build_num", latest_build)

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

if __name__ == "__main__":
    main()

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

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "../.."))
VERSION_FILE = os.path.join(REPO_ROOT, "version.txt")
CHANGELOG_FILE = os.path.join(REPO_ROOT, "CHANGELOG.md")

BASE_URL = "https://global-static.wpscdn.com/office/commons/static/kplugin/global/plugin/wps_intl/addons/pool/win-x64/klangruru_3.1.0.{}.7z"

APP_GROUPS = {
    "kaiwpp": "WPS Presentation (ИИ-ассистент)",
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

def extract_strings(filepath):
    strings = set()
    try:
        with open(filepath, "rb") as f:
            data = f.read()
        utf8_str = re.findall(rb"[\x20-\x7e\xd0-\xd1][\x80-\xbf\x20-\x7e]{3,}", data)
        for s in utf8_str:
            try:
                decoded = s.decode("utf-8").strip()
                decoded = re.sub(r'^[^\w]+', '', decoded)
                if len(decoded) > 3 and not decoded.startswith("http"):
                    strings.add(decoded)
            except:
                pass
    except:
        pass
    return strings

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

    print(f"Current version in version.txt: {current_build}")

    target_build_arg = sys.argv[1] if len(sys.argv) > 1 else None

    if target_build_arg:
        latest_build = str(target_build_arg)
        print(f"Target build specified via CLI argument: {latest_build}")
    else:
        current_int = int(current_build)
        latest_build = current_int
        if check_ver(current_int):
            step = 1
            curr = current_int
            while True:
                next_v = curr + step
                if check_ver(next_v):
                    latest_build = next_v
                    curr = next_v
                    step *= 2
                else:
                    first_inv = next_v
                    break

            low = latest_build + 1
            high = first_inv - 1
            while low <= high:
                mid = (low + high) // 2
                if check_ver(mid):
                    latest_build = mid
                    low = mid + 1
                else:
                    high = mid - 1
        latest_build = str(latest_build)

    if latest_build == current_build:
        print(f"No new version found. Current version {current_build} is latest.")
        set_github_output("updated", "false")
        sys.exit(0)

    print(f"NEW VERSION FOUND: {latest_build} (was {current_build})")

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

        if os.path.exists(old_archive):
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

        deb_version = f"12.3.1.0.{latest_build}"

        old_files = {os.path.relpath(p, old_extracted) for p in glob.glob(old_extracted + "/**", recursive=True) if os.path.isfile(p)}
        new_files = {os.path.relpath(p, new_extracted) for p in glob.glob(new_extracted + "/**", recursive=True) if os.path.isfile(p)}

        added = sorted(list(new_files - old_files))
        common = sorted(list(new_files & old_files))

        changed_apps = set()
        for f in common:
            if os.path.getsize(os.path.join(old_extracted, f)) != os.path.getsize(os.path.join(new_extracted, f)):
                base_mod = os.path.basename(f).replace(".qm", "").lower()
                if base_mod in APP_GROUPS:
                    changed_apps.add(APP_GROUPS[base_mod])

        human_changed_list = sorted(list(changed_apps))

        raw_summary_parts = []
        if human_changed_list:
            raw_summary_parts.append(f"Обновлен перевод в приложениях: {', '.join(human_changed_list)}")
        if len(added) > 0:
            raw_summary_parts.append(f"Добавлено {len(added)} новых файлов графических шаблонов/диаграмм")

        raw_diff_text = ". ".join(raw_summary_parts)

        raw_key = os.environ.get("GEMINI_API_KEY", "")
        api_key = raw_key.replace('"', '').replace("'", "").strip()

        ai_generated = False
        new_entry_content = ""

        if api_key and raw_diff_text.strip():
            prompt = f"""Ты — технический редактор языковых пакетов Linux. Сформируй краткий и точный Changelog на русском языке для релиза языкового пакета WPS Office {deb_version} (ревизия {latest_build}, была {current_build}).

ВАЖНОЕ ПРАВИЛО: ЭТО ЯЗЫКОВОЙ ПАКЕТ (LANGPACK). Здесь НЕТ программного или исполняемого кода C++! Пиши ТОЛЬКО про **перевод интерфейса, подсказки меню и локализованные шаблоны**.

КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО:
- Писать "обновлен программный код", "библиотеки", "исполняемый модуль".
- Писать воду и хвалебные слова ("комфортный", "интуитивный", "всестороннее").
- Писать про добавление 0 файлов.

ТРЕБОВАНИЯ:
- Пиши только короткие факты про обновление перевода и шаблонов (3-4 пункта).
- Формат каждого пункта: * **Приложение/Компонент**: Сухое описание обновления перевода.

ДАННЫЕ:
{raw_diff_text}

Выведи ТОЛЬКО маркированный список изменений в формате Markdown."""

            endpoints = [
                "https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent",
                "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent",
                "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent"
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
                    with urllib.request.urlopen(req_obj, timeout=25) as resp:
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

        with open(CHANGELOG_FILE, "w", encoding="utf-8") as f:
            f.write(full_changelog)

        with open(VERSION_FILE, "w", encoding="utf-8") as f:
            f.write(f"{latest_build}\n")

        print(f"Version bumped to {latest_build}, CHANGELOG.md updated.")
        set_github_output("updated", "true")
        set_github_output("version", deb_version)
        set_github_output("build_num", latest_build)

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

if __name__ == "__main__":
    main()

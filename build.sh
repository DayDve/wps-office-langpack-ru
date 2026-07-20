#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSION_FILE="$SCRIPT_DIR/version.txt"

if [ -n "$1" ]; then
    BUILD_NUM="$1"
elif [ -f "$VERSION_FILE" ]; then
    BUILD_NUM="$(tr -d '[:space:]' < "$VERSION_FILE")"
else
    echo "Ошибка: не найден version.txt и не передана версия."
    exit 1
fi

LANGPACK_URL="https://global-static.wpscdn.com/office/commons/static/kplugin/global/plugin/wps_intl/addons/pool/win-x64/klangruru_3.1.0.${BUILD_NUM}.7z"
RAW_VER="3.1.0.${BUILD_NUM}"
DEB_VERSION="12.${RAW_VER}"

echo "=== Сборка пакета wps-office-langpack-ru ==="
echo "Билд языкового пакета: $BUILD_NUM"
echo "Версия DEB-пакета: $DEB_VERSION"
echo "URL: $LANGPACK_URL"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

ARCHIVE_NAME="$(basename "$LANGPACK_URL")"

echo "=== 1. Скачивание архива ==="
wget -q --show-progress --no-check-certificate -O "$TMP_DIR/$ARCHIVE_NAME" "$LANGPACK_URL"

echo "=== 2. Распаковка архива ==="
EXTRACT_DIR="$TMP_DIR/extracted"
mkdir -p "$EXTRACT_DIR"
7z x "$TMP_DIR/$ARCHIVE_NAME" -o"$EXTRACT_DIR" -y > /dev/null

echo "=== 3. Подготовка структуры пакета ==="
PKG_DIR="$TMP_DIR/pkg"
DIST_DIR="$SCRIPT_DIR/dist"

mkdir -p "$PKG_DIR" "$DIST_DIR"

# Копируем готовые системные каталоги и скрипты из репозитория
cp -r "$SCRIPT_DIR/DEBIAN" "$PKG_DIR/"
cp -r "$SCRIPT_DIR/etc" "$PKG_DIR/"

# Генерируем control из шаблона с подстановкой версии
sed "s/__VERSION__/$DEB_VERSION/g" "$SCRIPT_DIR/DEBIAN/control.template" > "$PKG_DIR/DEBIAN/control"
rm -f "$PKG_DIR/DEBIAN/control.template"

# Копируем распакованные файлы перевода
mkdir -p "$PKG_DIR/opt/kingsoft/wps-office/office6/mui"
mkdir -p "$PKG_DIR/opt/kingsoft/wps-office/office6/addons"

if [ -d "$EXTRACT_DIR/ru_RU" ]; then
    cp -a "$EXTRACT_DIR/ru_RU" "$PKG_DIR/opt/kingsoft/wps-office/office6/mui/"
fi

if [ -d "$EXTRACT_DIR/addons" ]; then
    cp -a "$EXTRACT_DIR/addons/." "$PKG_DIR/opt/kingsoft/wps-office/office6/addons/"
fi

chmod 755 "$PKG_DIR/DEBIAN/postinst" "$PKG_DIR/DEBIAN/postrm"

DEB_FILE="$DIST_DIR/wps-office-langpack-ru_${DEB_VERSION}_all.deb"

echo "=== 4. Сборка DEB-пакета ==="
dpkg-deb --build "$PKG_DIR" "$DEB_FILE"

echo "=== ГОТОВО! Пакет собран: $DEB_FILE ==="

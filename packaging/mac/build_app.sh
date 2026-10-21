#!/bin/bash
# Construit « Genshin Overlay.app » et « GenshinOverlay-<version>.dmg » (sans aucune donnée personnelle).
#   packaging/mac/build_app.sh CODE_DIR MAP_DATA_DIR ICON.icns OUT_DIR
#   CODE_DIR : la racine du dépôt ; MAP_DATA_DIR : un dossier « maps » (python fetch_data.py le crée
#   dans data/maps) ; ICON : packaging/mac/AppIcon.icns.
#   Sur macOS, l'image disque est faite avec hdiutil ; sous Linux avec genisoimage + libdmg-hfsplus
#   (variable DMG_TOOL = chemin de l'outil « dmg »).
set -e
CODE="$1"; MAPS="$2"; ICON="$3"; OUT="$4"
HERE="$(cd "$(dirname "$0")" && pwd)"
VER="$(cat "$CODE/VERSION")"
DIST="$OUT/dist"
APP="$DIST/Genshin Overlay.app"
rm -rf "$DIST"; mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/app/data"
cp "$HERE/launcher" "$APP/Contents/MacOS/launcher"
chmod 755 "$APP/Contents/MacOS/launcher"
cp "$ICON" "$APP/Contents/Resources/AppIcon.icns"
R="$APP/Contents/Resources/app"
cp -r "$CODE/gioverlay" "$R/"
cp "$CODE/run.py" "$CODE/requirements.txt" "$CODE/VERSION" "$CODE/channel.json" "$R/"
find "$R" -name __pycache__ -prune -exec rm -rf {} +
cp "$HERE/setup.command" "$R/setup.command"; chmod 755 "$R/setup.command"
cp -r "$MAPS" "$R/data/maps"
rm -f "$R"/data/maps/*/progress.json      # jamais la progression de quelqu'un
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>CFBundleDisplayName</key>
	<string>Genshin Overlay</string>
	<key>CFBundleExecutable</key>
	<string>launcher</string>
	<key>CFBundleIconFile</key>
	<string>AppIcon</string>
	<key>CFBundleIdentifier</key>
	<string>fr.armand.genshinoverlay.app</string>
	<key>CFBundleName</key>
	<string>Genshin Overlay</string>
	<key>CFBundlePackageType</key>
	<string>APPL</string>
	<key>CFBundleShortVersionString</key>
	<string>$VER</string>
	<key>CFBundleVersion</key>
	<string>$VER</string>
	<key>LSUIElement</key>
	<true/>
	<key>LSMinimumSystemVersion</key>
	<string>12.0</string>
	<key>LSApplicationCategoryType</key>
	<string>public.app-category.games</string>
	<key>NSHighResolutionCapable</key>
	<true/>
	<key>NSScreenCaptureUsageDescription</key>
	<string>Genshin Overlay lit la mini-map du jeu pour afficher les coffres et les défis.</string>
</dict>
</plist>
PLIST
printf 'APPL????' > "$APP/Contents/PkgInfo"
ln -s /Applications "$DIST/Applications"
cp "$HERE/LISEZ-MOI.txt" "$DIST/LISEZ-MOI.txt"
# Image disque
if command -v hdiutil >/dev/null 2>&1; then
  rm -f "$OUT/GenshinOverlay-$VER.dmg"
  hdiutil create -volname "Genshin Overlay" -srcfolder "$DIST" -ov -format UDZO "$OUT/GenshinOverlay-$VER.dmg" >/dev/null
else
  # ISO 9660 + Rock Ridge (droits d'exécution, lien « Applications »), compressée en .dmg
  genisoimage -quiet -D -l -V "Genshin Overlay" -no-pad -r -dir-mode 0755 -o "$OUT/temp.dmg" "$DIST"
  "${DMG_TOOL:-$HERE/tools/dmg}" "$OUT/temp.dmg" "$OUT/GenshinOverlay-$VER.dmg" >/dev/null
  rm -f "$OUT/temp.dmg"
fi
ls -la "$OUT/GenshinOverlay-$VER.dmg"

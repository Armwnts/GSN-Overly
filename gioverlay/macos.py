"""Réglages spécifiques à macOS (via PyObjC). Tout est optionnel : sans PyObjC
ou sur un autre système, ces fonctions ne font rien."""
from __future__ import annotations

import sys

IS_MAC = sys.platform == "darwin"


def _nswindow(widget):
    import objc
    view = objc.objc_object(c_void_p=int(widget.winId()))
    return view.window()


def _ensure_ui_element_bundle() -> None:
    """Ajoute LSUIElement à l'Info.plist de l'application (comme la Beta) : macOS la lance alors
    d'emblée sans icône dans le Dock, condition pour s'afficher au-dessus d'un jeu en plein écran.
    Prend effet au lancement suivant."""
    import os
    import plistlib
    import subprocess
    from pathlib import Path
    res = os.environ.get("GIO_APP_RES")          # …/Genshin Overlay.app/Contents/Resources/app
    if not res:
        return
    contents = Path(res).parent.parent
    plist = contents / "Info.plist"
    try:
        info = plistlib.loads(plist.read_bytes())
        if info.get("LSUIElement") in (True, "1", 1):
            return
        info["LSUIElement"] = True
        plist.write_bytes(plistlib.dumps(info))
        app = contents.parent
        os.utime(app, None)
        lsreg = ("/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework"
                 "/Support/lsregister")
        if os.path.exists(lsreg):
            subprocess.Popen([lsreg, "-f", str(app)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print("[macOS] LSUIElement ajouté à l'application")
    except Exception as e:  # pragma: no cover
        print(f"[macOS] Info.plist inchangé : {e}")


def _transform_to_ui_element() -> None:
    """Équivalent immédiat de LSUIElement pour le processus en cours."""
    import ctypes
    import ctypes.util

    class PSN(ctypes.Structure):
        _fields_ = [("high", ctypes.c_uint32), ("low", ctypes.c_uint32)]
    try:
        lib = ctypes.cdll.LoadLibrary(ctypes.util.find_library("ApplicationServices"))
        psn = PSN(0, 2)                                   # kCurrentProcess
        lib.TransformProcessType(ctypes.byref(psn), ctypes.c_uint32(4))   # → UIElement
    except Exception as e:  # pragma: no cover
        print(f"[macOS] TransformProcessType : {e}")


def set_accessory_app() -> None:
    """Application « accessoire » (pas d'icône dans le Dock) : condition pour que
    nos fenêtres puissent s'afficher au-dessus d'un jeu en plein écran."""
    if not IS_MAC:
        return
    _ensure_ui_element_bundle()
    _transform_to_ui_element()
    try:
        from AppKit import NSApp, NSApplication, NSApplicationActivationPolicyAccessory
        app = NSApp() or NSApplication.sharedApplication()
        app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    except Exception as e:  # pragma: no cover - dépend de l'OS
        print(f"[macOS] mode accessoire indisponible : {e}")


_dock = False   # l'application a-t-elle une icône dans le Dock ?


def _regular_policy():
    from AppKit import NSApplicationActivationPolicyRegular
    return NSApplicationActivationPolicyRegular


class _AsAccessory:
    """macOS n'autorise une fenêtre au-dessus d'un jeu en plein écran que pour les applications
    « accessoires » (sans icône dans le Dock). Avec l'icône dans le Dock, on repasse en accessoire
    le temps de régler la fenêtre, puis on remet l'icône : la fenêtre garde son réglage."""

    def __enter__(self):
        if _dock:
            from AppKit import NSApp, NSApplicationActivationPolicyAccessory
            NSApp().setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    def __exit__(self, *a):
        if _dock:
            from AppKit import NSApp
            NSApp().setActivationPolicy_(_regular_policy())
        return False


def set_app_identity(name: str = "Genshin Overlay"):
    """Nom affiché par macOS (menu, Dock) : le programme est lancé par Python, qui s'appellerait « Python »."""
    if not IS_MAC:
        return
    try:
        from Foundation import NSBundle
        b = NSBundle.mainBundle()
        info = b.localizedInfoDictionary() or b.infoDictionary()
        if info is not None:
            info["CFBundleName"] = name
            info["CFBundleDisplayName"] = name
    except Exception as e:  # pragma: no cover
        print(f"[macOS] nom de l'application inchangé : {e}")


def show_dock_icon(on: bool, icon_path: str | None = None) -> None:
    """Désactivé : avec une icône dans le Dock, macOS cache l'overlay derrière le jeu en plein écran."""
    global _dock
    if not IS_MAC or on:
        return
    try:
        from AppKit import NSApp, NSApplicationActivationPolicyAccessory, NSImage
        if on:
            NSApp().setActivationPolicy_(_regular_policy())
            if icon_path:
                img = NSImage.alloc().initWithContentsOfFile_(icon_path)
                if img is not None:
                    NSApp().setApplicationIconImage_(img)
        else:
            NSApp().setActivationPolicy_(NSApplicationActivationPolicyAccessory)
        _dock = on
    except Exception as e:  # pragma: no cover
        print(f"[macOS] icône du Dock : {e}")


def make_overlay_window(widget, click_through: bool = True) -> None:
    """Fenêtre visible sur tous les bureaux, y compris au-dessus du plein écran,
    exclue des captures d'écran (pour ne pas se détecter elle-même)."""
    if not IS_MAC:
        return
    try:
        from AppKit import (NSScreenSaverWindowLevel, NSWindowCollectionBehaviorCanJoinAllSpaces,
                            NSWindowCollectionBehaviorFullScreenAuxiliary,
                            NSWindowCollectionBehaviorStationary, NSWindowSharingNone)
        win = _nswindow(widget)
        if not getattr(widget, "_gio_spaces", False):
            with _AsAccessory():
                win.setCollectionBehavior_(NSWindowCollectionBehaviorCanJoinAllSpaces
                                           | NSWindowCollectionBehaviorFullScreenAuxiliary
                                           | NSWindowCollectionBehaviorStationary)
            widget._gio_spaces = True
        win.setLevel_(NSScreenSaverWindowLevel)
        win.setSharingType_(NSWindowSharingNone)
        win.setHidesOnDeactivate_(False)  # rester visible quand le jeu a le focus
        win.setIgnoresMouseEvents_(click_through)
        win.setHasShadow_(False)
    except Exception as e:  # pragma: no cover
        print(f"[macOS] réglage de la fenêtre impossible : {e}")


def activate_app() -> None:
    """Met l'application au premier plan (utile car elle n'a pas d'icône dans le Dock)."""
    if not IS_MAC:
        return
    try:
        from AppKit import NSApp
        NSApp().activateIgnoringOtherApps_(True)
    except Exception:
        pass


def make_floating_window(widget) -> None:
    """Pour le panneau et la calibration : au-dessus du jeu mais cliquable."""
    if not IS_MAC:
        return
    try:
        from AppKit import (NSFloatingWindowLevel, NSWindowCollectionBehaviorCanJoinAllSpaces,
                            NSWindowCollectionBehaviorFullScreenAuxiliary)
        win = _nswindow(widget)
        if not getattr(widget, "_gio_spaces", False):
            with _AsAccessory():
                win.setCollectionBehavior_(NSWindowCollectionBehaviorCanJoinAllSpaces
                                           | NSWindowCollectionBehaviorFullScreenAuxiliary)
            widget._gio_spaces = True
        win.setLevel_(NSFloatingWindowLevel)
        win.setHidesOnDeactivate_(False)
    except Exception as e:  # pragma: no cover
        print(f"[macOS] réglage de la fenêtre impossible : {e}")


# --- Raccourcis clavier globaux ----------------------------------------------------

def parse_hotkey(spec: str) -> tuple[frozenset[str], str]:
    parts = [p.strip().lower() for p in spec.split("+") if p.strip()]
    mods = frozenset(p for p in parts if p in {"ctrl", "alt", "cmd", "shift"})
    keys = [p for p in parts if p not in mods]
    if len(keys) != 1 or len(keys[0]) != 1:
        raise ValueError(f"Raccourci invalide : {spec!r} (ex. 'ctrl+alt+m')")
    return mods, keys[0]


# Codes de touches physiques (clavier QWERTY américain), utilisés si la disposition
# du clavier ne peut pas être lue.
US_KEYCODES = {"a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9, "b": 11,
               "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21,
               "6": 22, "5": 23, "=": 24, "9": 25, "7": 26, "-": 27, "8": 28, "0": 29, "]": 30, "o": 31,
               "u": 32, "[": 33, "i": 34, "p": 35, "l": 37, "j": 38, "'": 39, "k": 40, ";": 41, "\\": 42,
               ",": 43, "/": 44, "n": 45, "m": 46, ".": 47, "`": 50}
_layout_cache: dict[str, int] | None = None


def _layout_keycodes() -> dict[str, int]:
    """Caractère -> code de touche, d'après la disposition du clavier actuelle (AZERTY, QWERTY…)."""
    global _layout_cache
    if _layout_cache is not None:
        return _layout_cache
    import ctypes
    table: dict[str, int] = {}
    try:
        hit = ctypes.CDLL("/System/Library/Frameworks/Carbon.framework/Carbon")
        cs = ctypes.CDLL("/System/Library/Frameworks/CoreServices.framework/CoreServices")
        cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        vp = ctypes.c_void_p
        for name in ("TISCopyCurrentKeyboardLayoutInputSource", "TISCopyCurrentASCIICapableKeyboardLayoutInputSource"):
            getattr(hit, name).restype = vp
        hit.TISGetInputSourceProperty.restype = vp
        hit.TISGetInputSourceProperty.argtypes = [vp, vp]
        cf.CFDataGetBytePtr.restype = vp
        cf.CFDataGetBytePtr.argtypes = [vp]
        cf.CFRelease.argtypes = [vp]
        cs.UCKeyTranslate.restype = ctypes.c_int32
        cs.UCKeyTranslate.argtypes = [vp, ctypes.c_uint16, ctypes.c_uint16, ctypes.c_uint32, ctypes.c_uint32,
                                      ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32), ctypes.c_ulong,
                                      ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_uint16)]
        try:
            hit.LMGetKbdType.restype = ctypes.c_uint8
            kbd = int(hit.LMGetKbdType())
        except AttributeError:
            kbd = 0
        prop = vp.in_dll(hit, "kTISPropertyUnicodeKeyLayoutData").value
        for name in ("TISCopyCurrentKeyboardLayoutInputSource", "TISCopyCurrentASCIICapableKeyboardLayoutInputSource"):
            src = getattr(hit, name)()
            if not src:
                continue
            data = hit.TISGetInputSourceProperty(src, prop)
            if data:
                layout = cf.CFDataGetBytePtr(data)
                for kc in range(128):
                    dead = ctypes.c_uint32(0)
                    n = ctypes.c_ulong(0)
                    buf = (ctypes.c_uint16 * 4)()
                    err = cs.UCKeyTranslate(layout, kc, 3, 0, kbd, 1, ctypes.byref(dead), 4, ctypes.byref(n), buf)
                    if err == 0 and n.value == 1:
                        table.setdefault(chr(buf[0]).lower(), kc)
            cf.CFRelease(src)
            if table:
                break
    except Exception as e:  # pragma: no cover - dépend de l'OS
        print(f"[raccourcis] disposition du clavier illisible : {e}")
    _layout_cache = table
    return table


def keycode_for(ch: str) -> int | None:
    table = _layout_keycodes() if IS_MAC else {}
    return table.get(ch) if ch in table else US_KEYCODES.get(ch)


class _CarbonHotkeys:
    """Raccourcis globaux « système » (RegisterEventHotKey) : fonctionnent par-dessus n'importe
    quelle application, jeu en plein écran compris, SANS autorisation Accessibilité."""
    MODS = {"cmd": 1 << 8, "shift": 1 << 9, "alt": 1 << 11, "ctrl": 1 << 12}
    SIG = int.from_bytes(b"GIOv", "big")

    def __init__(self):
        import ctypes
        self.ct = ctypes
        lib = ctypes.CDLL("/System/Library/Frameworks/Carbon.framework/Carbon")
        vp = ctypes.c_void_p

        class HotKeyID(ctypes.Structure):
            _fields_ = [("signature", ctypes.c_uint32), ("id", ctypes.c_uint32)]

        class TypeSpec(ctypes.Structure):
            _fields_ = [("eventClass", ctypes.c_uint32), ("eventKind", ctypes.c_uint32)]

        self.HotKeyID = HotKeyID
        self.Proc = ctypes.CFUNCTYPE(ctypes.c_int32, vp, vp, vp)
        lib.GetApplicationEventTarget.restype = vp
        lib.InstallEventHandler.restype = ctypes.c_int32
        lib.InstallEventHandler.argtypes = [vp, self.Proc, ctypes.c_ulong, ctypes.POINTER(TypeSpec), vp,
                                            ctypes.POINTER(vp)]
        lib.RegisterEventHotKey.restype = ctypes.c_int32
        lib.RegisterEventHotKey.argtypes = [ctypes.c_uint32, ctypes.c_uint32, HotKeyID, vp, ctypes.c_uint32,
                                            ctypes.POINTER(vp)]
        lib.UnregisterEventHotKey.argtypes = [vp]
        lib.RemoveEventHandler.argtypes = [vp]
        lib.GetEventParameter.restype = ctypes.c_int32
        lib.GetEventParameter.argtypes = [vp, ctypes.c_uint32, ctypes.c_uint32, vp, ctypes.c_ulong, vp, vp]
        self.lib = lib
        self.cbs: dict[int, callable] = {}
        self.refs: list = []
        self._proc = self.Proc(self._on_event)   # gardé en mémoire tant que les raccourcis existent
        spec = (TypeSpec * 1)(TypeSpec(int.from_bytes(b"keyb", "big"), 5))  # kEventHotKeyPressed
        self.handler = vp()
        err = lib.InstallEventHandler(lib.GetApplicationEventTarget(), self._proc, 1, spec, None,
                                      ctypes.byref(self.handler))
        if err != 0:
            raise OSError(f"InstallEventHandler a échoué ({err})")

    def _on_event(self, _next, event, _user):
        try:
            hk = self.HotKeyID()
            err = self.lib.GetEventParameter(event, int.from_bytes(b"----", "big"), int.from_bytes(b"hkid", "big"),
                                             None, self.ct.sizeof(hk), None, self.ct.byref(hk))
            if err == 0 and hk.signature == self.SIG and hk.id in self.cbs:
                self.cbs[hk.id]()
        except Exception as e:  # une erreur ne doit jamais remonter dans le système
            print(f"[raccourcis] erreur : {e}")
        return 0

    def register(self, mods: frozenset[str], key: str, cb) -> str | None:
        kc = keycode_for(key)
        if kc is None:
            return f"touche « {key} » introuvable sur ce clavier"
        bits = sum(self.MODS[m] for m in mods)
        hid = len(self.cbs) + 1
        ref = self.ct.c_void_p()
        err = self.lib.RegisterEventHotKey(kc, bits, self.HotKeyID(self.SIG, hid),
                                           self.lib.GetApplicationEventTarget(), 0, self.ct.byref(ref))
        if err != 0:
            return ("déjà utilisé par une autre application" if err == -9878
                    else f"refusé par macOS (code {err})")
        self.cbs[hid] = cb
        self.refs.append(ref)
        return None

    def stop(self):
        for r in self.refs:
            try:
                self.lib.UnregisterEventHotKey(r)
            except Exception:
                pass
        self.refs, self.cbs = [], {}
        if self.handler:
            try:
                self.lib.RemoveEventHandler(self.handler)
            except Exception:
                pass
            self.handler = None


def _ax_trusted() -> bool:
    try:
        from ApplicationServices import AXIsProcessTrusted
        return bool(AXIsProcessTrusted())
    except Exception:
        return False


class GlobalHotkeys:
    """Raccourcis globaux.
    macOS : méthode système (Carbon), sans autorisation ; repli sur NSEvent (autorisation
    « Accessibilité » nécessaire) si elle n'est pas disponible. Autres systèmes : pynput."""

    def __init__(self, bindings: dict[str, callable]):
        self._specs = list(bindings)
        self._bindings = []
        for spec, cb in bindings.items():
            self._bindings.append((parse_hotkey(spec), self._wrap(spec, cb)))
        self._handles = []
        self._listener = None
        self._carbon = None
        self.method = ""
        self.errors: dict[str, str] = {}
        self.last: tuple[str, float] | None = None   # (raccourci, heure) du dernier déclenchement

    def _wrap(self, spec, cb):
        def run():
            import time
            now = time.time()
            if self.last and self.last[0] == spec and now - self.last[1] < 0.35:
                return   # même appui reçu par les deux méthodes : une seule action
            self.last = (spec, now)
            cb()
        return run

    def start(self) -> str | None:
        """Renvoie un message d'erreur, ou None si tout va bien."""
        if IS_MAC:
            try:
                self._carbon = _CarbonHotkeys()
                for (mods, key), cb in self._bindings:
                    err = self._carbon.register(mods, key, cb)
                    if err:
                        self.errors["+".join(sorted(mods) + [key])] = err
                self.method = "système"
                # En plus, si l'app est autorisée dans « Accessibilité », on écoute aussi le clavier
                # (au cas où un raccourci système ne serait pas transmis).
                if _ax_trusted():
                    self._start_mac()
                    self.method = "système + Accessibilité"
                if self.errors:
                    return "Raccourcis non enregistrés : " + " ; ".join(f"{k} ({v})" for k, v in self.errors.items())
                return None
            except Exception as e:
                print(f"[raccourcis] méthode système indisponible ({e}) : repli sur NSEvent")
                self._carbon = None
            self.method = "NSEvent (Accessibilité)"
            return self._start_mac()
        self.method = "pynput"
        return self._start_pynput()

    def status(self) -> str:
        if not self.method:
            return "Raccourcis désactivés."
        txt = f"Actifs — méthode {self.method}"
        if self.method.startswith("système"):
            txt += " (aucune autorisation macOS nécessaire)"
        if self.errors:
            txt += ". Problèmes : " + " ; ".join(f"{k} : {v}" for k, v in self.errors.items())
        return txt

    def _start_mac(self):
        try:
            from AppKit import (NSEvent, NSEventMaskKeyDown, NSEventModifierFlagCommand,
                                NSEventModifierFlagControl, NSEventModifierFlagOption,
                                NSEventModifierFlagShift)
        except ImportError:
            return "PyObjC absent : raccourcis globaux désactivés."
        flag_of = {"ctrl": NSEventModifierFlagControl, "alt": NSEventModifierFlagOption,
                   "cmd": NSEventModifierFlagCommand, "shift": NSEventModifierFlagShift}
        all_mods = sum(flag_of.values())
        table = [(sum(flag_of[m] for m in mods), key, keycode_for(key), cb) for (mods, key), cb in self._bindings]

        def handle(event) -> bool:
            flags = int(event.modifierFlags()) & all_mods
            ch = (event.charactersIgnoringModifiers() or "").lower()
            kc = int(event.keyCode())
            for mods, key, code, cb in table:
                if flags == mods and (ch == key or kc == code):
                    cb()
                    return True
            return False

        def on_global(event):
            handle(event)   # bloc « void » : ne rien renvoyer

        g = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(NSEventMaskKeyDown, on_global)
        loc = NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
            NSEventMaskKeyDown, lambda e: None if handle(e) else e)
        self._handles = [g, loc]
        try:
            from ApplicationServices import AXIsProcessTrusted
            if not AXIsProcessTrusted():
                return ("Raccourcis : autorise l'app dans Réglages Système → "
                        "Confidentialité et sécurité → Accessibilité, puis relance.")
        except Exception:
            pass
        return None

    def _start_pynput(self):
        try:
            from pynput import keyboard
        except ImportError:
            self.method = ""
            return "pynput absent : raccourcis globaux désactivés."
        names = {"ctrl": "<ctrl>", "alt": "<alt>", "cmd": "<cmd>", "shift": "<shift>"}
        mapping = {"+".join([names[m] for m in sorted(mods)] + [key]): cb
                   for (mods, key), cb in self._bindings}
        self._listener = keyboard.GlobalHotKeys(mapping)
        self._listener.start()
        return None

    def stop(self):
        if self._carbon is not None:
            self._carbon.stop()
            self._carbon = None
        if self._handles:
            from AppKit import NSEvent
            for h in self._handles:
                if h is not None:
                    NSEvent.removeMonitor_(h)
            self._handles = []
        if self._listener:
            self._listener.stop()
            self._listener = None

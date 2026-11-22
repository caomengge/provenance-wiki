# PyInstaller runtime hook: make PDF export (WeasyPrint) work inside the app.
#
# 1. WeasyPrint loads Pango/GLib/HarfBuzz through cffi by bare library name.
#    In a bundled app those libraries sit in sys._MEIPASS, which bare-name
#    lookups don't search, so resolve bare names there first.
# 2. Fontconfig was built with the build machine's config path; point it at
#    a bundled fonts.conf that lists the operating system's font folders.
import os
import sys

_base = getattr(sys, "_MEIPASS", None)
if _base:
    _fc_dir = os.path.join(_base, "fontconfig")
    if os.path.isdir(_fc_dir):
        os.environ.setdefault("FONTCONFIG_PATH", _fc_dir)
        os.environ.setdefault("FONTCONFIG_FILE", os.path.join(_fc_dir, "fonts.conf"))
    try:
        import cffi

        _orig_dlopen = cffi.FFI.dlopen

        def _dlopen(self, name, flags=0):
            if isinstance(name, str) and os.sep not in name:
                candidate = os.path.join(_base, name)
                if os.path.exists(candidate):
                    return _orig_dlopen(self, candidate, flags)
            return _orig_dlopen(self, name, flags)

        cffi.FFI.dlopen = _dlopen
    except ImportError:
        pass

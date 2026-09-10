"""
main.py — Application Entry Point
PDF Manipulation Tool Desktop Edition
─────────────────────────────────────────────────────────────
Initialises pywebview with the BridgeApi and opens the
main window pointing to a local WSGI app.

Run in dev:
    python main.py

Build .exe:
    pyinstaller --noconfirm build.spec
─────────────────────────────────────────────────────────────
"""

import os
import sys
import tempfile
import atexit
import shutil
import webview
import bottle

from backend.bridge import BridgeApi


def resolve_assets_path() -> str:
    """
    Return the absolute path to the assets/ folder.
    Works both in plain-Python dev mode and in a PyInstaller bundle.
    """
    if getattr(sys, "frozen", False):
        # Running as a PyInstaller .exe — assets are in sys._MEIPASS
        base = sys._MEIPASS  # type: ignore[attr-defined]
    else:
        base = os.path.dirname(os.path.abspath(__file__))

    return os.path.join(base, "assets")


def main() -> None:
    api = BridgeApi()
    assets_dir = resolve_assets_path()
    
    # Create an isolated temporary directory for this session's images
    temp_dir = tempfile.mkdtemp(prefix="pdfmerger_")
    
    # Cleanup temp folder on exit
    def cleanup():
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass
    atexit.register(cleanup)

    # Setup Bottle WSGI App
    app = bottle.Bottle()

    @app.route('/')
    def index():
        return bottle.static_file("index.html", root=assets_dir)

    @app.route('/temp/<filepath:path>')
    def serve_temp(filepath):
        return bottle.static_file(filepath, root=temp_dir)

    @app.route('/<filepath:path>')
    def serve_assets(filepath):
        return bottle.static_file(filepath, root=assets_dir)

    # Let the backend know where to write temporary images
    api.set_temp_dir(temp_dir)

    window = webview.create_window(
        title     = "PDF Manipulation Tool",
        url       = app,
        js_api    = api,
        width     = 1440,
        height    = 900,
        min_size  = (960, 640),
        resizable = True,
        background_color = '#FFFFFF'
    )

    # Give BridgeApi a reference to the window so it can call evaluate_js()
    api._window = window

    # Start the WebView2 event loop (blocking)
    webview.start(
        debug   = False,       # Set True for DevTools during development
    )


if __name__ == "__main__":
    main()

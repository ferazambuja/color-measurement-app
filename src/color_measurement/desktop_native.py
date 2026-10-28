"""Qt desktop presentation: local documents, narrow bridge and native recovery UI."""

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from functools import cache
from pathlib import Path
from types import SimpleNamespace

from .demo import demo_frontends
from .desktop import _WINDOW_TITLE, DesktopHost, _bridge_document, _configure_instrument_software
from .desktop_session import DesktopSession


@cache
def qt_types():
    """Load Qt only for the optional desktop path, never during core imports."""
    from PySide6.QtCore import QEvent, QFile, QIODevice, QObject, Qt, QUrl, Signal, Slot
    from PySide6.QtGui import QDesktopServices, QKeySequence
    from PySide6.QtWebChannel import QWebChannel
    from PySide6.QtWebEngineCore import (
        QWebEnginePage,
        QWebEngineProfile,
        QWebEngineScript,
        QWebEngineSettings,
        QWebEngineUrlRequestInfo,
        QWebEngineUrlRequestInterceptor,
    )
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog, QMainWindow, QMessageBox

    class DesktopApplication(QApplication):
        def event(self, event):
            # Application Quit (including the macOS application menu) uses the
            # same flush/confirmation protocol as the window close control.
            host = getattr(self, "host", None)
            if event.type() == QEvent.Type.Quit and host is not None and not host.confirmed:
                host.request_close()
                event.ignore()
                return True
            return super().event(event)

    class MainQueue(QObject):
        """Queue every completion on the QApplication thread, releasing it after delivery."""

        posted = Signal(object)

        def __init__(self):
            super().__init__()
            self.posted.connect(self.deliver, Qt.ConnectionType.QueuedConnection)

        @Slot(object)
        def deliver(self, callback):
            callback()

        def __call__(self, callback):
            self.posted.emit(callback)

    def _local_asset(url, assets):
        return (
            url.isLocalFile()
            and not url.hasQuery()
            and Path(url.toLocalFile()).resolve().is_relative_to(assets)
            and Path(url.toLocalFile()).is_file()
        )

    class LocalRequests(QWebEngineUrlRequestInterceptor):
        """Deny network, subframes and reads outside the immutable workspace assets."""

        def __init__(self, assets, parent):
            super().__init__(parent)
            self.assets = assets

        def interceptRequest(self, info):
            frame = (
                info.resourceType() == QWebEngineUrlRequestInfo.ResourceType.ResourceTypeSubFrame
            )
            info.block(
                frame
                or not (
                    _local_asset(info.requestUrl(), self.assets)
                    or info.requestUrl().scheme() == "data"
                )
            )

    class WorkspacePage(QWebEnginePage):
        def __init__(self, profile, view, assets):
            super().__init__(profile, view)
            self.assets = assets

        def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
            local = (
                is_main_frame
                and url.isLocalFile()
                and not url.hasQuery()
                and Path(url.toLocalFile()).resolve() == self.assets / "index.html"
            )
            if local:
                return True
            if is_main_frame and navigation_type == self.NavigationType.NavigationTypeLinkClicked:
                notices = {
                    self.assets / "vendor" / "LICENSE",
                    self.assets / "resources" / "NOTICE.md",
                }
                if url.scheme() == "https" or (
                    url.isLocalFile() and Path(url.toLocalFile()) in notices
                ):
                    QDesktopServices.openUrl(url)
            return False

        def createWindow(self, _kind):
            return None

        def chooseFiles(self, *_):
            # File selection belongs to the authenticated owner commands, never <input type=file>.
            return []

    class OwnerBridge(QObject):
        def __init__(self, ui):
            super().__init__(ui.page)
            self.ui = ui

        @Slot(str)
        def submit(self, wire):
            # QWebChannel lives only in ApplicationWorld. Its bootstrap runs in the
            # top frame only; subframe navigation and all external resources are denied.
            # Recheck the current document before admitting any envelope.
            url = self.ui.page.url()
            try:
                _bridge_document(
                    file_url=url.isLocalFile(),
                    document_path=url.toLocalFile(),
                    workspace=self.ui.assets / "index.html",
                )
                if len(wire.encode("utf-8")) > 132096:
                    raise ValueError("Bridge envelope exceeds its size limit")
                body = json.loads(wire)
            except (ValueError, RecursionError) as error:
                self.ui.host.reply_value("invalid", error=error)
                return
            self.ui.host.receive(body)

    def _bridge_scripts(page, channel):
        """Expose one JSON submit function; Qt objects stay outside the renderer world."""
        page.setWebChannel(channel, QWebEngineScript.ScriptWorldId.ApplicationWorld)
        library = QFile(":/qtwebchannel/qwebchannel.js")
        if not library.open(QIODevice.OpenModeFlag.ReadOnly):
            raise RuntimeError("The installed Qt runtime has no WebChannel bootstrap")
        source = bytes(library.readAll()).decode("utf-8")
        library.close()
        isolated = (
            source
            + """
    new QWebChannel(qt.webChannelTransport, channel => {
      document.addEventListener('cm-owner-request', event => {
        if (typeof event.detail === 'string') channel.objects.owner.submit(event.detail);
      });
      document.dispatchEvent(new Event('cm-owner-ready'));
    });
    """
        )
        renderer = """
    (() => {
      let resolve, isReady = false;
      const ready = new Promise(done => { resolve = done; });
      document.addEventListener('cm-owner-ready', () => {
        isReady = true; resolve();
      }, {once:true});
      Object.defineProperty(window, 'ownerTransport', {value:Object.freeze({
        ready, get isReady() { return isReady; },
        postMessage: body => document.dispatchEvent(new CustomEvent('cm-owner-request',
          {detail:JSON.stringify(body)}))
      }), configurable:false, writable:false});
    })();
    """
        # Install the renderer listener first. Both scripts run before workspace modules.
        for name, world, source in (
            ("owner-renderer", QWebEngineScript.ScriptWorldId.MainWorld, renderer),
            ("owner-channel", QWebEngineScript.ScriptWorldId.ApplicationWorld, isolated),
        ):
            script = QWebEngineScript()
            script.setName(name)
            script.setWorldId(world)
            script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
            script.setRunsOnSubFrames(False)
            script.setSourceCode(source)
            page.scripts().insert(script)

    class ChoiceBox(QMessageBox):
        def __init__(self, dialog, parent):
            super().__init__(parent)
            self.choice = dialog
            self.setWindowTitle(dialog.title)
            self.setText(dialog.title)
            self.setInformativeText(dialog.text)
            self.setTextFormat(Qt.TextFormat.PlainText)
            for title in dialog.buttons:
                button = self.addButton(title, QMessageBox.ButtonRole.ActionRole)
                button.setAutoDefault(False)
                button.setDefault(False)
            self.setEscapeButton(self.buttons()[dialog.cancel])
            if dialog.default is not None:
                self.setDefaultButton(self.buttons()[dialog.default])

        def keyPressEvent(self, event):
            if self.choice.default is None and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                event.accept()
                return
            super().keyPressEvent(event)

    class DesktopWindow(QMainWindow):
        def closeEvent(self, event):
            if self.ui.host.confirmed:
                event.accept()
            else:
                event.ignore()
                self.ui.host.request_close()

    class QtDesktop:
        def __init__(self, application, assets):
            self.application, self.assets = application, assets
            self.window = DesktopWindow()
            self.window.ui = self
            self.window.setWindowTitle(_WINDOW_TITLE)
            self.window.setMinimumSize(320, 480)
            self.window.resize(1280, 850)
            self.view = QWebEngineView(self.window)
            # An unnamed profile is off the record, without cookies or persistent storage.
            self.profile = QWebEngineProfile(self.view)
            self.interceptor = LocalRequests(assets, self.profile)
            self.profile.setUrlRequestInterceptor(self.interceptor)
            self.page = WorkspacePage(self.profile, self.view, assets)
            self.view.setPage(self.page)
            self.window.setCentralWidget(self.view)
            settings = self.page.settings()
            for setting, enabled in (
                (QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False),
                (QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, False),
                (QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard, False),
                (QWebEngineSettings.WebAttribute.FullScreenSupportEnabled, False),
                (QWebEngineSettings.WebAttribute.NavigateOnDropEnabled, False),
            ):
                settings.setAttribute(setting, enabled)
            self.page.permissionRequested.connect(lambda permission: permission.deny())
            self.page.fileSystemAccessRequested.connect(lambda request: request.reject())
            self.profile.downloadRequested.connect(lambda download: download.cancel())
            self.page.renderProcessTerminated.connect(self._crashed)
            self.queue = MainQueue()
            self.channel = QWebChannel(self.page)
            self.bridge = OwnerBridge(self)
            self.channel.registerObject("owner", self.bridge)
            _bridge_scripts(self.page, self.channel)

        def bind(self, host):
            self.host = host
            menu = self.window.menuBar()
            app = menu.addMenu("Application")
            app.addAction(
                "Reload workspace",
                lambda: self.evaluate("window.reloadOwner(); null"),
                QKeySequence.StandardKey.Refresh,
            )
            app.addAction("Quit", host.request_close, QKeySequence.StandardKey.Quit)
            edit = menu.addMenu("Edit")
            for label, action, shortcut in (
                ("Undo", QWebEnginePage.WebAction.Undo, QKeySequence.StandardKey.Undo),
                ("Redo", QWebEnginePage.WebAction.Redo, QKeySequence.StandardKey.Redo),
                ("Cut", QWebEnginePage.WebAction.Cut, QKeySequence.StandardKey.Cut),
                ("Copy", QWebEnginePage.WebAction.Copy, QKeySequence.StandardKey.Copy),
                ("Paste", QWebEnginePage.WebAction.Paste, QKeySequence.StandardKey.Paste),
                (
                    "Select All",
                    QWebEnginePage.WebAction.SelectAll,
                    QKeySequence.StandardKey.SelectAll,
                ),
            ):
                entry = self.page.action(action)
                entry.setText(label)
                entry.setShortcut(shortcut)
                edit.addAction(entry)
            view = menu.addMenu("View")
            view.addAction(
                "Zoom in",
                lambda: self.view.setZoomFactor(min(2.0, self.view.zoomFactor() + 0.25)),
                QKeySequence.StandardKey.ZoomIn,
            )
            view.addAction(
                "Zoom out",
                lambda: self.view.setZoomFactor(max(0.5, self.view.zoomFactor() - 0.25)),
                QKeySequence.StandardKey.ZoomOut,
            )
            view.addAction(
                "Actual size", lambda: self.view.setZoomFactor(1.0), QKeySequence("Ctrl+0")
            )
            window = menu.addMenu("Window")
            window.addAction("Minimize", self.window.showMinimized)
            window.addAction("Portrait", lambda: self.arrange(760, 980))
            window.addAction("Wide", lambda: self.arrange(1280, 850))

        def arrange(self, width, height):
            available = self.window.screen().availableGeometry()
            self.window.resize(
                min(width, available.width() - 40), min(height, available.height() - 70)
            )
            self.window.move(available.center() - self.window.rect().center())

        def _crashed(self, status, code):
            self.host.renderer_terminated()

        def evaluate(self, script, callback=None):
            wrapped = (
                "(()=>{try{" + script + ";return JSON.stringify({ok:true});}catch(e){"
                "return JSON.stringify({ok:false,error:String(e)});}})()"
            )

            def delivered(value):
                try:
                    value = json.loads(value) if isinstance(value, str) else None
                except ValueError:
                    value = None
                error = (
                    None
                    if isinstance(value, dict) and value.get("ok")
                    else ValueError(
                        value.get("error", "Workspace renderer is unavailable")
                        if isinstance(value, dict)
                        else "Workspace renderer is unavailable"
                    )
                )
                if callback is not None:
                    callback(value, error)
                elif error:
                    self.host.show_failure("Workspace action failed", error)

            self.page.runJavaScript(wrapped, delivered)

        def ask(self, dialog):
            box = ChoiceBox(dialog, self.window)
            box.exec()
            clicked = box.clickedButton()
            return box.buttons().index(clicked) if clicked is not None else dialog.cancel

        def file_path(self, title, *, extensions=None, directory=False, save=False, name=""):
            if directory:
                result = QFileDialog.getExistingDirectory(self.window, title)
            else:
                filters = (
                    "Files (" + " ".join("*." + ext for ext in extensions) + ")"
                    if extensions
                    else "All files (*)"
                )
                chooser = QFileDialog.getSaveFileName if save else QFileDialog.getOpenFileName
                result, _ = chooser(self.window, title, name, filters)
            return Path(result) if result else None

        def choose_folder(self, creating, saved_data=False):
            if creating:
                # Save dialog selects a new path; BatchApplication alone creates/owns it.
                return self.file_path(
                    "Choose a new batch directory", save=True, name="Measurement batch"
                )
            if not saved_data:
                return self.file_path("Review a saved batch without resuming it", directory=True)
            dialog = QFileDialog(self.window, "Open a saved batch folder or MAT archive")
            # Qt's native open dialogs cannot select both folders and typed files.
            # Pick the type explicitly, then use the matching native chooser.
            kind, accepted = QInputDialog.getItem(
                self.window, "Open saved data", "Open", ["Batch folder", "MAT archive"], 0, False
            )
            if not accepted:
                return None
            return self.file_path(
                dialog.windowTitle(),
                directory=kind == "Batch folder",
                extensions=["mat"] if kind == "MAT archive" else None,
            )

        def choose_port(self, ports):
            labels = [port.device + " · " + (port.description or "Serial port") for port in ports]
            selected, accepted = QInputDialog.getItem(
                self.window,
                "Choose the instrument's serial port",
                "Initialization follows only after you review and accept the instrument's effects.",
                labels,
                0,
                False,
            )
            return ports[labels.index(selected)].device if accepted else None

        def copy_text(self, text):
            self.application.clipboard().setText(text)
            if self.application.clipboard().text() != text:
                raise ValueError("The summary could not be copied. Try Copy again.")

        def reload(self):
            self.view.reload()

        def quit(self):
            self.window.close()
            self.application.quit()

    return SimpleNamespace(
        MainQueue=MainQueue,
        LocalRequests=LocalRequests,
        WorkspacePage=WorkspacePage,
        OwnerBridge=OwnerBridge,
        ChoiceBox=ChoiceBox,
        QtDesktop=QtDesktop,
        Application=DesktopApplication,
        Url=QUrl,
    )


def package_identity():
    """Frozen packages fail closed if their compiled worker identity is missing."""
    if not getattr(sys, "frozen", False):
        return None, None
    try:
        from ._packaged_identity import REPORT_SHA256
    except ImportError:
        REPORT_SHA256 = ""
    root = Path(sys._MEIPASS)
    if sys.platform == "darwin":
        root = root.parent / "Resources"
    return root, REPORT_SHA256


def run(assets):
    native = qt_types()
    app = native.Application.instance() or native.Application(sys.argv)
    app.setApplicationName(_WINDOW_TITLE)
    app.setQuitOnLastWindowClosed(False)
    ui = native.QtDesktop(app, assets)
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="desktop-owner")
    demo = ThreadPoolExecutor(max_workers=1, thread_name_prefix="desktop-demo-load")
    session = DesktopSession(demo_frontends(), setup_executor=demo)
    resource_root, digest = package_identity()
    software = _configure_instrument_software(session, resource_root, digest)
    host = DesktopHost(
        ui=ui,
        session=session,
        assets=assets,
        executor=executor,
        demo_executor=demo,
        dispatch_main=ui.queue,
        instrument_software=software,
    )
    ui.bind(host)
    app.host = host
    ui.page.load(native.Url.fromLocalFile(str(assets / "index.html")))
    ui.window.show()
    # Menus and window-close both enter the same renderer flush / owner-close protocol.
    try:
        return app.exec()
    finally:
        demo.shutdown(wait=True)
        executor.shutdown(wait=True)


def startup_failure(reason):
    """Keep failures visible in a windowed package that has no console."""
    from .desktop import _failure_alert

    native = qt_types()
    app = native.Application.instance() or native.Application(sys.argv)
    box = native.ChoiceBox(_failure_alert("Unable to open Color Measurement", reason), None)
    box.exec()
    # No acquisition owner, web view or executor has been constructed.
    return app

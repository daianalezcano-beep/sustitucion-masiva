"""Deteccion e instalacion de Google Chrome, multiplataforma.

Los scripts originales (pensados para el contenedor Linux de Google Colab)
instalaban Chrome automaticamente via apt-get/.deb. En Windows no hay esa
garantia de permisos de administrador, asi que aca NUNCA se instala Chrome
solo en Windows/Mac: si no se encuentra, se levanta un error legible pidiendo
instalacion manual. En Linux se mantiene el comportamiento original (auto
instalacion), util para correr esta misma app en un servidor/CI si hiciera
falta.
"""
import os
import platform
import shutil
import subprocess


def _chrome_version(path):
    try:
        r = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=8)
        v = (r.stdout or r.stderr).strip()
        return v if v and any(c.isdigit() for c in v) else ""
    except Exception:
        return ""


def _find_chrome_windows():
    candidates = []
    for env_var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        base = os.environ.get(env_var)
        if base:
            candidates.append(os.path.join(base, "Google", "Chrome", "Application", "chrome.exe"))
    candidates += [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    # Alcanza con que el .exe exista: no exigimos que "chrome.exe --version"
    # devuelva texto, porque en PCs corporativas eso puede fallar (antivirus,
    # otro Chrome ya abierto, políticas de ejecución) aunque Chrome esté bien
    # instalado. La versión queda solo como dato informativo para el log.
    for path in candidates:
        if os.path.exists(path):
            return path, _chrome_version(path) or "versión no detectada"
    p = shutil.which("chrome.exe") or shutil.which("chrome")
    if p:
        return p, _chrome_version(p) or "versión no detectada"
    return None, ""


def _find_chrome_mac():
    path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    if os.path.exists(path):
        return path, _chrome_version(path) or "versión no detectada"
    return None, ""


def _find_chrome_linux():
    for name in ["google-chrome-stable", "google-chrome", "chromium-browser", "chromium"]:
        p = shutil.which(name)
        if p:
            v = _chrome_version(p)
            if v:
                return p, v
    for path in ["/usr/bin/google-chrome-stable", "/usr/bin/google-chrome"]:
        if os.path.exists(path):
            v = _chrome_version(path)
            if v:
                return path, v
    return None, ""


def find_chrome():
    system = platform.system()
    if system == "Windows":
        return _find_chrome_windows()
    if system == "Darwin":
        return _find_chrome_mac()
    return _find_chrome_linux()


def _install_chrome_linux():
    DEVNULL = subprocess.DEVNULL
    e1 = e2 = None
    try:
        subprocess.run(
            "wget -qO- https://dl.google.com/linux/linux_signing_key.pub "
            "| gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg",
            shell=True, check=True, stdout=DEVNULL, stderr=DEVNULL)
        with open("/etc/apt/sources.list.d/google-chrome.list", "w") as f:
            f.write("deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] "
                    "https://dl.google.com/linux/chrome/deb/ stable main\n")
        subprocess.run(["apt-get", "update", "-qq"], stdout=DEVNULL, stderr=DEVNULL)
        subprocess.run(["apt-get", "install", "-y", "-qq", "google-chrome-stable"],
                        check=True, stdout=DEVNULL, stderr=DEVNULL)
        if find_chrome()[0]:
            return
    except Exception as exc:
        e1 = exc
    try:
        deb = "/tmp/google-chrome-stable.deb"
        url = "https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb"
        subprocess.run(["wget", "-q", "-O", deb, url], check=True)
        subprocess.run(["dpkg", "-i", deb], stdout=DEVNULL, stderr=DEVNULL)
        subprocess.run(["apt-get", "update", "-qq"], stdout=DEVNULL, stderr=DEVNULL)
        subprocess.run(["apt-get", "install", "-f", "-y", "-qq"],
                        check=True, stdout=DEVNULL, stderr=DEVNULL)
    except Exception as exc:
        e2 = exc
        raise EnvironmentError(f"No se pudo instalar Chrome automaticamente. Repo: {e1}  .deb: {e2}")


def find_or_prepare_chrome():
    """Devuelve (path, version) de Chrome.

    En Windows/Mac nunca instala solo: si falta, levanta un error legible
    pidiendo instalacion manual (no hay garantia de permisos de admin).
    En Linux mantiene el auto-instalador original via apt-get/.deb.
    """
    path, version = find_chrome()
    if path:
        print(f"  Chrome OK: {path}  [{version}]")
        return path, version

    system = platform.system()
    if system == "Windows":
        raise EnvironmentError(
            "No se encontro Google Chrome instalado en esta PC.\n"
            "Instalalo desde https://www.google.com/chrome/ y volve a ejecutar.\n"
            "(Esta app no lo instala automaticamente porque requiere permisos de administrador.)"
        )
    if system == "Darwin":
        raise EnvironmentError(
            "No se encontro Google Chrome instalado en esta Mac.\n"
            "Instalalo desde https://www.google.com/chrome/ y volve a ejecutar."
        )

    print("  Chrome no encontrado, instalando (Linux)...")
    _install_chrome_linux()
    path, version = find_chrome()
    if not path:
        raise EnvironmentError("Chrome instalado pero no encontrado. Reintenta.")
    print(f"  Chrome instalado: {version}")
    return path, version

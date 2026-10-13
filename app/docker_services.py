"""Local Docker apps with checked startup and persistent storage.

Only the two registered upstream images can be launched. No shell commands, no
container replacement, and no volume removal. Docker Desktop remains independent
of NexusDesk's process lifetime.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Service:
    key: str
    title: str
    image: str
    container_port: int
    host_port: int
    accepted_images: tuple[str, ...]
    legacy_names: tuple[str, ...] = ()

    @property
    def container_name(self):
        return "nexusdesk-" + self.key


SERVICES = {
    "metube": Service("metube", "MeTube", "ghcr.io/alexta69/metube:latest", 8081, 8082,
                      ("ghcr.io/alexta69/metube", "alexta69/metube"), ("metube",)),
    "stirling_pdf": Service("stirling_pdf", "Stirling-PDF",
                           "docker.stirlingpdf.com/stirlingtools/stirling-pdf:latest", 8080, 8090,
                           ("docker.stirlingpdf.com/stirlingtools/stirling-pdf", "stirlingtools/stirling-pdf",
                            "ghcr.io/stirling-tools/stirling-pdf")),
}
_SERVICE_LOCKS = {key: threading.Lock() for key in SERVICES}


def log(message):
    print(message, flush=True)


def docker_executable():
    if os.name != "nt":
        # Fedora's rootless Podman needs no daemon, root or Docker Desktop.
        executable = shutil.which("podman") or shutil.which("docker")
        if executable:
            return executable
        raise RuntimeError("Install Podman (Fedora: sudo dnf install podman), or use an existing Docker engine, then launch again.")
    executable = shutil.which("docker")
    if executable:
        return executable
    candidates = [Path(os.environ.get("ProgramFiles", r"C:\Program Files")) /
                  "Docker/Docker/resources/bin/docker.exe",
                  Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Docker/Docker/resources/bin/docker.exe"]
    for path in candidates:
        if path.is_file():
            return str(path)
    raise RuntimeError("Docker Desktop is required. Install it, finish its first-time setup, then click Launch again.")


def flags():
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def command(docker, *args, timeout=30, check=True):
    try:
        result = subprocess.run([docker, *args], capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=timeout, creationflags=flags())
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("The container engine did not respond in time. Check Podman/Docker and try again.") from error
    if check and result.returncode:
        raise RuntimeError((result.stderr or result.stdout).strip() or "Docker command failed.")
    return result


def stream(docker, *args):
    # Image download progress is forwarded to the panel, including stderr.
    process = subprocess.Popen([docker, *args], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, encoding="utf-8", errors="replace", creationflags=flags())
    try:
        for line in process.stdout:
            log(line.rstrip())
        if process.wait():
            raise RuntimeError("Docker could not download the image. Check the output above and your internet connection.")
    finally:
        if process.poll() is None:
            process.terminate()
        process.stdout.close()


def ensure_engine(docker, timeout=180):
    if Path(docker).name in {"podman", "podman.exe"}:
        result = command(docker, "info", "--format", "{{.Host.OS}}", timeout=15, check=False)
        if result.returncode or result.stdout.strip().casefold() != "linux":
            raise RuntimeError("Rootless Podman is unavailable. Check podman info and your user's subordinate UID/GID configuration.")
        log("Rootless Podman is ready.")
        return
    def ready():
        try:
            return command(docker, "info", "--format", "{{.OSType}}", timeout=8, check=False)
        except RuntimeError:
            return None
    result = ready()
    if not result or result.returncode:
        if os.name != "nt":
            raise RuntimeError("Start the Docker engine, then launch this brick again.")
        paths = [Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Docker/Docker/Docker Desktop.exe",
                 Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/DockerDesktop/Docker Desktop.exe",
                 Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Docker/Docker/Docker Desktop.exe",
                 Path(os.environ.get("LOCALAPPDATA", "")) / "Docker/Docker Desktop.exe"]
        executable = next((p for p in paths if p.is_file()), None)
        if not executable:
            raise RuntimeError("Docker Desktop was not found. Start it manually or install it first.")
        log("Starting Docker Desktop; waiting for the Linux engine…")
        subprocess.Popen([str(executable)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=getattr(subprocess, "DETACHED_PROCESS", 0) |
                                       getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = ready()
            if result and not result.returncode:
                break
            time.sleep(2)
        else:
            raise RuntimeError("Docker Desktop did not start within 3 minutes. Complete its setup and retry.")
    if result.stdout.strip() != "linux":
        raise RuntimeError("Switch Docker Desktop to Linux containers, then launch again.")
    log("Docker Linux engine is ready.")


def inspect(docker, name, timeout=30):
    result = command(docker, "container", "inspect", name, check=False, timeout=timeout)
    if result.returncode:
        if "no such" in result.stderr.lower():
            return None
        raise RuntimeError(result.stderr.strip() or "Could not inspect Docker containers.")
    try:
        return json.loads(result.stdout)[0]
    except (ValueError, IndexError, TypeError) as error:
        raise RuntimeError("Docker returned invalid container information.") from error


def compatible(info, service):
    image = info.get("Config", {}).get("Image", "").split("@")[0]
    image = image.rsplit(":", 1)[0] if ":" in image.rsplit("/", 1)[-1] else image
    return image in service.accepted_images


def container_url(info, service):
    # PortBindings remains populated for stopped containers; NetworkSettings may not.
    key = f"{service.container_port}/tcp"
    ports = info.get("HostConfig", {}).get("PortBindings", {}).get(key) or \
            info.get("NetworkSettings", {}).get("Ports", {}).get(key) or []
    for binding in ports:
        if binding.get("HostIp", "") in {"", "0.0.0.0", "127.0.0.1"}:
            value = str(binding.get("HostPort", ""))
            if value.isdecimal() and 0 < int(value) < 65536:
                prefix = "/"
                if service.key == "metube":
                    for setting in info.get("Config", {}).get("Env", []):
                        if setting.startswith("URL_PREFIX="):
                            prefix = "/" + setting.split("=", 1)[1].strip("/") + "/"
                return f"http://127.0.0.1:{int(value)}" + quote(prefix.replace("//", "/"), safe="/")
    raise RuntimeError("This container has no usable local browser port. Its existing configuration was left unchanged.")


def find_existing(docker, service, timeout=30):
    own = inspect(docker, service.container_name, timeout=timeout)
    if own:
        if not compatible(own, service):
            raise RuntimeError(f"{service.container_name} belongs to another image. Rename that container in Docker Desktop first.")
        container_url(own, service)
        return service.container_name, own
    for name in service.legacy_names:
        info = inspect(docker, name, timeout=timeout)
        if info and compatible(info, service):
            try:
                container_url(info, service)
            except RuntimeError:
                continue
            log(f"Reusing existing {name} container and its downloads/configuration.")
            return name, info
    return None, None


def free_port(preferred):
    for port in range(preferred, preferred + 40):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                pass
    raise RuntimeError("No free local port was found. Close an unused service and retry.")


def new_container_arguments(service, port, root=ROOT, engine="docker"):
    state = root / "services" / service.key
    state.mkdir(parents=True, exist_ok=True)
    args = ["run", "-d", "--name", service.container_name, "--restart", "unless-stopped",
            "--publish", f"127.0.0.1:{port}:{service.container_port}",
            "--label", "com.nexusdesk.service=" + service.key]
    podman = Path(engine).name == "podman"
    if podman:
        # Keep newly created bind folders owned by the user; never chown existing data.
        args += ["--userns", "keep-id" if service.key == "metube" else "keep-id:uid=0,gid=0", "--user", "0"]
    def mount(source, target):
        if os.name != "nt":
            # Private relabel of app-owned folders permits SELinux enforcing mode.
            return ["--volume", f"{source.resolve()}:{target}:Z"]
        return ["--mount", f"type=bind,source={source.resolve()},target={target}"]
    if service.key == "metube":
        downloads = Path.home() / "Downloads" / "MeTube"
        downloads.mkdir(parents=True, exist_ok=True)
        args += mount(downloads, "/downloads")
        if podman:
            args += ["--env", f"PUID={os.getuid()}", "--env", f"PGID={os.getgid()}", "--env", "CHOWN_DIRS=false"]
        args += ["--env", "DEFAULT_THEME=dark", "--env", "DELETE_FILE_ON_TRASHCAN=false"]
    else:
        # Named volume for tessdata retains the image's bundled languages on first
        # creation. An empty host bind mount would hide those OCR files.
        for folder, target in [("configs", "/configs"), ("logs", "/logs"),
                               ("customFiles", "/customFiles"), ("pipeline", "/pipeline")]:
            path = state / folder
            path.mkdir(exist_ok=True)
            args += mount(path, target)
        args += ["--mount", "type=volume,source=nexusdesk-stirling-tessdata,target=/usr/share/tessdata",
                 "--env", "SECURITY_ENABLELOGIN=false", "--env", "SYSTEM_DEFAULTLOCALE=en-GB"]
    return args + [service.image]


def wait_http(url, service, timeout=240):
    log("Waiting for the application to finish starting…")
    deadline = time.monotonic() + timeout
    # Do not follow a container's redirects to another host or use HTTP proxies.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    client = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    endpoint = url + ("api/v1/info/status" if service.key == "stirling_pdf" else "")
    while time.monotonic() < deadline:
        try:
            with client.open(endpoint, timeout=5) as response:
                if 200 <= response.status < 300:
                    if service.key != "stirling_pdf" or "UP" in response.read(65536).decode("utf-8", "replace"):
                        return
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(2)
    raise RuntimeError("The container started but the application is not ready. Check Docker Desktop logs, then retry.")


def open_browser(url):
    if not webbrowser.open(url, new=2):
        raise RuntimeError("The service is ready, but the browser could not open. Open " + url + " manually.")


def service_details(key, name, info):
    """Return only integration metadata, never Docker environment/credentials."""
    service = SERVICES[key]
    result = {"key": key, "title": service.title, "container": name,
              "url": container_url(info, service), "download_root": None,
              "audio_download_root": None}
    if key == "metube":
        paths = {"DOWNLOAD_DIR": "/downloads", "AUDIO_DOWNLOAD_DIR": ""}
        for item in info.get("Config", {}).get("Env", []):
            key_name, _, value = item.partition("=")
            if key_name in paths:
                paths[key_name] = value
        paths["AUDIO_DOWNLOAD_DIR"] = paths["AUDIO_DOWNLOAD_DIR"] or paths["DOWNLOAD_DIR"]
        for field, container_path in (("download_root", paths["DOWNLOAD_DIR"]),
                                      ("audio_download_root", paths["AUDIO_DOWNLOAD_DIR"])):
            # Only host bind mounts can be opened locally. Named Docker volumes
            # live inside the VM and intentionally do not get Open actions.
            for mount in sorted(info.get("Mounts", []), key=lambda m: len(m.get("Destination", "")), reverse=True):
                target = mount.get("Destination", "").rstrip("/")
                if mount.get("Type") != "bind" or not target:
                    continue
                if container_path == target or container_path.startswith(target + "/"):
                    suffix = container_path[len(target):].lstrip("/")
                    source = mount.get("Source", "")
                    # Docker Desktop can report VM mount locations. Use its
                    # own host-source metadata if that is present.
                    labels = info.get("Config", {}).get("Labels", {}) or {}
                    for label, value in labels.items():
                        if label.endswith("/Target") and value == target:
                            source = labels.get(label[:-6] + "Source", source)
                    if os.name == "nt" and str(source).startswith("/"):
                        continue
                    base = Path(source).resolve()
                    resolved = (base / suffix).resolve()
                    if resolved.is_relative_to(base):
                        result[field] = str(resolved)
                    break
    return result


def resolve_service(key, timeout=4):
    """Read-only discovery of a registered service; does not start Docker."""
    if key not in SERVICES:
        raise ValueError("Unknown registered service.")
    docker = docker_executable()
    name, info = find_existing(docker, SERVICES[key], timeout=timeout)
    return service_details(key, name, info) if info else None


def operate(key, action="launch", root=ROOT):
    if key not in SERVICES or action not in {"start", "restart", "launch", "stop", "update"}:
        raise ValueError("Unknown service or unsupported action.")
    with _SERVICE_LOCKS[key]:
        return _operate(key, action, root)


def _operate(key, action, root):
    service = SERVICES[key]
    docker = docker_executable()
    ensure_engine(docker)
    name, info = find_existing(docker, service)
    if action == "stop":
        if name:
            command(docker, "stop", name, timeout=60)
            log(f"Stopped {service.title}. All downloads, settings and OCR data are retained.")
        else:
            log(f"{service.title} has no container to stop.")
        return
    if action == "update":
        # Download only. Replacement is explicit and handled in Docker Desktop,
        # so legacy bind mounts and user customisations cannot be lost.
        stream(docker, "pull", service.image)
        log("Image downloaded. The existing container was not replaced; see DOCKER_BRICKS.md for applying the update.")
        return
    if not name:
        port = free_port(service.host_port)
        if port != service.host_port:
            log(f"Port {service.host_port} is busy; using {port} instead.")
        if command(docker, "image", "inspect", service.image, check=False).returncode:
            log(f"First launch: downloading the full {service.title} image. This may take several minutes.")
            stream(docker, "pull", service.image)
        command(docker, *new_container_arguments(service, port, root, engine=docker), timeout=120)
        name = service.container_name
        info = inspect(docker, name)
    elif action == "restart":
        command(docker, "restart", name, timeout=120)
        info = inspect(docker, name)
    if not info or not info.get("State", {}).get("Running"):
        command(docker, "start", name, timeout=120)
        info = inspect(docker, name)
    if not info:
        raise RuntimeError("The container could not be found after startup.")
    url = container_url(info, service)
    if service.key == "metube":
        location = next((m.get("Source") for m in info.get("Mounts", []) if m.get("Destination") == "/downloads"), "container storage")
        log("Downloads: " + location)
    wait_http(url, service)
    log(f"{service.title} is ready at {url}")
    if action == "launch":
        open_browser(url)
        log("Browser opened. The service stays running when NexusDesk closes.")
    return service_details(key, name, info)


def main(key):
    parser = argparse.ArgumentParser(description="Launch a NexusDesk Docker brick")
    parser.add_argument("--action", choices=["launch", "start", "restart", "stop", "update"], default="launch")
    args = parser.parse_args()
    try:
        operate(key, args.action)
    except (RuntimeError, OSError, ValueError) as error:
        log("ERROR: " + str(error))
        return 1
    return 0

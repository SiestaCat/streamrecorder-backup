#!/usr/bin/env python3

import argparse
import json
import shlex
import logging
import re
import shutil
import signal
import subprocess
import sys
import threading
import tempfile
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

API_URL = "https://streamrecorder.io/api/user/recordingsv2"
COOKIE_FILE = Path("/root/streamrecorder/cookie")
FILES_DIR = Path("/root/streamrecorder/files")
RCLONE_REMOTE = "gdrive"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36 Edg/152.0.0.0"
PAGE_LIMIT = 20
INTERVAL_SECONDS = 600

STOP = threading.Event()
LOGGER = logging.getLogger("streamrecorder")


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(threadName)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
        force=True,
    )


def handle_signal(signum, _frame):
    LOGGER.info("Señal %s recibida. Se detendrá el script al terminar la operación actual.", signum)
    STOP.set()


def load_cookie():
    cookie = COOKIE_FILE.read_text(encoding="utf-8").strip()
    if not cookie:
        raise RuntimeError(f"El archivo de cookie está vacío: {COOKIE_FILE}")

    if cookie.lower().startswith("cookie:"):
        cookie = cookie.split(":", 1)[1].strip()

    if "=" not in cookie:
        raise RuntimeError(f"El contenido de {COOKIE_FILE} no parece una cookie válida NAME=VALUE")

    return cookie


def log_multiline(level, prefix, text):
    if not text:
        LOGGER.log(level, "%s | <vacío>", prefix)
        return

    for line in text.splitlines():
        LOGGER.log(level, "%s | %s", prefix, line)


def run_command(command, description, log_output=True, capture_output=False):
    LOGGER.info("%s | Comando: %s", description, shlex.join(command))
    started = time.monotonic()

    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    stdout_lines = []
    stderr_lines = []

    def read_stdout():
        for line in process.stdout:
            line = line.rstrip("\r\n")
            stdout_lines.append(line)

            if log_output and not capture_output:
                LOGGER.info("%s | stdout | %s", description, line)

    def read_stderr():
        for line in process.stderr:
            line = line.rstrip("\r\n")
            stderr_lines.append(line)
            if not capture_output:
                LOGGER.info("%s | stderr | %s", description, line)

    stdout_thread = threading.Thread(
        target=read_stdout,
        name=f"{threading.current_thread().name}-stdout",
    )
    stderr_thread = threading.Thread(
        target=read_stderr,
        name=f"{threading.current_thread().name}-stderr",
    )

    stdout_thread.start()
    stderr_thread.start()

    returncode = process.wait()

    stdout_thread.join()
    stderr_thread.join()

    elapsed = time.monotonic() - started

    stdout = "\n".join(stdout_lines).strip()
    stderr = "\n".join(stderr_lines).strip()

    if not capture_output or returncode != 0:
        LOGGER.log(
            logging.ERROR if returncode != 0 else logging.INFO,
            "%s | Finalizado | exit_code=%s duración=%.3fs",
            description,
            returncode,
            elapsed,
        )

    if returncode != 0:
        if stdout and (capture_output or not log_output):
            log_multiline(logging.ERROR, f"{description} | stdout", stdout)

        if capture_output:
            log_multiline(logging.ERROR, f"{description} | stderr", stderr)
        elif stderr:
            LOGGER.error(
                "%s | El comando terminó con errores; stderr capturado arriba",
                description,
            )

        raise RuntimeError(f"{description} terminó con código {returncode}")

    return stdout


def fetch_recordings(target_id):
    recordings = []
    offset = 0

    while not STOP.is_set():
        url = f"{API_URL}?targetid={target_id}&offset={offset}&limit={PAGE_LIMIT}&sortby=id&sortdir=desc"
        LOGGER.info(
            "Target %s | API | GET %s",
            target_id,
            url,
        )
        cookie = load_cookie()
        LOGGER.info(
            "Target %s | API | cookie_file=%s user_agent=%s",
            target_id,
            COOKIE_FILE,
            USER_AGENT,
        )
        LOGGER.info("Target %s | API | -b %s", target_id, cookie)

        with tempfile.NamedTemporaryFile(prefix=f"streamrecorder-{target_id}-", suffix=".body", delete=False) as body_tmp:
            body_path = Path(body_tmp.name)
        with tempfile.NamedTemporaryFile(prefix=f"streamrecorder-{target_id}-", suffix=".headers", delete=False) as headers_tmp:
            headers_path = Path(headers_tmp.name)

        command = [
            "curl",
            "-sS",
            "-v",
            "-L",
            "-b",
            cookie,
            "-H",
            f"user-agent: {USER_AGENT}",
            "--dump-header",
            str(headers_path),
            "--output",
            str(body_path),
            "--write-out",
            "http_code=%{http_code}\nurl_effective=%{url_effective}\ncontent_type=%{content_type}\ntime_total=%{time_total}\nsize_download=%{size_download}\nnum_redirects=%{num_redirects}",
            url,
        ]

        description = f"API target={target_id} offset={offset}"
        LOGGER.info("%s | Comando: %s", description, shlex.join(command))
        started = time.monotonic()

        try:
            result = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            elapsed = time.monotonic() - started
            body = body_path.read_text(encoding="utf-8", errors="replace")
            headers = headers_path.read_text(encoding="utf-8", errors="replace")
            curl_meta = {}

            for line in result.stdout.splitlines():
                if "=" in line:
                    key, value = line.split("=", 1)
                    curl_meta[key] = value

            LOGGER.info(
                "%s | curl finalizado | exit_code=%s duración_python=%.3fs http_code=%s url_effective=%s content_type=%s time_total=%s size_download=%s num_redirects=%s",
                description,
                result.returncode,
                elapsed,
                curl_meta.get("http_code", "desconocido"),
                curl_meta.get("url_effective", "desconocida"),
                curl_meta.get("content_type", "desconocido"),
                curl_meta.get("time_total", "desconocido"),
                curl_meta.get("size_download", "desconocido"),
                curl_meta.get("num_redirects", "desconocido"),
            )

            verbose_lines = result.stderr.splitlines()
            cookie_headers = [
                line[2:]
                for line in verbose_lines
                if line.lower().startswith("> cookie:")
            ]

            if cookie_headers:
                for cookie_header in cookie_headers:
                    LOGGER.info("%s | request header (-b) | %s", description, cookie_header)
            else:
                LOGGER.warning(
                    "%s | request header (-b) | curl no envió ninguna cabecera Cookie para esta URL usando el contenido de %s",
                    description,
                    COOKIE_FILE,
                )

            if result.returncode != 0:
                error_lines = [line for line in verbose_lines if not line.lower().startswith("> cookie:")]
                if error_lines:
                    log_multiline(logging.ERROR, f"{description} | curl stderr", "\n".join(error_lines))

            log_multiline(logging.INFO, f"{description} | response headers", headers.strip())
            log_multiline(logging.INFO, f"{description} | response body", body)

            if result.returncode != 0:
                raise RuntimeError(
                    f"{description} | curl terminó con código {result.returncode} | "
                    f"url={url} | url_effective={curl_meta.get('url_effective', 'desconocida')}"
                )

            http_code_text = curl_meta.get("http_code", "0")
            try:
                http_code = int(http_code_text)
            except ValueError:
                http_code = 0

            if not 200 <= http_code < 300:
                raise RuntimeError(
                    f"{description} | HTTP {http_code_text} | "
                    f"url={url} | url_effective={curl_meta.get('url_effective', 'desconocida')}"
                )

            try:
                payload = json.loads(body)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"{description} | La API devolvió JSON inválido: {exc} | "
                    f"url={url} | body_length={len(body)}"
                ) from exc
        finally:
            body_path.unlink(missing_ok=True)
            headers_path.unlink(missing_ok=True)

        page = payload.get("data")
        if not isinstance(page, list):
            LOGGER.error(
                "Target %s | Respuesta JSON sin 'data' válido | claves=%s",
                target_id,
                list(payload.keys()) if isinstance(payload, dict) else type(payload).__name__,
            )
            raise RuntimeError(f"La respuesta de target {target_id} no contiene un campo 'data' válido")

        LOGGER.info(
            "Target %s | Página recibida | offset=%s registros=%s count=%s count_total=%s",
            target_id,
            offset,
            len(page),
            payload.get("count", "desconocido"),
            payload.get("count_total", "desconocido"),
        )

        if not page:
            LOGGER.info("Target %s | Paginación terminada: página vacía en offset=%s", target_id, offset)
            break

        recordings.extend(page)
        offset += len(page)
        LOGGER.info("Target %s | Siguiente página | offset=%s", target_id, offset)

    LOGGER.info("Target %s | Total de registros obtenidos: %s", target_id, len(recordings))
    return recordings


def sanitize_target_name(value, target_id):
    value = str(value or f"target_{target_id}")
    value = value.replace("/", "_").replace("\\", "_").replace("\x00", "_")
    value = re.sub(r"\s+", "_", value).strip("._")
    return value or f"target_{target_id}"


def build_filename(recording, download_url, target_id):
    target_name = sanitize_target_name(recording.get("target_name"), target_id)
    recorded_at = recording.get("recorded_at")
    if not recorded_at:
        raise RuntimeError("El registro no contiene 'recorded_at'")

    try:
        parsed_date = datetime.fromisoformat(str(recorded_at).replace("Z", "+00:00"))
    except ValueError as exc:
        raise RuntimeError(f"recorded_at no tiene un formato ISO válido: {recorded_at}") from exc

    timestamp = parsed_date.strftime("%Y-%m-%d-%H-%M")
    url_path = unquote(urlparse(download_url).path)
    extension = Path(url_path).suffix

    if not extension or len(extension) > 15:
        LOGGER.warning("No se pudo detectar una extensión fiable en la URL; se usará .bin")
        extension = ".bin"

    return f"{target_name}_{timestamp}{extension}"


def is_processed(filename, folder_dest, recording_id):
    remote_file = f"{RCLONE_REMOTE}:{folder_dest.rstrip('/')}/{filename}"
    command = ["rclone", "lsjson", "--stat", "--files-only", remote_file]

    LOGGER.info("Recording %s | Verificando archivo remoto: %s", recording_id, remote_file)

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    if result.returncode == 4:
        return False

    if result.returncode != 0:
        raise RuntimeError(
            f"Recording {recording_id} | Error verificando {remote_file}: {result.stderr.strip()}"
        )

    file_info = json.loads(result.stdout)
    exists = isinstance(file_info, dict) and file_info.get("Name") == filename and not file_info.get("IsDir", False)

    LOGGER.info("Recording %s | Archivo remoto %s: %s", recording_id, "existente" if exists else "inexistente", remote_file)
    return exists

def download_file(download_url, destination, recording_id):
    partial = Path(f"{destination}.part")

    if destination.exists():
        LOGGER.info(
            "Recording %s | El archivo local ya existe; se reutilizará sin descargar de nuevo: %s",
            recording_id,
            destination,
        )
        return

    LOGGER.info(
        "Recording %s | Descargando a %s%s",
        recording_id,
        partial,
        f" | reanudando desde {partial.stat().st_size} bytes" if partial.exists() else "",
    )
    LOGGER.info("Recording %s | URL descarga exacta: %s", recording_id, download_url)

    command = [
        "curl",
        "-fL",
        "--silent",
        "--show-error",
        "--retry",
        "3",
        "--retry-delay",
        "5",
        "--connect-timeout",
        "30",
        "--continue-at",
        "-",
        "-H",
        f"user-agent: {USER_AGENT}",
        "--output",
        str(partial),
        "--write-out",
        "http_code=%{http_code} bytes=%{size_download} seconds=%{time_total} speed=%{speed_download}",
        download_url,
    ]

    run_command(command, f"Descarga recording={recording_id}")
    partial.replace(destination)
    LOGGER.info("Recording %s | Descarga completada: %s (%s bytes)", recording_id, destination, destination.stat().st_size)


def upload_file(local_file, folder_dest, recording_id):
    output = run_command(
        [
            "rclone",
            "copy",
            str(local_file),
            f"{RCLONE_REMOTE}:{folder_dest}",
            "-P",
        ],
        f"Subida rclone recording={recording_id}",
    )
    LOGGER.info("Recording %s | Subida a Google Drive completada", recording_id)
    return output


def process_recording(recording, target_id, folder_dest):
    recording_id = recording.get("id")
    if recording_id is None:
        LOGGER.warning("Target %s | Registro sin id; se omite", target_id)
        return

    sources = recording.get("sources")
    if not isinstance(sources, list) or not sources:
        LOGGER.warning("Recording %s | No contiene sources; se omite por ahora", recording_id)
        return

    download_url = sources[0].get("downloadlink") if isinstance(sources[0], dict) else None
    if not download_url:
        LOGGER.warning("Recording %s | sources[0] no contiene downloadlink; se omite por ahora", recording_id)
        return

    filename = build_filename(recording, download_url, target_id)
    local_file = FILES_DIR / filename

    if is_processed(filename, folder_dest, recording_id):
        LOGGER.info("Recording %s | Archivo ya existente en Google Drive; se omite: %s", recording_id, filename)
        return

    LOGGER.info(
        "Recording %s | Procesando | target=%s target_name=%s recorded_at=%s archivo=%s",
        recording_id,
        target_id,
        recording.get("target_name"),
        recording.get("recorded_at"),
        local_file,
    )

    download_file(download_url, local_file, recording_id)
    upload_file(local_file, folder_dest, recording_id)

    try:
        local_file.unlink()
        LOGGER.info("Recording %s | Archivo local eliminado tras subida correcta: %s", recording_id, local_file)
    except FileNotFoundError:
        LOGGER.warning("Recording %s | El archivo local ya no existe: %s", recording_id, local_file)
    except OSError as exc:
        LOGGER.error("Recording %s | No se pudo eliminar %s: %s", recording_id, local_file, exc)


def process_target(target_id, folder_dest):
    LOGGER.info("Target %s | Inicio de ciclo", target_id)

    try:
        recordings = fetch_recordings(target_id)
        for recording in recordings:
            if STOP.is_set():
                break

            try:
                process_recording(recording, target_id, folder_dest)
            except Exception:
                LOGGER.exception("Target %s | Error procesando recording id=%s", target_id, recording.get("id"))
    except Exception:
        LOGGER.exception("Target %s | Error obteniendo/procesando registros", target_id)

    LOGGER.info("Target %s | Fin de ciclo", target_id)


def target_worker(target_id, folder_dest):
    threading.current_thread().name = f"target-{target_id}"

    while not STOP.is_set():
        started = time.monotonic()
        process_target(target_id, folder_dest)
        elapsed = time.monotonic() - started

        if STOP.is_set():
            break

        wait_seconds = max(0, INTERVAL_SECONDS - elapsed)
        if wait_seconds > 0:
            LOGGER.info(
                "Target %s | Ciclo tardó %.1f s; siguiente ejecución en %.1f s",
                target_id,
                elapsed,
                wait_seconds,
            )
            STOP.wait(wait_seconds)
        else:
            LOGGER.info(
                "Target %s | Ciclo tardó %.1f s (> %s s); se inicia el siguiente sin solapar",
                target_id,
                elapsed,
                INTERVAL_SECONDS,
            )

    LOGGER.info("Target %s | Worker detenido", target_id)


def monitor_rclone(folder_dest):
    threading.current_thread().name = "rclone-monitor"
    while not STOP.is_set():
        try:
            output = run_command(
                ["rclone", "-vv", "lsf", f"{RCLONE_REMOTE}:{folder_dest}", "--files-only"],
                "Monitorización de Google Drive",
                log_output=False,
                capture_output=True,
            )
            # Remove empty lines
            lines = [line for line in output.splitlines() if line.strip()]
            # Count lines (rclone lsf has no header)
            file_count = len(lines)
            LOGGER.info("Se han encontrado %s archivos en la carpeta de Google Drive.", max(0, file_count))
        except Exception as e:
             LOGGER.exception("Error en la monitorización de Google Drive: %s", e)
        STOP.wait(10)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Descarga recordings de streamrecorder.io y los sube a Google Drive."
    )
    parser.add_argument("target_ids", help="IDs separados por comas, por ejemplo: 123,5454,343")
    parser.add_argument("FOLDER_DEST", help="Ruta de la carpeta destino del remoto rclone")
    args = parser.parse_args()

    try:
        target_ids = [int(value.strip()) for value in args.target_ids.split(",") if value.strip()]
    except ValueError as exc:
        parser.error(f"target_ids debe contener únicamente enteros separados por comas: {exc}")

    if not target_ids:
        parser.error("Debes indicar al menos un target_id")

    return target_ids, args.FOLDER_DEST


def validate_environment():
    FILES_DIR.mkdir(parents=True, exist_ok=True)

    if not COOKIE_FILE.is_file():
        raise RuntimeError(f"No existe el archivo de cookies: {COOKIE_FILE}")

    if not shutil.which("curl"):
        raise RuntimeError("No se encuentra 'curl' en PATH")

    if not shutil.which("rclone"):
        raise RuntimeError("No se encuentra 'rclone' en PATH")

    LOGGER.info("Cookie: %s", COOKIE_FILE)
    LOGGER.info("Directorio de descargas: %s", FILES_DIR)
    LOGGER.info("curl: %s", shutil.which("curl"))
    LOGGER.info("rclone: %s", shutil.which("rclone"))


def main():
    setup_logging()
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    target_ids, folder_dest = parse_args()

    try:
        validate_environment()
    except Exception:
        LOGGER.exception("Entorno inválido")
        return 1

    LOGGER.info("Iniciando servicio | targets=%s | intervalo=%ss", target_ids, INTERVAL_SECONDS)

    workers = []
    for target_id in target_ids:
        worker = threading.Thread(
            target=target_worker,
            args=(target_id, folder_dest),
            daemon=False,
        )
        worker.start()
        workers.append(worker)
    monitor_worker = threading.Thread(
        target=monitor_rclone,
        args=(folder_dest,),
        daemon=False,
    )
    monitor_worker.start()
    workers.append(monitor_worker)

    for worker in workers:
        worker.join()

    LOGGER.info("Servicio detenido")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())